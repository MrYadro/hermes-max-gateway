"""Инструмент агента max_contact — отправить контакт (vCard) в MAX."""
import contextlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CONTACT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "имя контакта (FN)"},
        "phone": {"type": "string", "description": "телефон в любом формате, лучше +7..."},
        "chat_id": {"type": "integer",
                    "description": "ID чата MAX; по умолчанию — текущий чат сессии"},
    },
    "required": ["name", "phone"],
}


def _build_vcf(name: str, phone: str) -> str:
    return (f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:{name}\r\n"
            f"TEL;TYPE=cell:{phone}\r\nEND:VCARD\r\n")


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


async def _max_contact_handler(args: Dict[str, Any], **kwargs) -> str:
    name = str(args.get("name") or "").strip()
    phone = str(args.get("phone") or "").strip()
    if not name or not phone:
        return "❌ Нужны name и phone."
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
                attachments=[{"type": "contact",
                              "payload": {"vcf_info": _build_vcf(name, phone)}}])
        except MaxApiError as exc:
            return f"❌ Ошибка MAX API: {exc}"
    return f"✅ Контакт отправлен: {name} {phone}"


def register_contact_tool(ctx) -> None:
    from .adapter import check_requirements

    ctx.register_tool(
        name="max_contact",
        toolset="hermes-max",
        schema=_CONTACT_SCHEMA,
        handler=_max_contact_handler,
        check_fn=check_requirements,
        is_async=True,
        description="Отправить контакт в чат MAX (карточка vCard): имя и телефон. "
                    "chat_id по умолчанию — текущий чат сессии.")
