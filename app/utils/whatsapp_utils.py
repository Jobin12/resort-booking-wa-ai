import logging
from flask import current_app, jsonify
import json
import requests
import re
from app.services.openai_service import handle_openai_conversation

def log_http_response(response):
    logging.info(f"Status: {response.status_code}")
    logging.info(f"Content-type: {response.headers.get('content-type')}")
    logging.info(f"Body: {response.text}")

def get_text_message_input(recipient, text):
    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
    )

def get_image_message_input(recipient, image_url):
    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "image",
            "image": {"link": image_url},
        }
    )

def send_message(data):
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
    }

    url = f"https://graph.facebook.com/{current_app.config['VERSION']}/{current_app.config['PHONE_NUMBER_ID']}/messages"

    try:
        response = requests.post(
            url, data=data, headers=headers, timeout=10
        )
        response.raise_for_status()
    except requests.Timeout:
        logging.error("Timeout occurred while sending message")
        return jsonify({"status": "error", "message": "Request timed out"}), 408
    except requests.RequestException as e:
        error_body = e.response.text if e.response is not None else "No response body"
        logging.error(f"Request failed due to: {e}. Body: {error_body}")
        return jsonify({"status": "error", "message": "Failed to send message"}), 500
    else:
        log_http_response(response)
        return response

def process_whatsapp_message(body):
    wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
    name = body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]

    message = body["entry"][0]["changes"][0]["value"]["messages"][0]
    message_body = message["text"]["body"]

    logging.info(f"Received message from {name} ({wa_id}): {message_body}")

    final_response = handle_openai_conversation(wa_id, name, message_body, send_message_callback=send_message)

    if final_response:
        data = get_text_message_input(wa_id, final_response)
        send_message(data)

def is_valid_whatsapp_message(body):
    """
    Check if the incoming webhook event has a valid WhatsApp message structure.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
        and body["entry"][0]["changes"][0]["value"]["messages"][0].get("text")
    )
