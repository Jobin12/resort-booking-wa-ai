import logging
import json
import os
from flask import current_app
from langchain_core.tools import tool, StructuredTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from app.utils.db_utils import search_cars_in_db, get_car_details_db, get_car_images_db

# LangGraph in-memory checkpointer to automatically manage chat history per thread_id
memory = MemorySaver()

@tool
def search_cars(
    manufacturer: str = None,
    model_name: str = None,
    vehicle_type: str = None,
    fuel_type: str = None,
    transmission: str = None,
    max_price: float = None,
    min_year: int = None,
    max_kms: int = None
) -> str:
    """
    Search for cars in the database based on criteria.
    Args:
        manufacturer: The brand of the car (e.g. Honda, Toyota).
        model_name: The specific model.
        vehicle_type: sedan, hatchback, suv, compact_suv, muv.
        fuel_type: petrol, diesel, electric.
        transmission: manual, automatic.
        max_price: Maximum budget.
        min_year: Minimum year of manufacture.
        max_kms: Maximum kilometers driven.
    """
    filters = {
        "manufacturer": manufacturer,
        "model_name": model_name,
        "vehicle_type": vehicle_type,
        "fuel_type": fuel_type,
        "transmission": transmission,
        "max_price": max_price,
        "min_year": min_year,
        "max_kms": max_kms
    }
    # remove None values
    filters = {k: v for k, v in filters.items() if v is not None}
    results = search_cars_in_db(filters)
    if not results:
        return "No cars found matching the criteria."
    return json.dumps(results)

@tool
def get_car_details(car_id: str) -> str:
    """
    Get full details of a specific car by its ID.
    """
    result = get_car_details_db(car_id)
    if not result:
        return f"Could not find details for car ID {car_id}"
    return json.dumps(result)

SYSTEM_INSTRUCTION = """
You are a helpful and knowledgeable Car Sales Assistant on WhatsApp.
Your job is to help users find cars to buy.
Ask clarifying questions to understand their needs (budget, preferred vehicle type like SUV or sedan, fuel type, transmission, etc).
Once you have enough information, use the `search_cars` tool to find matching vehicles.
Present the found cars to the user in a concise, friendly format.
If a user wants to know more about a specific car, use the `get_car_details` tool.
If a user asks to see pictures of a car, use the `send_car_images` tool. The backend will automatically send the images to the user on your behalf, so you just need to say something like "Here are the pictures of the car you requested!" after using the tool.
Always be polite, friendly, and concise. Avoid using markdown that WhatsApp doesn't support (WhatsApp supports *bold*, _italic_, ~strikethrough~).
"""

def handle_gemini_conversation(wa_id, name, user_message, send_message_callback):
    """
    Handles the conversation using LangChain 1.2+ Agent API.
    """
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        logging.error("GEMINI_API_KEY is not set.")
        return "Sorry, the AI service is currently unavailable."

    # Define send_car_images dynamically so it has access to the wa_id and send_message_callback
    def _send_car_images(car_id: str) -> str:
        """
        Fetch image URLs for a specific car ID and signal that images should be sent.
        """
        images = get_car_images_db(car_id)
        if not images:
            return json.dumps({"status": "no_images_found"})
        
        # Send images natively via callback immediately
        from app.utils.whatsapp_utils import get_image_message_input
        for url in images:
            image_payload = get_image_message_input(wa_id, url)
            send_message_callback(image_payload)
            
        return json.dumps({"status": "images_found", "urls": images})

    send_car_images = StructuredTool.from_function(
        func=_send_car_images,
        name="send_car_images",
        description="Fetch image URLs for a specific car ID and signal that images should be sent."
    )

    llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=api_key)
    tools = [search_cars, get_car_details, send_car_images]

    # LangChain 1.2 uses create_agent which returns a LangGraph CompiledStateGraph
    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_INSTRUCTION,
        checkpointer=memory
    )

    logging.info(f"Processing message for {name} ({wa_id}) via LangChain 1.2 Agent...")

    try:
        inputs = {"messages": [{"role": "user", "content": user_message}]}
        # We use the wa_id as the thread_id so the checkpointer remembers the conversation per user
        config = {"configurable": {"thread_id": wa_id}}
        
        result = graph.invoke(inputs, config=config)
        
        # The graph returns a dict with a 'messages' key. The last message is the AI's final response.
        return result["messages"][-1].content

    except Exception as e:
        logging.error(f"Error communicating with LangChain: {e}")
        return "I'm having some trouble processing your request right now. Please try again later."
