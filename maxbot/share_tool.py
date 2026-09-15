"""Инструмент агента max_share — карточка ссылки (share-вложение MAX)."""
import contextlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SHARE_SCHEMA = {
    "description": "Отправить ссылку красивой карточкой (share-вложение с превью): "
                   "url и необязательный текст сообщения. chat_id по умолчанию — "
                   "текущий чат сессии.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "полный https:// URL"},
            "text": {"type": "string", "description": "текст сообщения (по умолчанию — url)"},
            "chat_id": {"type": "integer",
                        "description": "ID чата MAX; по умолчанию — текущий чат сессии"},
        },
        "required": ["url"],
    },
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


async def _max_share_handler(args: Dict[str, Any], **kwargs) -> str:
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return "❌ url должен начинаться с http(s)://"
    text = str(args.get("text") or "").strip() or url  # share не принимается с пустым text
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
                int(chat_id), text,
                attachments=[{"type": "share", "payload": {"url": url}}])
        except MaxApiError as exc:
            return f"❌ Ошибка MAX API: {exc}"
    return f"✅ Карточка ссылки отправлена: {url}"


def register_share_tool(ctx) -> None:
    from .adapter import check_requirements

    ctx.register_tool(
        name="max_share",
        toolset="hermes-max",
        schema=_SHARE_SCHEMA,
        handler=_max_share_handler,
        check_fn=check_requirements,
        is_async=True,
        description=_SHARE_SCHEMA["description"])
