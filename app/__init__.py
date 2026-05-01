from flask import Flask
from .config import load_configurations, configure_logging
from .views import webhook_blueprint

def create_app():
    app = Flask(__name__)

    # Load configurations and logging settings
    load_configurations(app)
    configure_logging()

    # Register the webhook blueprint
    app.register_blueprint(webhook_blueprint)

    return app
