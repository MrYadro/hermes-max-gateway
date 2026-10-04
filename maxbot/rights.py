"""Гейт прав бота-админа (chat.admin_permissions → ChatAdminPermission).

Кэш — state.BOT_RIGHTS: пишется адаптером по событию bot_admin_permissions_changed
и ленивым fetch GET /chats/{id}/members/me. Fail-open: блокируем только когда
ДОКАЗАННО нет права (not is_admin либо список permissions без нужного);
при пустом/недоступном списке — пускаем, ошибку вернёт API.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _denial(rights: dict, permission: str) -> Optional[str]:
    perms = rights.get("permissions") or []
    if not rights.get("is_admin"):
        return "❌ Бот не админ в этом чате — действие недоступно (права выдаёт владелец чата)."
    if perms and permission not in perms:
        return f"❌ У бота нет права {permission} в этом чате (права админа изменены)."
    return None


async def require_right(client, chat_id: int, permission: str) -> Optional[str]:
    """None — действие разрешено; иначе текст отказа для пользователя."""
    from . import state
    from .max_api import MaxApiError

    rights = state.bot_rights(str(chat_id))
    if rights is None:
        try:
            member = await client.get_membership(int(chat_id))
        except MaxApiError as exc:
            logger.warning("max: membership chat=%s недоступен (%s) — гейт пропущен",
                           chat_id, exc)
            return None
        rights = {"is_admin": bool(member.get("is_admin")),
                  "permissions": [str(p) for p in member.get("permissions") or []]}
        state.remember_bot_rights(str(chat_id), **rights)
    return _denial(rights, permission)
