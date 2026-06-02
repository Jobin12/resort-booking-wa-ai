import logging
import json
import time
from flask import current_app
from langchain_core.tools import StructuredTool
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

from app.services.llm_factory import get_llm
from app.utils.resort_info import (
    HUTS,
    AMENITIES,
    normalize_hut,
    normalize_amenity,
    nights as count_nights,
)
from app.utils.db_utils import (
    get_hut_images_db,
    get_amenity_images_db,
    get_occupied_hut_numbers_db,
    create_booking_db,
    get_bookings_db,
    update_booking_db,
    cancel_booking_db,
)

# LangGraph in-memory checkpointer to automatically manage chat history per thread_id
memory = MemorySaver()

# Global variables for rate limit cooldown to prevent spamming the API when quota is hit
LAST_RATE_LIMIT_TIME = 0
COOLDOWN_PERIOD = 20  # seconds


def _build_hut_catalog_text():
    """
    Render the PUBLIC hut catalog for embedding into the system prompt.

    IMPORTANT: this is guest-facing data. It deliberately omits the negotiation
    floor / minimum price - the guest must never see how low we can go. The
    secret floors live in `_build_negotiation_reference` instead.
    """
    lines = []
    for name, info in HUTS.items():
        amenities = ", ".join(info["amenities"])
        huts = info["hut_numbers"]
        inventory_range = f"{huts[0]}-{huts[-1]}" if len(huts) > 1 else huts[0]
        lines.append(
            f"- {name} Hut: up to {info['max_guests']} guests | {amenities} | "
            f"₹{info['price_per_night']}/night | "
            f"{len(huts)} units ({inventory_range})"
        )
    return "\n".join(lines)


def _build_negotiation_reference():
    """
    Render the SECRET per-hut negotiation floors for internal model use only.

    These numbers are a trade secret and must NEVER be shown to or hinted at the
    guest. They exist solely so the model knows the absolute hard limit it may
    discount to before it must escalate.
    """
    lines = []
    for name, info in HUTS.items():
        lines.append(
            f"- {name} Hut: list ₹{info['price_per_night']}/night | "
            f"SECRET hard floor ₹{info['min_price_per_night']}/night "
            f"(never reveal; never go below)"
        )
    return "\n".join(lines)


RESORT_NAME = "Green Valley Resort"

