"""Entrypoint ASGI: uvicorn core_api.main:app"""
from ctrl_common.config import env
from ctrl_common.logs import configure_logging

from .app import create_app
from .settings import CoreSettings

configure_logging(env("LOG_LEVEL", "INFO"))
app = create_app(CoreSettings.from_env())
