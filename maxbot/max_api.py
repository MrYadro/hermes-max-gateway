"""Тонкий асинхронный клиент MAX Bot API (platform-api2.max.ru)."""
import asyncio
import contextlib
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .models import Message, Update, User, parse_update

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://platform-api2.max.ru"


class MaxApiError(Exception):
    """Ошибка MAX API. status=0 — сетевая. code — машинный код (напр. attachment.not.ready)."""

    def __init__(self, status: int, message: str = "", code: Optional[str] = None):
        self.status, self.code, self.message = status, code, message
        super().__init__(f"HTTP {status} code={code!r} {message}")


def _backoff(attempt: int, base: float) -> float:
    return min(60.0, base * (2 ** attempt)) * (0.5 + random.random() / 2)


class MaxClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE, *,
                 rps: float = 30.0, retries: int = 3, backoff_base: float = 1.0,
                 disable_link_preview: bool = True):
        self._client = httpx.AsyncClient(
            base_url=base_url or DEFAULT_BASE, headers={"Authorization": token},
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=60.0, pool=30.0))
        self._min_interval = 1.0 / max(0.1, rps)
        self._last_sent = 0.0
        self._gate = asyncio.Lock()
        self._retries = retries
        self._backoff_base = backoff_base
        self.disable_link_preview = disable_link_preview

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "MaxClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _throttle(self) -> None:
        async with self._gate:
            wait = self._last_sent + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_sent = time.monotonic()

    async def _request(self, method: str, path: str, *, params: Optional[dict] = None,
                       json_body: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        attempt = 0
        while True:
            await self._throttle()
            try:
                resp = await self._client.request(method, path, params=params,
                                                  json=json_body, timeout=timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._retries:
                    raise MaxApiError(0, str(exc), code="network") from exc
                attempt += 1
                await asyncio.sleep(_backoff(attempt, self._backoff_base))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self._retries:
                    break
                attempt += 1
                retry_after = resp.headers.get("Retry-After")
                delay = (min(60.0, float(retry_after)) if retry_after
                         else _backoff(attempt, self._backoff_base))
                await asyncio.sleep(delay)
                continue
            break
        if resp.status_code >= 400:
            code = message = None
            try:
                body = resp.json()
                code, message = body.get("code"), body.get("message")
            except Exception:
                pass
            raise MaxApiError(resp.status_code, message or resp.text[:200], code)
        if not resp.content:
            return {}
        return resp.json()

    async def get_me(self) -> User:
        from .models import User
        d = await self._request("GET", "/me")
        return User(user_id=int(d.get("user_id") or 0), name=d.get("name") or "", username=d.get("username"))

    async def get_chat(self, chat_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}")

    async def get_message(self, message_id: str) -> Message:
        from .models import parse_message
        d = await self._request("GET", f"/messages/{message_id}")
        return parse_message(d.get("message") or d)

    async def send_message(self, chat_id: int, text: str, *, attachments: Optional[List[dict]] = None,
                           notify: bool = True, reply_to_mid: Optional[str] = None) -> str:
        body: Dict[str, Any] = {"text": text, "format": "markdown", "notify": notify}
        if attachments:
            body["attachments"] = attachments
        if reply_to_mid:
            body["link"] = {"type": "reply", "mid": reply_to_mid}
        params: Dict[str, Any] = {"chat_id": chat_id}
        if self.disable_link_preview:
            params["disable_link_preview"] = "true"
        for attempt in range(4):  # attachment.not.ready: 3 ретрая
            try:
                resp = await self._request("POST", "/messages", params=params, json_body=body)
            except MaxApiError as exc:
                if exc.code != "attachment.not.ready" or attempt >= 3:
                    raise
                await asyncio.sleep(max(0.3, self._backoff_base * (2 ** attempt)))
                continue
            mid = extract_message_id(resp)
            if mid is None:
                raise MaxApiError(200, "send_message: нет message_id в ответе", code="no_mid")
            return mid
        raise MaxApiError(0, "unreachable", code="internal")

    async def edit_message(self, message_id: str, text: str, *,
                           attachments: Optional[List[dict]] = None) -> bool:
        body: Dict[str, Any] = {"text": text, "format": "markdown"}
        if attachments is not None:
            body["attachments"] = attachments
        resp = await self._request("PUT", "/messages", params={"message_id": message_id}, json_body=body)
        return bool(resp.get("success", True))

    async def delete_message(self, message_id: str) -> bool:
        resp = await self._request("DELETE", "/messages", params={"message_id": message_id})
        return bool(resp.get("success", True))

    async def get_updates(self, marker: Optional[int] = None, timeout: int = 90) -> Tuple[int, List[Update]]:
        params = {"timeout": timeout, "types": "message_created,message_callback,bot_started,comment_created,comment_edited,comment_removed"}
        if marker is not None:
            params["marker"] = marker
        resp = await self._request("GET", "/updates", params=params, timeout=timeout + 5.0)
        updates = [parse_update(u) for u in resp.get("updates") or []]
        return int(resp.get("marker") or marker or 0), updates

    async def get_subscriptions(self) -> List[Dict[str, Any]]:
        return list((await self._request("GET", "/subscriptions")).get("subscriptions") or [])

    async def subscribe(self, url: str, update_types: List[str], secret: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"url": url, "update_types": update_types}
        if secret:
            body["secret"] = secret
        return await self._request("POST", "/subscriptions", json_body=body)

    async def unsubscribe(self) -> bool:
        resp = await self._request("DELETE", "/subscriptions")
        return bool(resp.get("success", True))

    async def answer_callback(self, callback_id: str, text: Optional[str] = None) -> bool:
        body: Dict[str, Any] = {"callback_id": callback_id}
        if text:
            body["message"] = {"text": text}
        params = {"disable_link_preview": "true"} if self.disable_link_preview else None
        resp = await self._request("POST", "/answers", params=params, json_body=body)
        return bool(resp.get("success", True))

    async def chat_action(self, chat_id: int, action: str = "typing_on") -> bool:
        resp = await self._request("POST", f"/chats/{chat_id}/actions", json_body={"action": action})
        return bool(resp.get("success", True))

    async def set_commands(self, commands: List[Dict[str, str]]) -> bool:
        resp = await self._request("PATCH", "/me/commands", json_body={"commands": commands})
        return bool(resp.get("success", True))

    # ── управление группой ──
    async def send_image_by_url(self, chat_id: int, image_url: str,
                                caption: str = "") -> str:
        """Картинка по внешнему URL: скачивает СЕРВЕР MAX (без загрузки с нашей стороны)."""
        body = {"text": caption, "format": "markdown", "notify": True,
                "attachments": [{"type": "image", "payload": {"url": image_url}}]}
        resp = await self._request("POST", "/messages",
                                   params={"chat_id": chat_id,
                                           **({"disable_link_preview": "true"}
                                              if self.disable_link_preview else {})},
                                   json_body=body)
        mid = extract_message_id(resp)
        if mid is None:
            raise MaxApiError(200, "send_image_by_url: нет message_id", code="no_mid")
        return mid

    async def pin_message(self, chat_id: int, message_id: str) -> bool:
        resp = await self._request("PUT", f"/chats/{chat_id}/pin",
                                   json_body={"message_id": message_id})
        return bool(resp.get("success", True))

    async def unpin_message(self, chat_id: int) -> bool:
        resp = await self._request("DELETE", f"/chats/{chat_id}/pin")
        return bool(resp.get("success", True))

    # ── комментарии к постам в каналах ──
    async def post_comment(self, post_id: str, text: str) -> str:
        body = {"text": text, "format": "markdown"}
        resp = await self._request("POST", f"/messages/{post_id}/comments", json_body=body)
        mid = extract_message_id(resp)
        if mid is None:
            raise MaxApiError(200, "post_comment: нет message_id", code="no_mid")
        return mid

    async def get_comments(self, post_id: str) -> list:
        return list((await self._request("GET", f"/messages/{post_id}/comments")).get("comments") or [])

    async def get_comment(self, post_id: str, comment_id: str) -> dict:
        return await self._request("GET", f"/messages/{post_id}/comments/{comment_id}")

    async def edit_comment(self, post_id: str, comment_id: str, text: str) -> bool:
        resp = await self._request("PUT", f"/messages/{post_id}/comments",
                                   params={"comment_id": comment_id},
                                   json_body={"text": text, "format": "markdown"})
        return bool(resp.get("success", True))

    async def delete_comment(self, post_id: str, comment_id: str) -> bool:
        resp = await self._request("DELETE", f"/messages/{post_id}/comments",
                                   params={"comment_id": comment_id})
        return bool(resp.get("success", True))

    async def get_video_info(self, video_token: str) -> dict:
        return await self._request("GET", f"/videos/{video_token}")

    async def get_pinned_message(self, chat_id: int) -> dict:
        return (await self._request("GET", f"/chats/{chat_id}/pin")).get("message") or {}

    async def get_membership(self, chat_id: int) -> dict:
        return await self._request("GET", f"/chats/{chat_id}/members/me")

    async def get_members(self, chat_id: int) -> list:
        return list((await self._request("GET", f"/chats/{chat_id}/members")).get("members") or [])

    async def get_admins(self, chat_id: int) -> list:
        return list((await self._request("GET", f"/chats/{chat_id}/members/admins")).get("admins") or [])

    async def add_admin(self, chat_id: int, user_id: int,
                        permissions: Optional[list] = None) -> bool:
        body = {"admins": [{"user_id": user_id, "permissions": permissions or ["read_all_messages"]}]}
        resp = await self._request("POST", f"/chats/{chat_id}/members/admins", json_body=body)
        return bool(resp.get("success", True))

    async def remove_admin(self, chat_id: int, user_id: int) -> bool:
        resp = await self._request("DELETE", f"/chats/{chat_id}/members/admins/{user_id}")
        return bool(resp.get("success", True))

    async def kick_member(self, chat_id: int, user_id: int) -> bool:
        resp = await self._request("DELETE", f"/chats/{chat_id}/members/{user_id}")
        return bool(resp.get("success", True))

    async def leave_chat(self, chat_id: int) -> bool:
        resp = await self._request("DELETE", f"/chats/{chat_id}/members/me")
        return bool(resp.get("success", True))

    async def patch_chat(self, chat_id: int, **fields) -> dict:
        return await self._request("PATCH", f"/chats/{chat_id}", json_body=fields)

    async def get_upload_slot(self, kind: str):
        """POST /uploads → (url, token|None). Для video/audio токен приходит сразу."""
        resp = await self._request("POST", "/uploads", params={"type": kind})
        return str(resp.get("url") or ""), resp.get("token")

    async def get_upload_url(self, kind: str) -> str:
        url, _ = await self.get_upload_slot(kind)
        if not url:
            raise MaxApiError(200, "uploads: нет url", code="no_url")
        return url

    async def upload_to_url(self, upload_url: str, path: str, token_hint: Optional[str] = None) -> str:
        """Файловый POST на upload-хост: ТОЛЬКО curl-подобный multipart
        (поле data + per-part Content-Type) — API-хосты отвергают иное.

        Токен: video/audio — из token_hint (файл отвечает retval-XML);
        image/file — в ответе загрузки (топ-уровень или карта photos).
        """
        import os
        with open(path, "rb") as fh:
            resp = await self._client.post(
                upload_url,
                files={"data": (os.path.basename(path), fh.read(),
                                "application/octet-stream")})
        if resp.status_code >= 400:
            raise MaxApiError(resp.status_code, resp.text[:200], code="upload_failed")
        with contextlib.suppress(ValueError):
            body = resp.json()
            if isinstance(body, dict):
                tok = _extract_upload_token(body)
                if tok:
                    return str(tok)
        if token_hint:
            return str(token_hint)
        raise MaxApiError(resp.status_code, "upload: нет token", code="no_token")


def _extract_upload_token(body: Dict[str, Any]) -> Optional[str]:
    """Токен из ответа upload-хоста: топ-уровень или карта вида photos/{id}/{token}."""
    tok = body.get("token")
    if isinstance(tok, str) and tok:
        return tok
    for value in body.values():
        if isinstance(value, dict):
            inner = _extract_upload_token(value)
            if inner:
                return inner
    return None


def extract_message_id(resp: Dict[str, Any]) -> Optional[str]:
    msg = resp.get("message") if isinstance(resp, dict) else None
    if isinstance(msg, dict):
        mid = (msg.get("body") or {}).get("mid") or msg.get("message_id")
        if mid:
            return str(mid)
    if isinstance(resp, dict) and resp.get("message_id"):
        return str(resp["message_id"])
    return None
