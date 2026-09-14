"""Общий стейт плагина: коды виденных стикеров (адаптер пишет, инструменты читают)."""
import time
from typing import Dict, List

SEEN_STICKERS: List[Dict] = []  # {"code": str, "ts": float}
_MAX_REMEMBERED = 32


def remember_sticker(code: str) -> None:
    code = str(code or "").strip()
    if not code:
        return
    SEEN_STICKERS[:] = [s for s in SEEN_STICKERS if s["code"] != code]
    SEEN_STICKERS.append({"code": code, "ts": time.time()})
    del SEEN_STICKERS[:-_MAX_REMEMBERED]


def recent_stickers(n: int = 10) -> List[Dict]:
    return list(reversed(SEEN_STICKERS[-n:]))
