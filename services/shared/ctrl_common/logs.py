"""Logging estruturado com redação de campos sensíveis."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_SENSITIVE = re.compile(
    r"(?i)(password|senha|passwd|secret|token|authorization|cookie|pfx|p12|private[_-]?key|csrf)"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_XML = re.compile(r"<\?xml.*", re.S)
_B64_BLOB = re.compile(r"[A-Za-z0-9+/=]{200,}")


def redact(text: str) -> str:
    text = _SENSITIVE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)
    text = _XML.sub("[XML REDACTED]", text)
    text = _B64_BLOB.sub("[BLOB REDACTED]", text)
    return text[:2000]


class JsonRedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            # Somente o tipo; stacktrace pode carregar valores sensíveis.
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonRedactingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Logs de acesso do uvicorn não incluem corpo; cabeçalhos não são registrados.
    logging.getLogger("httpx").setLevel(logging.WARNING)
