"""Инструмент агента max_sticker — отправка стикеров в MAX."""
import contextlib
import json
import logging
from typing import Any, Dict, Optional

from .state import recent_stickers

logger = logging.getLogger(__name__)

_STICKER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["send", "recent"],
                   "description": "send — отправить стикер (code; по умолчанию последний "
                                  "присланный пользователем); recent — список недавних кодов"},
        "code": {"type": "string", "description": "код стикера (из recent или от пользователя)"},
        "chat_id": {"type": "integer",
                    "description": "ID чата MAX; по умолчанию — текущий чат сессии"},
    },
    "required": ["action"],
}


def _secret(name: str, default: str = "") -> str:
    with contextlib.suppress(Exception):
        from gateway.platforms._shared import get_scoped_secret
        return get_scoped_secret(name, default) or default
    return default


def _session_chat_id() -> Optional[int]:
    with contextlib.suppress(Exception):
        from gateway.session_context import get_session_env
        if (get_session_env("HERMES_SESSION_PLATFORM") or "").lower() == "max":
            raw = (get_session_env("HERMES_SESSION_CHAT_ID") or "").strip()
            if raw.lstrip("-").isdigit():
                return int(raw)
    return None


async def _max_sticker_handler(args: Dict[str, Any], **kwargs) -> str:
    action = str(args.get("action") or "send")
    if action == "recent":
        seen = recent_stickers()
        if not seen:
            return ("Недавних стикеров нет: пользователь ещё не присылал стикеры "
                    "в этой сессии гейтвея.")
        return json.dumps(seen, ensure_ascii=False)

    code = str(args.get("code") or "").strip()
    if not code:
        seen = recent_stickers(1)
        if not seen:
            return ("❌ Не указан code, и присланных стикеров пока нет — попросите "
                    "пользователя прислать стикер или укажите code.")
        code = seen[0]["code"]

    chat_id = args.get("chat_id") or _session_chat_id()
    if chat_id is None:
        return ("❌ Не удалось определить chat_id. Укажите его параметром chat_id "
                "(числовой ID чата MAX), либо вызывайте из чата MAX.")

    token = _secret("MAX_ACCESS_TOKEN", "")
    if not token:
        return "❌ MAX_ACCESS_TOKEN не настроен."

    from .max_api import MaxApiError, MaxClient
    async with MaxClient(token, base_url=_secret("MAX_API_BASE", "") or None) as client:
        try:
            await client.send_message(
                int(chat_id), "",
                attachments=[{"type": "sticker", "payload": {"code": code}}])
        except MaxApiError as exc:
            return f"❌ Ошибка MAX API: {exc}"
    return f"✅ Стикер отправлен ({code})."


def register_sticker_tool(ctx) -> None:
    from .adapter import check_requirements

    ctx.register_tool(
        name="max_sticker",
        toolset="hermes-max",
        schema=_STICKER_SCHEMA,
        handler=_max_sticker_handler,
        check_fn=check_requirements,
        is_async=True,
        description="Отправить стикер в чат MAX. По умолчанию — последний присланный "
                    "пользователем стикер; recent — список недавних кодов стикеров.")
