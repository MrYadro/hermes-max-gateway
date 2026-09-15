"""Инструмент агента max_group — управление групповым чатом MAX.

Действия: pin/unpin, список участников/админов, назначение/снятие админа,
кик, выход из чата, переименование. chat_id по умолчанию — чат текущей
MAX-сессии (HERMES_SESSION_CHAT_ID/PLATFORM).
"""
import contextlib
import json
import logging
from typing import Any, Dict, Optional

from gateway.platforms._shared import get_scoped_secret

logger = logging.getLogger(__name__)

_ACTIONS_HELP = (
    "pin — закрепить сообщение (нужен message_id); "
    "unpin — открепить; pinned — показать закреплённое; "
    "my_permissions — права бота в чате; "
    "members — список участников; "
    "admins — список админов; "
    "add_admin — назначить админа (user_id, permissions?); "
    "remove_admin — снять админа (user_id); "
    "kick — исключить участника (user_id); "
    "leave — бот покидает чат; "
    "info — информация о чате; "
    "rename — переименовать (title, description?); "
)

_CHANNEL_SCHEMA = {
    "description": "Комментарии к постам канала MAX (бот — админ канала): список, отправка, "
                   "правка, удаление. В комментариях не поддерживаются ссылки и упоминания.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["comments_list", "comment_post", "comment_edit", "comment_delete"],
                       "description": "comments_list — комментарии к посту (post_id); "
                                      "comment_post — написать комментарий (post_id, text); "
                                      "comment_edit — править свой (post_id, comment_id, text); "
                                      "comment_delete — удалить (post_id, comment_id)"},
            "post_id": {"type": "string", "description": "mid поста в канале"},
            "comment_id": {"type": "string", "description": "для comment_edit/comment_delete"},
            "text": {"type": "string", "description": "текст комментария (markdown без ссылок)"},
        },
        "required": ["action"],
    },
}


_SCHEMA = {
    "description": "Управление групповым чатом MAX: закрепление сообщений, участники, "
                   "админы, кик, выход, переименование. Требует прав администратора у бота.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["pin", "unpin", "pinned", "my_permissions", "members",
                                "admins", "add_admin", "remove_admin", "kick", "leave",
                                "info", "rename"],
                       "description": _ACTIONS_HELP},
            "chat_id": {"type": "integer",
                        "description": "ID чата MAX; по умолчанию — текущий чат сессии"},
            "message_id": {"type": "string", "description": "для pin: ID сообщения (mid)"},
            "user_id": {"type": "integer", "description": "для add_admin/remove_admin/kick"},
            "permissions": {"type": "array", "items": {"type": "string"},
                            "description": "права нового админа: read_all_messages, edit, write, delete"},
            "title": {"type": "string", "description": "для rename: новое название чата"},
            "description": {"type": "string", "description": "для rename: новое описание чата"},
        },
        "required": ["action"],
    },
}


def _session_chat_id() -> Optional[int]:
    with contextlib.suppress(Exception):
        from gateway.session_context import get_session_env
        if (get_session_env("HERMES_SESSION_PLATFORM") or "").lower() == "max":
            raw = (get_session_env("HERMES_SESSION_CHAT_ID") or "").strip()
            if raw.lstrip("-").isdigit():
                return int(raw)
    return None