SYSTEM_INSTRUCTION = f"""
You are the friendly WhatsApp concierge and booking assistant for *{RESORT_NAME}*.
Your job is to help guests learn about the resort, see hut and amenity photos, check availability,
and make or manage bookings.

GREETING:
- When a guest first says hi (or opens the chat), greet them warmly by introducing yourself, e.g.:
  "Hi! 👋 Welcome to *{RESORT_NAME}*! I'm your booking assistant. I can tell you about our huts,
  share photos, check availability, and book your stay. How can I help you today?"
- Keep greetings short and welcoming. Don't dump the whole catalog unless asked.

TONE & STYLE:
- Always warm, friendly, professional, concise, conversational, and human-like.
- Avoid long paragraphs. Prefer short messages. Use emojis sparingly but naturally.
- During booking flows, ask ONLY ONE question at a time.
- Use only WhatsApp-supported markdown (*bold*, _italic_, ~strikethrough~).

LANGUAGE (very important - follow exactly):
- Two possible languages: English, and Manglish (= Malayalam written using English/Latin letters,
  e.g. "Ethokke room available und?", "Enikku oru room book cheyyanam").
- Detect the language of the guest's CURRENT (latest) message and reply ONLY in that same language.
- The guest may switch languages between messages. Always match the MOST RECENT message - if their
  last message was English, reply in English; if it was Manglish, reply in Manglish. Do not be
  influenced by what language earlier messages used.
- NEVER mix the two in one reply, and NEVER add a translation in brackets or parentheses. One
  language only, the one the guest just used.
- When replying in Manglish, keep it natural Manglish (Malayalam in Latin script) - do not write in
  the Malayalam script and do not slip into pure English.

ANSWER FIRST, COLLECT DETAILS LATER (very important):
- Do NOT ask the guest for their name, phone, dates, or any personal detail just because they
  messaged you. First simply ANSWER what they asked.
- General/info questions - answer them directly with NO personal questions:
  e.g. "what huts/rooms are available?", "what are the categories?", "what facilities/amenities do
  you have?", "show me photos", "what's the price?", "is there a pool?". Just give the information.
- Only START collecting personal details once the guest clearly expresses intent to BOOK
  (e.g. "I want to book", "book the deluxe for me", "reserve a hut"). Until then, never ask for
  their name or other details.

RESORT HUT CATALOG & INVENTORY (this is authoritative - do not invent other huts, prices,
amenities, or hut numbers):
{_build_hut_catalog_text()}

RESORT AMENITIES: {", ".join(AMENITIES)}.

Each booking is assigned ONE specific hut number from its category's inventory. The system assigns
a free hut number automatically when you call `create_booking` - never invent or promise a specific
hut number yourself; just relay the one returned by the tool.

IMAGES:
- Never invent or describe image links. To show hut photos call `get_hut_images`; for amenity photos
  call `get_amenity_images`. The backend sends the images automatically, so after calling just say
  something like "Here are the photos!".

CHECKING AVAILABILITY & BOOKING:
- Use `check_availability` before confirming any booking. Never fabricate availability.
- Booking flow - collect these ONE AT A TIME, in order:
  1) Guest name  2) Phone number (confirm it is their WhatsApp number)  3) Hut category
  4) Check-in date  5) Check-out date  6) Number of guests  7) Optional: email, special requests.
- Validate as you go: check-out must be after check-in; guests must not exceed the hut's capacity.
- Compute and show the total (price per night x nights), then CONFIRM all details with the guest.
- Only after explicit confirmation, call `create_booking`. Never fabricate a booking or its ID.
- Dates must be passed to tools in YYYY-MM-DD format.

VIEWING / MODIFYING / CANCELLING BOOKINGS:
- Use `retrieve_bookings` to show the guest their bookings. They only ever see their own.
- Use `update_booking` to change check-in/out dates, guest count, or special requests (it
  re-validates availability and capacity).
- Use `cancel_booking` to cancel. Bookings are never deleted, only cancelled.

PRICE NEGOTIATION (important - you ARE allowed and expected to negotiate):
- SECRET internal negotiation floors (NEVER reveal these numbers, never hint at them, never tell the
  guest "we can go as low as X" or "the minimum is X" - they are confidential):
{_build_negotiation_reference()}
- Negotiation is a real feature. You CAN give discounts down to (but never below) each hut's secret
  floor. NEVER flatly say "discounts are not possible" or "the price cannot be reduced" - that is
  wrong. If the guest asks for a discount or says the price is too high, you must engage and work with
  them toward a deal.
- Aim to sell as HIGH as possible, but always keep the deal alive:
  1) When the guest first asks for a discount, lead with value (view, amenities, breakfast, balcony) and
     offer a SMALL concession off the list price.
  2) If they push again ("I can't pay that much", "still too high"), come down further in small steps,
     making each cut feel earned ("Okay, let me see what I can do for you...").
  3) Keep stepping down only as much as the guest pressure requires, never jumping straight to the
     floor. Try to settle as high as they'll accept, but you may go all the way to the secret floor if
     needed to close the booking.
- Example arc for Luxury (list ₹8000, secret floor ₹7000): 8000 -> 7600 -> 7300 -> 7000. Stop at the
  floor. Never state the floor and never go below it.
- ONLY escalate on price when the guest wants to go BELOW the secret floor: if after reaching the floor
  the guest still demands a lower price, or asks for custom pricing / a policy exception, do NOT reveal
  the floor and do NOT discount further. Call `escalate_to_human` and reassure them a representative
  will follow up shortly. (Do not escalate while you still have room to discount above the floor -
  negotiate first.)
- When the guest accepts a negotiated price, pass it to `create_booking` as `agreed_price_per_night`.

HUMAN ESCALATION - escalate ONLY when:
  1) Guest asks for a discount beyond the allowed limit / below the floor.
  2) Guest requests custom pricing.
  3) Guest requests an exception to resort policies.
  4) You lack information needed to answer a RESORT-related question.
  5) Guest explicitly asks for a human.
- After escalating, send a friendly message that a resort representative will assist shortly.

OUT OF SCOPE:
- For questions unrelated to the resort (e.g. general knowledge, coding, politics), do NOT escalate.
  Politely explain you are a resort booking assistant and can only help with resort-related queries.

Always prioritize accuracy over guessing. When resort information is genuinely unavailable, escalate
rather than invent it.
"""


