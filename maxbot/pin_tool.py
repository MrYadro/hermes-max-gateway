"""Инструмент агента max_pin — закрепление сообщений в чатах MAX."""
import contextlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _secret(name: str, default: str = "") -> str:
    with contextlib.suppress(Exception):
        from gateway.platforms._shared import get_scoped_secret
        return get_scoped_secret(name, default) or default
    with contextlib.suppress(Exception):
        import os

        return os.environ.get(name, default)
    return default


def _session_chat_id() -> Optional[int]:
    with contextlib.suppress(Exception):
        from gateway.session_context import get_session_env
        if (get_session_env("HERMES_SESSION_PLATFORM") or "").lower() == "max":
            raw = (get_session_env("HERMES_SESSION_CHAT_ID") or "").strip()
            if raw.lstrip("-").isdigit():
                return int(raw)
    return None

_PIN_SCHEMA = {
    "description": "Закрепление сообщений в групповых чатах и каналах MAX. "
                   "pin — закрепить (нужен message_id, бот должен быть админом); "
                   "unpin — открепить; pinned — текущее закреплённое сообщение.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["pin", "unpin", "pinned"],
                       "description": "pin — закрепить сообщение; unpin — открепить; "
                                      "pinned — показать закреплённое"},
            "message_id": {"type": "string",
                           "description": "mid сообщения MAX для pin (mid.* из ответов)"},
            "chat_id": {"type": "integer",
                        "description": "ID чата MAX; по умолчанию — текущий чат сессии"},
        },
        "required": ["action"],
    },
}


async def _max_pin_handler(args: Dict[str, Any], **kwargs) -> str:
    action = str(args.get("action") or "").strip()
    if action not in ("pin", "unpin", "pinned"):
        return "❌ Действие должно быть pin | unpin | pinned."
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
            if action == "pin":
                mid = str(args.get("message_id") or "").strip()
                if not mid:
                    return "❌ Для pin нужен message_id."
                ok = await client.pin_message(int(chat_id), mid)
                return "✅ Закреплено." if ok else "⚠️ Не удалось закрепить."
            if action == "unpin":
                ok = await client.unpin_message(int(chat_id))
                return "✅ Откреплено." if ok else "⚠️ Не удалось открепить."
            msg = await client.get_pinned_message(int(chat_id))
            text = ((msg.get("body") or {}).get("text") or "").strip()
            return f"📌 {text[:200]}" if msg else "В этом чате нет закреплённого сообщения."
        except MaxApiError as exc:
            logger.warning("max_pin: %s chat=%s: %s", action, chat_id, exc)
            return f"❌ MAX API: {exc}"


def register_pin_tool(ctx) -> None:
    from .adapter import check_requirements

    ctx.register_tool(
        name="max_pin",
        toolset="hermes-max",
        schema=_PIN_SCHEMA,
        handler=_max_pin_handler,
        check_fn=check_requirements,
        is_async=True,
        description=_PIN_SCHEMA["description"])
