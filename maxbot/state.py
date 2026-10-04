"""Общий стейт плагина: коды виденных стикеров, права бота-админа (адаптер пишет, инструменты читают)."""
import time
from typing import Any, Dict, List, Optional

SEEN_STICKERS: List[Dict] = []  # {"code": str, "ts": float}
_MAX_REMEMBERED = 32

# Права бота-админа по чатам: {"chat_id": {"is_admin": bool, "permissions": [...]}}
# Пишется адаптером по событию bot_admin_permissions_changed и ленивым fetch
# в pin_tool (GET /chats/{id}/members/me); читается гейтами инструментов.
BOT_RIGHTS: Dict[str, Dict[str, Any]] = {}


def remember_bot_rights(chat_id: str, *, is_admin: bool,
                        permissions: Optional[List[str]] = None) -> None:
    BOT_RIGHTS[str(chat_id)] = {"is_admin": bool(is_admin),
                                "permissions": [str(p) for p in permissions or []]}


def bot_rights(chat_id: str) -> Optional[Dict[str, Any]]:
    return BOT_RIGHTS.get(str(chat_id))


def remember_sticker(code: str) -> None:
    code = str(code or "").strip()
    if not code:
        return
    SEEN_STICKERS[:] = [s for s in SEEN_STICKERS if s["code"] != code]
    SEEN_STICKERS.append({"code": code, "ts": time.time()})
    del SEEN_STICKERS[:-_MAX_REMEMBERED]


def recent_stickers(n: int = 10) -> List[Dict]:
    return list(reversed(SEEN_STICKERS[-n:]))
