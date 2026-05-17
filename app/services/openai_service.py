import logging
import json
import time
from flask import current_app
from langchain_core.tools import tool, StructuredTool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from app.utils.db_utils import (
    search_jewellery_in_db,
    get_jewellery_details_db,
    get_jewellery_images_db,
)

# LangGraph in-memory checkpointer to automatically manage chat history per thread_id
memory = MemorySaver()


@tool
def search_jewellery(
    category: str = None,
    metal_type: str = None,
    karat_purity: int = None,
    stone_type: str = None,
    gender: str = None,
    max_price: float = None,
    max_weight_grams: float = None,
) -> str:
    """
    Search for jewellery items in the Vajra Diamonds catalog.
    Args:
        category: ring, bangle, necklace, earrings, chain, bracelet, pendant, anklet, set, other.
        metal_type: gold, platinum, silver, diamond, rose_gold, white_gold, other.
        karat_purity: Gold purity in karats (integer 1-24, e.g. 18, 22, 24).
        stone_type: Free-text stone name (e.g. diamond, ruby, emerald, sapphire).
        gender: men, women, unisex, children, other.
        max_price: Maximum budget in INR.
        max_weight_grams: Maximum weight of the item in grams.
    """
    filters = {
        "category": category,
        "metal_type": metal_type,
        "karat_purity": karat_purity,
        "stone_type": stone_type,
        "gender": gender,
        "max_price": max_price,
        "max_weight_grams": max_weight_grams,
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    results = search_jewellery_in_db(filters)
    if not results:
        return "No jewellery items found matching the criteria."
    return json.dumps(results)


@tool
def get_jewellery_details(item_id: str) -> str:
    """
    Get full details of a specific jewellery item by its ID.
    """
    result = get_jewellery_details_db(item_id)
    if not result:
        return f"Could not find details for jewellery ID {item_id}"
    return json.dumps(result, default=str)


SYSTEM_INSTRUCTION = """
You are a professional and knowledgeable Jewellery Sales Assistant for Vajra Diamonds, a premium jewellery shop, speaking with customers on WhatsApp.
Greet new customers warmly with a brief welcome to Vajra Diamonds, then help them discover rings, necklaces, bangles, earrings, chains, bracelets, pendants, anklets, and bridal sets.

TONE & STYLE:
- Maintain a professional, polite, and respectful tone at all times. Avoid overly casual language.
- Be concise and efficient in your communication.

LANGUAGE SUPPORT:
- Strictly respond ONLY in the same language the user uses.
- If the user writes in English, reply ONLY in English.
- If the user writes in Manglish (Malayalam written in English script), reply ONLY in Manglish.
- DO NOT provide English translations or any other language versions in brackets or otherwise. Respond in one language only.

DATABASE SCHEMA HINTS:
- When querying using `search_jewellery`, strictly map the user's requirements to these database ENUMs:
  - `category`: ring, bangle, necklace, earrings, chain, bracelet, pendant, anklet, set, other
  - `metal_type`: gold, platinum, silver, diamond, rose_gold, white_gold, other
  - `gender`: men, women, unisex, children, other
- `karat_purity` is an integer between 1 and 24 (typical values: 18, 22, 24).
- `stone_type` is free-text (e.g. diamond, ruby, emerald, sapphire, pearl).
- If the user mentions a "budget" or "budget is X lakhs", map this to the `max_price` parameter. (1 lakh = 100,000. So 2 lakhs = 200000).
- IMPORTANT: Only pass filters the user has EXPLICITLY mentioned. Never invent a `max_price`, `karat_purity`, or any other filter the user did not state. If the user asks for "cheapest gold rings", only set `category=ring` and `metal_type=gold` — do NOT add a `max_price`.
- Prices are in Indian Rupees (INR). Format prices with the ₹ symbol when presenting them.

Once you have enough information, use the `search_jewellery` tool to find matching items.
Present the found items to the user in a concise, professional format. Mention category, metal, karat (if any), stone (if any), weight, and price.
If a user wants to know more about a specific item, use the `get_jewellery_details` tool.
If a user asks to see pictures of an item, use the `send_jewellery_images` tool. The backend will automatically send the images to the user on your behalf, so you just need to say something like "Here are the pictures you requested!" after using the tool.
Avoid using markdown that WhatsApp doesn't support (WhatsApp supports *bold*, _italic_, ~strikethrough~).
"""


# Global variables for rate limit cooldown to prevent spamming the API when quota is hit
LAST_RATE_LIMIT_TIME = 0
COOLDOWN_PERIOD = 20  # seconds


def handle_openai_conversation(wa_id, name, user_message, send_message_callback):
    """
    Handles the conversation using LangChain 1.2+ Agent API with OpenAI.
    """
    global LAST_RATE_LIMIT_TIME

    # Check if we are in a cooldown period
    current_time = time.time()
    if current_time - LAST_RATE_LIMIT_TIME < COOLDOWN_PERIOD:
        wait_time = int(COOLDOWN_PERIOD - (current_time - LAST_RATE_LIMIT_TIME))
        return f"I'm currently taking a short break due to high demand. Please try again in about {wait_time} seconds! 🙏"

    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        logging.error("OPENAI_API_KEY is not set.")
        return "Sorry, the AI service is currently unavailable."

    # Define send_jewellery_images dynamically so it has access to wa_id and send_message_callback
    def _send_jewellery_images(item_id: str) -> str:
        """
        Fetch image URLs for a specific jewellery item ID and signal that images should be sent.
        """
        images = get_jewellery_images_db(item_id)
        if not images:
            return json.dumps({"status": "no_images_found"})

        from app.utils.whatsapp_utils import get_image_message_input
        from app.utils.aws_utils import generate_presigned_url

        sent_urls = []
        for url in images:
            presigned_url = generate_presigned_url(url)
            image_payload = get_image_message_input(wa_id, presigned_url)
            send_message_callback(image_payload)
            sent_urls.append(presigned_url)

        return json.dumps({"status": "images_found", "count": len(sent_urls)})

    send_jewellery_images = StructuredTool.from_function(
        func=_send_jewellery_images,
        name="send_jewellery_images",
        description="Fetch image URLs for a specific jewellery item ID and signal that images should be sent.",
    )

    llm = ChatOpenAI(model="gpt-4.1-nano", api_key=api_key, max_retries=1)
    tools = [search_jewellery, get_jewellery_details, send_jewellery_images]

    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_INSTRUCTION,
        checkpointer=memory,
    )

    logging.info(f"Processing message for {name} ({wa_id}) via OpenAI agent...")

    try:
        inputs = {"messages": [{"role": "user", "content": user_message}]}
        # We use the wa_id as the thread_id so the checkpointer remembers the conversation per user
        config = {"configurable": {"thread_id": wa_id}}

        result = graph.invoke(inputs, config=config)

        final_message = result["messages"][-1]
        final_content = final_message.content

        # Normalize content to a plain string for the WhatsApp API
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

        logging.error(f"Error communicating with LangChain: {e}")
        return "I'm having some trouble processing your request right now. Please try again later."
