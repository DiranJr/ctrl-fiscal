"""Entrypoint ASGI: uvicorn ctrl_fiscal.main:app"""
from ctrl_common.config import env
from ctrl_common.logs import configure_logging

from .api.app import create_app
from .settings import FiscalSettings

configure_logging(env("LOG_LEVEL", "INFO"))
app = create_app(FiscalSettings.from_env("fiscal-api"))
