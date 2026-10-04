"""Типизированные модели апдейтов MAX Bot API (толерантны к лишним полям)."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Типы событий — полный паритет с OpenAPI-схемой MAX (tests/fixtures/max-api-schema.yaml,
# discriminator объекта Update). Паритет проверяет tests/test_parity.py: при обновлении
# схемы диф покажет, что добавить/убрать здесь.
UPDATE_TYPES = [
    "message_created", "message_callback", "bot_started", "bot_added", "bot_removed",
    "bot_stopped", "chat_title_changed", "dialog_cleared", "dialog_muted", "dialog_unmuted",
    "dialog_removed", "message_edited", "message_removed", "comment_created",
    "comment_edited", "comment_removed", "user_added", "user_removed",
    "bot_admin_permissions_changed",
]
# События, которые API отдаёт только через Webhook (changelog API MAX);
# в types для long polling их не просим.
WEBHOOK_ONLY_TYPES = frozenset({"bot_admin_permissions_changed"})
WEBHOOK_UPDATE_TYPES = tuple(UPDATE_TYPES)
POLLING_UPDATE_TYPES = tuple(t for t in UPDATE_TYPES if t not in WEBHOOK_ONLY_TYPES)


@dataclass
class User:
    user_id: int
    name: str = ""
    username: Optional[str] = None


@dataclass
class Attachment:
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    # location идёт плоскими полями вложения (не в payload) — не теряем
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # имя файла MAX держит на уровне вложения (не в payload) — важно для .docx и т.п.
    filename: Optional[str] = None


@dataclass
class MessageBody:
    mid: Optional[str] = None
    text: Optional[str] = None
    attachments: List[Attachment] = field(default_factory=list)


@dataclass
class Message:
    body: MessageBody
    chat_id: int
    chat_type: str  # "dialog" | "chat" | "channel"
    sender: Optional[User] = None
    timestamp: int = 0
    link: Optional[Dict[str, Any]] = None  # {"type": "reply", "mid": "..."} и т.п.
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Callback:
    callback_id: str
    payload: str
    message: Optional[Message] = None
    user: Optional["User"] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Update:
    update_type: str
    marker: Optional[int] = None
    message: Optional[Message] = None
    callback: Optional[Callback] = None
    chat_id: Optional[int] = None  # bot_started, bot_admin_permissions_changed
    user: Optional[User] = None    # bot_started
    # bot_admin_permissions_changed (OpenAPI: BotAdminPermissionsChangedUpdate);
    # user_id также приходит плоским в message_removed
    user_id: Optional[int] = None
    bot_id: Optional[int] = None
    is_channel: Optional[bool] = None
    is_admin: Optional[bool] = None
    permissions: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


def _user(d: Dict[str, Any]) -> Optional[User]:
    if not d:
        return None
    return User(user_id=int(d.get("user_id") or 0), name=d.get("name") or "",
                username=d.get("username"))


def _attachments(items: Any) -> List[Attachment]:
    return [Attachment(type=i.get("type", ""), payload=i.get("payload") or {},
                       latitude=i.get("latitude"), longitude=i.get("longitude"),
                       filename=i.get("filename"))
            for i in items or [] if isinstance(i, dict)]


def parse_message(d: Dict[str, Any]) -> Message:
    body_raw = d.get("body") or {}
    recipient = d.get("recipient") or {}
    body = MessageBody(mid=body_raw.get("mid"), text=body_raw.get("text"),
                       attachments=_attachments(body_raw.get("attachments")))
    return Message(body=body, chat_id=int(recipient.get("chat_id") or 0),
                   chat_type=recipient.get("chat_type") or "", sender=_user(d.get("sender")),
                   timestamp=int(d.get("timestamp") or 0), link=d.get("link"), raw=d)


def parse_update(d: Dict[str, Any]) -> Update:
    callback = None
    if d.get("callback"):
        cb = d["callback"]
        msg = parse_message(cb["message"]) if cb.get("message") else None
        callback = Callback(callback_id=str(cb.get("callback_id") or ""),
                            payload=str(cb.get("payload") or ""), message=msg,
                            user=_user(cb.get("user")),
                            raw=cb if isinstance(cb, dict) else {})
    return Update(update_type=d.get("update_type") or "", marker=d.get("marker"), raw=d,
                  message=parse_message(d["message"]) if d.get("message") else None,
                  callback=callback, chat_id=d.get("chat_id"), user=_user(d.get("user")),
                  user_id=d.get("user_id"), bot_id=d.get("bot_id"),
                  is_channel=d.get("is_channel"), is_admin=d.get("is_admin"),
                  permissions=[str(p) for p in d.get("permissions") or []])
