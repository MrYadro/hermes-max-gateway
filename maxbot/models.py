"""Типизированные модели апдейтов MAX Bot API (толерантны к лишним полям)."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class User:
    user_id: int
    name: str = ""
    username: Optional[str] = None


@dataclass
class Attachment:
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)


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
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Update:
    update_type: str
    marker: Optional[int] = None
    message: Optional[Message] = None
    callback: Optional[Callback] = None
    chat_id: Optional[int] = None  # bot_started
    user: Optional[User] = None    # bot_started
    raw: Dict[str, Any] = field(default_factory=dict)


def _user(d: Dict[str, Any]) -> Optional[User]:
    if not d:
        return None
    return User(user_id=int(d.get("user_id") or 0), name=d.get("name") or "",
                username=d.get("username"))


def _attachments(items: Any) -> List[Attachment]:
    return [Attachment(type=i.get("type", ""), payload=i.get("payload") or {})
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
                            raw=cb if isinstance(cb, dict) else {})
    return Update(update_type=d.get("update_type") or "", marker=d.get("marker"), raw=d,
                  message=parse_message(d["message"]) if d.get("message") else None,
                  callback=callback, chat_id=d.get("chat_id"), user=_user(d.get("user")))
