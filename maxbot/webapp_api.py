"""HTTP-API мини-аппа «Assistants»: initData-авторизация, state/set, статика."""
import hashlib
import hmac
import json
import logging
import time
from typing import List, Optional, Tuple
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_INIT_HEADER = "X-WebApp-InitData"


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 3600,
                       now: Optional[float] = None) -> Optional[int]:
    """Официальная схема dev.max.ru/docs/webapps/validation.
    Возвращает user_id при валидной подписи и свежем auth_date, иначе None."""
    try:
        pairs: List[Tuple[str, str]] = []
        for chunk in (init_data or "").split("&"):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            pairs.append((k, unquote(v)))
        hashes = [v for k, v in pairs if k == "hash"]
        if len(hashes) != 1:
            return None
        original = hashes[0]
        params = dict(pairs)
        launch = "\n".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hash")
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, launch.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, original):
            return None
        auth_date = int(params.get("auth_date") or 0)
        ts = now if now is not None else time.time()
        if auth_date <= 0 or ts - auth_date > max_age:
            return None
        user = json.loads(params.get("user") or "{}")
        return int(user.get("id") or 0) or None
    except Exception:
        return None
