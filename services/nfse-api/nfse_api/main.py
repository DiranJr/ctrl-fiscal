"""Entrypoint ASGI: uvicorn nfse_api.main:app"""
from ctrl_common.config import env
from ctrl_common.logs import configure_logging

from .app import create_app
from .settings import NfseSettings

configure_logging(env("LOG_LEVEL", "INFO"))
app = create_app(NfseSettings.from_env())
