"""Hash canônico (JSON ordenado) usado para idempotência e snapshots."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal


def canonical_hash(obj: object) -> str:
    def default(o: object) -> str:
        if isinstance(o, Decimal):
            return f"{o}"
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(type(o).__name__)

    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default)
    return hashlib.sha256(data.encode()).hexdigest()
