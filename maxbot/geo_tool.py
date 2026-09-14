"""Инструмент агента max_geo — отправить геопозицию вложением MAX."""
import contextlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GEO_SCHEMA = {
    "type": "object",
    "properties": {
        "latitude": {"type": "number", "description": "широта, -90..90"},
        "longitude": {"type": "number", "description": "долгота, -180..180"},
        "chat_id": {"type": "integer",
                    "description": "ID чата MAX; по умолчанию — текущий чат сессии"},
    },
    "required": ["latitude", "longitude"],
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


def _MaxClient(token, base_url=None):
    from .max_api import MaxClient
    return MaxClient(token, base_url=base_url)


async def _max_geo_handler(args: Dict[str, Any], **kwargs) -> str:
    try:
        lat = float(args.get("latitude"))
        lng = float(args.get("longitude"))
    except (TypeError, ValueError):
        return "❌ latitude и longitude должны быть числами."
    if not (-90 <= lat <= 90):
        return "❌ Широта должна быть в диапазоне -90..90."
    if not (-180 <= lng <= 180):
        return "❌ Долгота должна быть в диапазоне -180..180."
    chat_id = args.get("chat_id") or _session_chat_id()
    if chat_id is None:
        return ("❌ Не удалось определить chat_id. Укажите его параметром chat_id "
                "(числовой ID чата MAX), либо вызывайте из чата MAX.")

    token = _secret("MAX_ACCESS_TOKEN", "")
    if not token:
        return "❌ MAX_ACCESS_TOKEN не настроен."

    from .max_api import MaxApiError
    async with _MaxClient(token, base_url=_secret("MAX_API_BASE", "") or None) as client:
        try:
            # локация — плоские поля вложения, БЕЗ обёртки payload (прото MAX)
            await client.send_message(
                int(chat_id), "",
                attachments=[{"type": "location", "latitude": lat, "longitude": lng}])
        except MaxApiError as exc:
            return f"❌ Ошибка MAX API: {exc}"
    return f"✅ Геопозиция отправлена: {lat}, {lng}"


def register_geo_tool(ctx) -> None:
    from .adapter import check_requirements

    ctx.register_tool(
        name="max_geo",
        toolset="hermes-max",
        schema=_GEO_SCHEMA,
        handler=_max_geo_handler,
        check_fn=check_requirements,
        is_async=True,
        description="Отправить геопозицию в чат MAX (карта с точкой): широта и долгота. "
                    "chat_id по умолчанию — текущий чат сессии.")