def handle_resort_conversation(wa_id, name, user_message, send_message_callback):
    """
    Handle a WhatsApp conversation turn using the LangChain agent. Tools are defined
    as inner closures so they can capture wa_id (for ownership-scoped bookings and
    image delivery) and the send_message_callback (to push images / operator alerts).
    """
    global LAST_RATE_LIMIT_TIME

    # Cooldown after a rate-limit hit, to avoid hammering the provider.
    current_time = time.time()
    if current_time - LAST_RATE_LIMIT_TIME < COOLDOWN_PERIOD:
        wait_time = int(COOLDOWN_PERIOD - (current_time - LAST_RATE_LIMIT_TIME))
        return f"I'm currently taking a short break due to high demand. Please try again in about {wait_time} seconds! 🙏"

    try:
        llm = get_llm()
    except Exception as e:
        logging.error(f"Could not initialize LLM: {e}")
        return "Sorry, the assistant is currently unavailable. Please try again later."

    # Lazy imports to mirror the reference structure and avoid circular imports.
    from app.utils.whatsapp_utils import get_image_message_input
    from app.utils.aws_utils import generate_presigned_url

    # --- Image tools -------------------------------------------------------

    def _get_hut_images(hut_category: str) -> str:
        """Send photos of a hut category to the guest. Valid: Economy, Deluxe, Luxury."""
        canonical = normalize_hut(hut_category)
        if not canonical:
            return json.dumps({"status": "invalid_hut", "valid": list(HUTS.keys())})
        urls = get_hut_images_db(canonical)
        if not urls:
            return json.dumps({"status": "no_images_found", "hut": canonical})
        for url in urls:
            send_message_callback(get_image_message_input(wa_id, generate_presigned_url(url)))
        return json.dumps({"status": "images_sent", "hut": canonical, "count": len(urls)})

    def _get_amenity_images(amenity_name: str) -> str:
        """Send photos of a resort amenity to the guest (e.g. Swimming Pool, Restaurant)."""
        canonical = normalize_amenity(amenity_name)
        if not canonical:
            return json.dumps({"status": "invalid_amenity", "valid": AMENITIES})
        urls = get_amenity_images_db(canonical)
        if not urls:
            return json.dumps({"status": "no_images_found", "amenity": canonical})
        for url in urls:
            send_message_callback(get_image_message_input(wa_id, generate_presigned_url(url)))
        return json.dumps({"status": "images_sent", "amenity": canonical, "count": len(urls)})

    # --- Availability ------------------------------------------------------

    def _free_hut_numbers(canonical, check_in_date, check_out_date, exclude_booking_id=None):
        """
        Shared helper: returns (free_hut_numbers_list, error_str). The list is the
        category's hut numbers minus those occupied over the date range. On any
        validation/DB issue, list is None and error_str is set.
        """
        try:
            n = count_nights(check_in_date, check_out_date)
        except Exception:
            return None, "invalid_date_format"
        if n <= 0:
            return None, "check_out_must_be_after_check_in"
        occupied = get_occupied_hut_numbers_db(
            canonical, check_in_date, check_out_date, exclude_booking_id=exclude_booking_id
        )
        if occupied is None:
            return None, "availability_lookup_failed"
        free = [h for h in HUTS[canonical]["hut_numbers"] if h not in occupied]
        return free, None

    def _check_availability(hut_category: str, check_in_date: str, check_out_date: str) -> str:
        """
        Check how many units of a hut category are free for a date range.
        Dates must be YYYY-MM-DD. Returns available count and price per night.
        """
        canonical = normalize_hut(hut_category)
        if not canonical:
            return json.dumps({"status": "invalid_hut", "valid": list(HUTS.keys())})
        free, err = _free_hut_numbers(canonical, check_in_date, check_out_date)
        if err:
            return json.dumps({"status": err})
        info = HUTS[canonical]
        return json.dumps({
            "status": "ok",
            "hut": canonical,
            "available_units": len(free),
            "is_available": len(free) > 0,
            "price_per_night": info["price_per_night"],
            "max_guests": info["max_guests"],
            "nights": count_nights(check_in_date, check_out_date),
        })

    # --- Bookings ----------------------------------------------------------

    def _create_booking(
        guest_name: str,
        hut_category: str,
        check_in_date: str,
        check_out_date: str,
        number_of_guests: int,
        guest_email: str = None,
        special_requests: str = None,
        agreed_price_per_night: float = None,
    ) -> str:
        """
        Create a confirmed booking AFTER the guest has confirmed all details.
        Dates must be YYYY-MM-DD. guest_phone is taken from the WhatsApp sender for security.
        agreed_price_per_night may be set if a discount was negotiated (must be >= the hut floor).
        """
        canonical = normalize_hut(hut_category)
        if not canonical:
            return json.dumps({"status": "invalid_hut", "valid": list(HUTS.keys())})
        info = HUTS[canonical]

        try:
            number_of_guests = int(number_of_guests)
        except (TypeError, ValueError):
            return json.dumps({"status": "invalid_guest_count"})
        if number_of_guests < 1 or number_of_guests > info["max_guests"]:
            return json.dumps({"status": "exceeds_capacity", "max_guests": info["max_guests"]})

        free, err = _free_hut_numbers(canonical, check_in_date, check_out_date)
        if err:
            return json.dumps({"status": err})
        if not free:
            return json.dumps({"status": "no_availability"})
        assigned_hut_number = free[0]

        # Determine price per night (apply negotiated price only within allowed limits).
        price_per_night = info["price_per_night"]
        if agreed_price_per_night is not None:
            agreed = float(agreed_price_per_night)
            if agreed < info["min_price_per_night"]:
                return json.dumps({
                    "status": "price_below_floor",
                    "floor": info["min_price_per_night"],
                })
            price_per_night = min(agreed, info["price_per_night"])

        n = count_nights(check_in_date, check_out_date)
        total_amount = price_per_night * n

        row = create_booking_db(
            guest_name=guest_name,
            guest_phone=wa_id,
            hut_category=canonical,
            hut_number=assigned_hut_number,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            number_of_guests=number_of_guests,
            total_amount=total_amount,
            guest_email=guest_email,
            special_requests=special_requests,
        )
        if not row:
            return json.dumps({"status": "booking_failed"})
        return json.dumps({
            "status": "booked",
            "booking": row,
            "price_per_night": price_per_night,
            "nights": n,
        })

    def _retrieve_bookings(booking_id: str = None) -> str:
        """Retrieve the guest's own bookings (optionally a specific booking_id)."""
        rows = get_bookings_db(wa_id, booking_id=booking_id)
        if not rows:
            return json.dumps({"status": "no_bookings_found"})
        return json.dumps({"status": "ok", "bookings": rows})

    def _update_booking(
        booking_id: str,
        check_in_date: str = None,
        check_out_date: str = None,
        number_of_guests: int = None,
        special_requests: str = None,
    ) -> str:
        """
        Modify an existing booking owned by the guest. Re-validates capacity/availability
        when dates or guest count change. Dates must be YYYY-MM-DD.
        """
        existing = get_bookings_db(wa_id, booking_id=booking_id)
        if not existing:
            return json.dumps({"status": "booking_not_found"})
        booking = existing[0]
        canonical = normalize_hut(booking["hut_category"]) or booking["hut_category"]
        info = HUTS.get(canonical, {})

        # Resolve the effective values after the requested change.
        new_in = check_in_date or booking["check_in_date"]
        new_out = check_out_date or booking["check_out_date"]

        fields = {}
        if check_in_date:
            fields["check_in_date"] = check_in_date
        if check_out_date:
            fields["check_out_date"] = check_out_date
        if special_requests is not None:
            fields["special_requests"] = special_requests
        if number_of_guests is not None:
            try:
                number_of_guests = int(number_of_guests)
            except (TypeError, ValueError):
                return json.dumps({"status": "invalid_guest_count"})
            if info and (number_of_guests < 1 or number_of_guests > info["max_guests"]):
                return json.dumps({"status": "exceeds_capacity", "max_guests": info["max_guests"]})
            fields["number_of_guests"] = number_of_guests

        if not fields:
            return json.dumps({"status": "nothing_to_update"})

        # Re-validate availability if dates changed. Keep the assigned hut number if it's
        # still free for the new range; otherwise reassign to another free hut number.
        if check_in_date or check_out_date:
            try:
                if count_nights(new_in, new_out) <= 0:
                    return json.dumps({"status": "check_out_must_be_after_check_in"})
            except Exception:
                return json.dumps({"status": "invalid_date_format"})
            free, err = _free_hut_numbers(canonical, new_in, new_out, exclude_booking_id=booking_id)
            if err:
                return json.dumps({"status": err})
            current_hut = booking.get("hut_number")
            if current_hut and current_hut in free:
                pass  # keep the same hut number
            elif free:
                fields["hut_number"] = free[0]  # reassign to a free one
            else:
                return json.dumps({"status": "no_availability"})

        row = update_booking_db(booking_id, wa_id, fields)
        if not row:
            return json.dumps({"status": "update_failed"})
        return json.dumps({"status": "updated", "booking": row})

    def _cancel_booking(booking_id: str) -> str:
        """Cancel a booking owned by the guest (sets status to CANCELLED, never deletes)."""
        row = cancel_booking_db(booking_id, wa_id)
        if not row:
            return json.dumps({"status": "booking_not_found"})
        return json.dumps({"status": "cancelled", "booking": row})

    # --- Human escalation --------------------------------------------------

    def _escalate_to_human(
        intent: str,
        conversation_summary: str,
        escalation_reason: str,
        requested_discount: str = None,
    ) -> str:
        """
        Notify the resort operator that a conversation needs a human. Provide the guest's
        intent, a short conversation summary, the escalation reason, and any requested
        discount / booking details collected so far.
        """
        operator = current_app.config.get("OPERATOR_WAID")
        summary_lines = [
            "🛎️ *Resort Escalation*",
            f"Customer: {name or 'Unknown'}",
            f"Phone: {wa_id}",
            "",
            f"Intent: {intent}",
            "",
            f"Summary: {conversation_summary}",
            "",
            f"Reason: {escalation_reason}",
        ]
        if requested_discount:
            summary_lines += ["", f"Requested Discount: {requested_discount}"]
        summary = "\n".join(summary_lines)

        if not operator:
            logging.error("OPERATOR_WAID not configured; cannot deliver escalation. Summary:\n" + summary)
            return json.dumps({"status": "operator_not_configured"})

        # NOTE: proactively messaging the operator assumes an open 24h WhatsApp session.
        # In production a message template would be required outside that window.
        from app.utils.whatsapp_utils import get_text_message_input
        send_message_callback(get_text_message_input(operator, summary))
        logging.info(f"Escalation sent to operator {operator} for guest {wa_id}")
        return json.dumps({"status": "escalated"})

    # --- Register tools ----------------------------------------------------

    tools = [
        StructuredTool.from_function(func=_get_hut_images, name="get_hut_images",
            description="Send photos of a hut category (Economy, Deluxe, Luxury) to the guest."),
        StructuredTool.from_function(func=_get_amenity_images, name="get_amenity_images",
            description="Send photos of a resort amenity (Swimming Pool, Play Area, Restaurant, Camp Fire, Kids Zone) to the guest."),
        StructuredTool.from_function(func=_check_availability, name="check_availability",
            description="Check available units of a hut category for a date range (dates YYYY-MM-DD)."),
        StructuredTool.from_function(func=_create_booking, name="create_booking",
            description="Create a confirmed booking after the guest confirms all details (dates YYYY-MM-DD)."),
        StructuredTool.from_function(func=_retrieve_bookings, name="retrieve_bookings",
            description="Retrieve the guest's own bookings, optionally by booking_id."),
        StructuredTool.from_function(func=_update_booking, name="update_booking",
            description="Modify an existing booking's dates, guest count, or special requests."),
        StructuredTool.from_function(func=_cancel_booking, name="cancel_booking",
            description="Cancel one of the guest's bookings (sets status to CANCELLED)."),
        StructuredTool.from_function(func=_escalate_to_human, name="escalate_to_human",
            description="Notify the resort operator that a human needs to take over."),
    ]

    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_INSTRUCTION,
        checkpointer=memory,
    )

    logging.info(f"Processing message for {name} ({wa_id}) via resort agent...")

    try:
        inputs = {"messages": [{"role": "user", "content": user_message}]}
        # wa_id as thread_id so the checkpointer remembers the conversation per guest.
        config = {"configurable": {"thread_id": wa_id}}

        result = graph.invoke(inputs, config=config)

        final_message = result["messages"][-1]
        final_content = final_message.content

        # Normalize content to a plain string for the WhatsApp API.
        if isinstance(final_content, list):
            text_parts = [block.get("text", "") for block in final_content if isinstance(block, dict) and "text" in block]
            final_content = " ".join(text_parts) if text_parts else str(final_content)
        elif not isinstance(final_content, str):
            final_content = str(final_content)

        if not final_content.strip():
            final_content = "I processed your request!"

        return final_content

    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "rate_limit" in error_str or "quota" in error_str or "insufficient_quota" in error_str:
            LAST_RATE_LIMIT_TIME = time.time()
            return "We've hit the AI's speed limit! I'm going to wait a moment before accepting more requests. Please try again shortly."

        logging.error(f"Error communicating with the LLM agent: {e}")
        return "I'm having some trouble processing your request right now. Please try again later."