async def _max_group_handler(args: Dict[str, Any], **kwargs) -> str:
    from .max_api import MaxApiError, MaxClient

    action = str(args.get("action") or "").strip()
    chat_id = args.get("chat_id") or _session_chat_id()
    if chat_id is None:
        return ("❌ Не удалось определить chat_id. Укажите его параметром chat_id "
                "(числовой ID чата MAX), либо вызывайте из чата MAX.")
    chat_id = int(chat_id)

    token = get_scoped_secret("MAX_ACCESS_TOKEN", "")
    if not token:
        return "❌ MAX_ACCESS_TOKEN не настроен."
    base = get_scoped_secret("MAX_API_BASE", "") or None

    async with MaxClient(token, base_url=base) as client:
        try:
            if action == "pin":
                mid = str(args.get("message_id") or "")
                if not mid:
                    return "❌ Для pin укажите message_id."
                ok = await client.pin_message(chat_id, mid)
                return "✅ Сообщение закреплено." if ok else "⚠️ MAX не подтвердил закрепление."
            if action == "unpin":
                return "✅ Закрепление снято." if await client.unpin_message(chat_id) \
                    else "⚠️ MAX не подтвердил."
            if action == "pinned":
                return json.dumps(await client.get_pinned_message(chat_id),
                                  ensure_ascii=False)[:3500] or "Закреплённого сообщения нет."
            if action == "my_permissions":
                return json.dumps(await client.get_membership(chat_id),
                                  ensure_ascii=False)[:3500]
            if action == "members":
                return json.dumps(await client.get_members(chat_id), ensure_ascii=False)[:3500]
            if action == "admins":
                return json.dumps(await client.get_admins(chat_id), ensure_ascii=False)[:3500]
            if action == "add_admin":
                uid = args.get("user_id")
                if not uid:
                    return "❌ Для add_admin укажите user_id."
                ok = await client.add_admin(chat_id, int(uid), args.get("permissions"))
                return f"✅ Пользователь {uid} назначен админом." if ok else "⚠️ MAX отказал."
            if action == "remove_admin":
                uid = args.get("user_id")
                if not uid:
                    return "❌ Для remove_admin укажите user_id."
                ok = await client.remove_admin(chat_id, int(uid))
                return f"✅ Пользователь {uid} больше не админ." if ok else "⚠️ MAX отказал."
            if action == "kick":
                uid = args.get("user_id")
                if not uid:
                    return "❌ Для kick укажите user_id."
                ok = await client.kick_member(chat_id, int(uid))
                return f"✅ Пользователь {uid} исключён." if ok else "⚠️ MAX отказал."
            if action == "leave":
                return "✅ Бот покинул чат." if await client.leave_chat(chat_id) else "⚠️ MAX отказал."
            if action == "info":
                return json.dumps(await client.get_chat(chat_id), ensure_ascii=False)[:3500]
            if action == "rename":
                title = str(args.get("title") or "").strip()
                if not title and not args.get("description"):
                    return "❌ Для rename укажите title и/или description."
                fields = {}
                if title:
                    fields["title"] = title
                if args.get("description") is not None:
                    fields["description"] = str(args["description"])
                await client.patch_chat(chat_id, **fields)
                return "✅ Чат обновлён: " + ", ".join(fields)
            return f"❌ Неизвестное действие: {action}. {_ACTIONS_HELP}"
        except MaxApiError as exc:
            return f"❌ Ошибка MAX API: {exc}"


async def _max_channel_handler(args: Dict[str, Any], **kwargs) -> str:
    from .markdown import comment_markdown
    from .max_api import MaxApiError, MaxClient

    action = str(args.get("action") or "").strip()
    post_id = str(args.get("post_id") or "").strip()
    if not post_id:
        return "❌ Укажите post_id (mid поста канала)."
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "")
    if not token:
        return "❌ MAX_ACCESS_TOKEN не настроен."
    async with MaxClient(token, base_url=get_scoped_secret("MAX_API_BASE", "") or None) as client:
        try:
            if action == "comments_list":
                return json.dumps(await client.get_comments(post_id), ensure_ascii=False)[:3500]
            if action == "comment_post":
                text = str(args.get("text") or "").strip()
                if not text:
                    return "❌ Для comment_post укажите text."
                mid = await client.post_comment(post_id, comment_markdown(text))
                return f"✅ Комментарий отправлен ({mid})."
            if action == "comment_edit":
                cid = str(args.get("comment_id") or "").strip()
                text = str(args.get("text") or "").strip()
                if not (cid and text):
                    return "❌ Для comment_edit нужны comment_id и text."
                ok = await client.edit_comment(post_id, cid, comment_markdown(text))
                return "✅ Комментарий обновлён." if ok else "⚠️ MAX отказал."
            if action == "comment_delete":
                cid = str(args.get("comment_id") or "").strip()
                if not cid:
                    return "❌ Для comment_delete укажите comment_id."
                ok = await client.delete_comment(post_id, cid)
                return "✅ Комментарий удалён." if ok else "⚠️ MAX отказал."
            return "❌ Неизвестное действие. См. описание action."
        except MaxApiError as exc:
            return f"❌ Ошибка MAX API: {exc}"


def register_group_tool(ctx) -> None:
    from .adapter import check_requirements

    ctx.register_tool(
        name="max_channel",
        toolset="hermes-max",
        schema=_CHANNEL_SCHEMA,
        handler=_max_channel_handler,
        check_fn=check_requirements,
        is_async=True,
        description=_CHANNEL_SCHEMA["description"])
    ctx.register_tool(
        name="max_group",
        toolset="hermes-max",
        schema=_SCHEMA,
        handler=_max_group_handler,
        check_fn=check_requirements,
        is_async=True,
        description=_SCHEMA["description"])
