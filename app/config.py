import sys
import os
from dotenv import load_dotenv
import logging

def load_configurations(app):
    load_dotenv()
    app.config["ACCESS_TOKEN"] = os.getenv("ACCESS_TOKEN")
    app.config["APP_ID"] = os.getenv("APP_ID")
    app.config["APP_SECRET"] = os.getenv("APP_SECRET")
    app.config["RECIPIENT_WAID"] = os.getenv("RECIPIENT_WAID")
    app.config["VERSION"] = os.getenv("VERSION")
    app.config["PHONE_NUMBER_ID"] = os.getenv("PHONE_NUMBER_ID")
    app.config["VERIFY_TOKEN"] = os.getenv("VERIFY_TOKEN")
    
    app.config["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
    
    app.config["PG_HOST"] = os.getenv("PG_HOST", "localhost")
    app.config["PG_PORT"] = os.getenv("PG_PORT", "5432")
    app.config["PG_USER"] = os.getenv("PG_USER", "postgres")
    app.config["PG_PASS"] = os.getenv("PG_PASS", "postgres")
    app.config["PG_DBNAME"] = os.getenv("PG_DBNAME", "postgres")
    
    app.config["AWS_ACCESS_KEY_ID"] = os.getenv("AWS_ACCESS_KEY_ID")
    app.config["AWS_SECRET_ACCESS_KEY"] = os.getenv("AWS_SECRET_ACCESS_KEY")
    app.config["AWS_REGION"] = os.getenv("AWS_REGION", "us-east-1")
    app.config["AWS_S3_BUCKET_NAME"] = os.getenv("AWS_S3_BUCKET_NAME")

def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
