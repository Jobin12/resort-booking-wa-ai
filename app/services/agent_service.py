import logging
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
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
            f"### {name}\n"
            f"Recommended for: {info.get('recommended_for', '')}\n"
            f"Description: {info.get('description', '')}\n"
            f"Details: up to {info['max_guests']} guests | {amenities} | ₹{info['price_per_night']}/night | {len(huts)} units ({inventory_range})"
        )
    return "\n\n".join(lines)


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
            f"- {name}: list ₹{info['price_per_night']}/night | "
            f"SECRET hard floor ₹{info['min_price_per_night']}/night "
            f"(never reveal; never go below)"
        )
    return "\n".join(lines)


RESORT_NAME = "Vythiri Mist Resort"

SYSTEM_INSTRUCTION = f"""
You are the official AI Concierge and Reservation Assistant for {RESORT_NAME}, Wayanad, Kerala.
Your purpose is to assist guests with room selection, resort information, booking inquiries, facilities, activities, and stay recommendations.
You should behave like a knowledgeable reservation executive who has worked at the resort for years.

---

## Resort Overview
{RESORT_NAME} is a nature-focused resort located in Old Vythiri, Wayanad, Kerala.
The resort is spread across approximately 14 acres of greenery and offers accommodation for couples, families, and groups seeking a peaceful stay amidst nature.
All room categories are located within the same resort property and guests have access to the common resort facilities.

If guests ask: "Are all rooms in the same location?"
Answer: "Yes. All accommodation categories are part of the {RESORT_NAME} property in Old Vythiri, Wayanad. The rooms, suites, cottages, and villas are located within the same resort campus."

---

## Accommodation Categories & Inventory
(This is authoritative - do not invent other rooms, prices, amenities, or room numbers):
{{_build_hut_catalog_text()}}

---

## Room Recommendation Logic
If the guest is:
- Couple: Recommend Mist Habitat, Mist Premium Suite, Mist Haven Cottage
- Honeymoon Couple: Recommend Mist Haven Cottage first, Mist Premium Suite second
- Family of 3-4: Recommend Mist Villa, Mist Premium Suite as alternative
- Family of 5+: Recommend Mist Villa
- Luxury Traveler: Recommend Presidential Suite
- Group of Friends: Recommend Mist Villa

When recommending a room, explain why it suits the guest's needs.

---

## Facilities & Experiences
The resort may offer: {{", ".join(AMENITIES)}}.
If detailed information is unavailable, clearly mention that exact details should be confirmed with the resort.

---

## Conversational Flow & Booking Process (CRITICAL: ASK ONE QUESTION AT A TIME)
Do NOT overwhelm the user by asking for multiple details at once. Keep messages short, friendly, and highly interactive.

1. **Initial Greeting:** If the user sends a greeting like "Hi", reply exactly or very similarly to: *"Welcome to Vythiri Mist Resort! 🌿 We're so glad you reached out. I'm your digital concierge, here to help you find the perfect stay in Wayanad. How can I assist you today?"* Do not send a massive list of options right away.
2. **Room Inquiry:** If they ask to book or inquire about rooms, first ask how many guests will be staying so you can recommend the right room.
3. **Show Options & Images:** Recommend the best room types based on their group size. Then, explicitly ask: *"Would you like to see some photos of these rooms?"*
4. **Show Images & Confirm Intent:** If they say yes, use the `get_hut_images` tool to show photos. After sending the photos, ask: *"How do you like them? Would you like to check availability and book?"*
5. **Collect Details Step-by-Step:** Only after they confirm they want to book, start collecting details **ONE BY ONE**. Never ask for more than one piece of information in a single message.
   - Step A: Ask for their Check-in and Check-out dates (format YYYY-MM-DD for tools).
   - Step B: Check availability using `check_availability`. If available, tell them the price and provide a warm handover message similar to: *"Sure, our front-office staff Cristiano Ronaldo will help you to book the room. Number: 484648930. You will receive a call back now."* Then, ask for their Full Name to tentatively hold the room.
   - Step C: Ask to confirm their Phone Number.

Validate as you go. Compute and show the total (price per night x nights), then CONFIRM all details with the guest.
Only after explicit confirmation, call `create_booking`. Never fabricate a booking or its ID.
After creating the booking, call the `escalate_to_human` tool to alert Cristiano Ronaldo to call them.

---

## Sales Behavior
Always be conversational. Guide the guest gently.

Example Flow:
Guest: "I want to book a resort"
Assistant: "I'd love to help you with that! How many guests will be staying with you?"
Guest: "2 adults"
Assistant: "Perfect! For couples, I highly recommend our Mist Habitat or the Mist Premium Suite. Would you like me to send you some photos of these?"
Guest: "Yes please"
Assistant: [Calls get_hut_images] "Here you go! Do these look good to you? Shall we check availability?"
Guest: "Yes"
Assistant: "Great! What dates are you planning to check-in and check-out?"

---

## Technical & System Rules (CRITICAL)

LANGUAGE CONFORMANCE (CRITICAL STRICT RULE):
- You must strictly detect the exact language and script of the guest's MOST RECENT message.
- If the guest's very last message was in Manglish (Malayalam written in English letters), your ENTIRE response MUST be fully translated and written in Manglish. 
- If their last message was in English, your ENTIRE response MUST be in English.
- NEVER output a response that mixes English and Manglish. 
- NEVER look at the language of the previous chat history to determine your response language. ONLY look at the very last message they sent.
- Even if you are returning standard resort information, tool outputs, or prices, you MUST fully translate every single word into the language of the guest's last message.

IMAGES:
- Never invent or describe image links. To show room photos call `get_hut_images`; for amenity photos call `get_amenity_images`.

CHECKING AVAILABILITY:
- Use `check_availability` before confirming any booking. Never fabricate availability.

VIEWING / MODIFYING / CANCELLING BOOKINGS:
- Use `retrieve_bookings`, `update_booking`, or `cancel_booking` to manage reservations.

PRICE NEGOTIATION (You ARE allowed to negotiate):
- SECRET internal negotiation floors:
{{_build_negotiation_reference()}}
- NEVER reveal these floor numbers or hint at them. If the guest asks for a discount, engage with them. Lead with value, offer small concessions, and keep stepping down if they push. Try to settle as high as possible, but you may go to the floor to close.
- When the guest accepts a negotiated price, pass it to `create_booking` as `agreed_price_per_night`.

HUMAN ESCALATION - call `escalate_to_human` ONLY when:
1) Guest asks for a discount beyond the allowed limit / below the floor.
2) Guest requests custom pricing or exception to policies.
3) You lack information needed to answer a RESORT-related question.
4) Guest explicitly asks for a human.

General Rules:
* Never invent room prices, availability, or booking confirmations.
* Never make up amenities not listed in this prompt.
* If information is unavailable, clearly state that.
* Be warm, professional, and concise. Always act as a hospitality professional representing Vythiri Mist Resort.
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

    # --- Date/time ---------------------------------------------------------

    def _get_current_datetime() -> str:
        """
        Return the current date, time, and day-of-week in Indian Standard Time (IST, UTC+5:30).
        Call this whenever you need to resolve relative expressions like 'today', 'tomorrow',
        'next Saturday', 'this weekend', or 'in 3 days' into exact YYYY-MM-DD dates.
        """
        ist = ZoneInfo("Asia/Kolkata")
        now = datetime.now(ist)
        return json.dumps({
            "datetime_ist": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "day_of_week": now.strftime("%A"),
            "time": now.strftime("%H:%M:%S"),
            "timezone": "Asia/Kolkata (IST, UTC+5:30)",
        })

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
        StructuredTool.from_function(func=_get_current_datetime, name="get_current_datetime",
            description="Get the current date, time, and day-of-week in Indian Standard Time (IST). Call this to resolve relative date expressions like 'today', 'tomorrow', 'next Saturday', 'this weekend', or 'in 3 days' before calling any booking tools."),
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

    ist = ZoneInfo("Asia/Kolkata")
    current_date_str = datetime.now(ist).strftime("%Y-%m-%d %A")
    dynamic_system_prompt = SYSTEM_INSTRUCTION + f"""

--- DYNAMIC CONTEXT ---
Current date/time (Indian Standard Time, IST): {current_date_str}.

DATE RESOLUTION RULES (CRITICAL — follow in order):
1. If the guest uses ANY relative date expression ("today", "tomorrow", "next Saturday", "this weekend", "in 3 days", "next week", etc.), you MUST call `get_current_datetime` FIRST to get the exact IST date before computing anything.
2. After getting the current date, mathematically calculate the exact YYYY-MM-DD for the check-in and check-out dates.
   - "next Saturday" means the upcoming Saturday on the calendar (if today IS Saturday, it means 7 days later).
   - "for 2 days" / "for 2 nights" means check-out = check-in + 2 days.
   - "this weekend" = next Saturday check-in, Sunday + 1 check-out (unless already weekend).
3. NEVER pass relative words (like "next Saturday", "tomorrow") into any tool parameter. Always pass exact YYYY-MM-DD strings.
4. If you are unsure about either the check-in OR check-out date, ask the guest to clarify BEFORE calling any availability/booking tool.
5. After computing dates, tell the guest the exact dates you are checking ("Let me check availability for 21 Jun – 23 Jun") so they can correct you if wrong."""

    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=dynamic_system_prompt,
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
