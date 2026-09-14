"""Адаптер платформы MAX для гейтвея Hermes Agent."""
import contextlib
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx as _httpx
from gateway.config import Platform
from gateway.platforms._shared import get_scoped_secret
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
)
from gateway.platforms.event import MessageEvent, MessageType

from .interactive import InteractiveDispatcher
from .markdown import comment_markdown, sanitize_markdown
from .max_api import MaxApiError, MaxClient
from .uploads import Uploader

logger = logging.getLogger(__name__)

GATEWAY_COMMANDS = [
    ("new", "Новая сессия"),
    ("status", "Статус"),
    ("stop", "Остановить"),
    ("queue", "Очередь"),
    ("model", "Выбор модели"),
    ("help", "Помощь"),
    ("geo", "Поделиться геопозицией"),
    ("contact", "Отправить контакт"),
    ("approve", "Одобрить команду"),
    ("deny", "Отклонить команду"),
    ("always", "Одобрять всегда"),
    ("cancel", "Отмена"),
    ("resume", "Продолжить сессию"),
    ("voice", "Голосовые режимы"),
]

_TRUTHY = {"1", "true", "yes"}
_DEFAULT_MARKER_KEY = "marker"

_DRAFT_MIN_INTERVAL = 1.0  # запас к лимиту MAX «2 правки/сек на чат»

_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # RAM-кап на скачивание вложений (MAX допускает файлы до 4 ГБ)

_GROUP_ISOLATION_PROMPT = (
    "Ты отвечаешь в ГРУППОВОМ чате MAX, а не в личном диалоге. "
    "Опирайся только на текущую беседу группы и прямые запросы участников; "
    "не упоминай и не используй личные детали, заметки и историю из личных "
    "диалогов пользователя. Отвечай по существу вопроса.")

# текстовая навигация страниц в ответах ядра: «/commands 2», «/help 3»
_PAGE_NAV_RE = re.compile(r"/(commands|help)\s+(\d+)")


def _env_or_extra(extra: dict, env: str, key: str, default: Any = None) -> Any:
    return get_scoped_secret(env, "") or extra.get(key, default)


def _marker_file() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / "maxbot_marker"


_MEDIA_EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav",
    "video/mp4": ".mp4",
}

# Консервативный сниф магических байтов: только однозначные сигналы.
# Расширение — не граница доверия (контент всё равно недоверенный),
# это лишь подсказка парсерам агента; не распознали — честный .bin.
_MAGIC_EXT = (
    (b"%PDF", ".pdf"),
    (b"PK\x03\x04", ".zip"),  # docx/xlsx тоже zip-контейнеры
    (b"OggS", ".ogg"),
    (b"ID3", ".mp3"),
    (b"7z\xbc\xaf\x27\x1c", ".7z"),
    (b"Rar!\x1a\x07", ".rar"),
    (b"\x1f\x8b", ".gz"),
)


def _sniff_ext(data: bytes) -> str:
    for magic, ext in _MAGIC_EXT:
        if data.startswith(magic):
            return ext
    if data[4:8] == b"ftyp":  # mp4/mov: size(4B) + 'ftyp'
        return ".mp4"
    return ".bin"


def _file_ext(filename) -> str:
    """Расширение из имени файла MAX-вложения; без расширения — .bin."""
    name = str(filename or "")
    if "." not in name:
        return ".bin"
    return "." + name.rsplit(".", 1)[-1].lower()


_EXT_TO_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".wav": "audio/wav",
    ".mp4": "video/mp4",
}


def _mime_for(path: str, default: str) -> str:
    """MIME по расширению скачанного файла — ядро ждёт настоящие типы в media_types."""
    return _EXT_TO_MIME.get(_file_ext(os.path.basename(path)), default)

def _greeting_keyboard() -> list:
    from .interactive import keyboard_attachment
    return [keyboard_attachment([[
        {"type": "message", "text": "🆕 Новая сессия", "payload": "/new"},
        {"type": "message", "text": "📋 Статус", "payload": "/status"},
        {"type": "message", "text": "ℹ️ Помощь", "payload": "/help"},
    ]])]


_GREETING = (
    "👋 Привет! Я Hermes-агент в MAX.\n"
    "💬 Напишите вопрос, отправьте фото или файл — я отвечу.\n"
    "⌨️ Команды: /new, /status, /model, /help"
)


def _vcf_field(vcf: str, name: str) -> str:
    m = re.search(rf"^{name}[^\r\n:]*:(.+)$", vcf or "", re.MULTILINE)
    return m.group(1).strip() if m else ""


class MaxAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 4000

    def __init__(self, config, client: Optional[MaxClient] = None, transport=None):
        super().__init__(config=config, platform=Platform("max"))
        extra = getattr(config, "extra", None) or {}
        self._extra = extra
        self._token = _env_or_extra(extra, "MAX_ACCESS_TOKEN", "access_token")
        self._api_base = _env_or_extra(extra, "MAX_API_BASE", "api_base")
        self._updates_mode = str(_env_or_extra(extra, "MAX_UPDATES_MODE", "updates_mode", "polling")).lower()
        _rc = _env_or_extra(extra, "MAX_REGISTER_COMMANDS", "register_commands", "1")
        self._register_commands = str(_rc).lower() not in {"0", "false", "no"}
        self._bot_user_id: Optional[int] = None
        self._mention_re = None  # строится в connect(): упоминание ссылки бота
        self._username_re = None  # строится в connect(): "@username" в группах
        self._bot_username = ""
        self._client = client
        self._transport = transport
        self._uploader: Optional[Uploader] = None
        self._lock_identity: Optional[str] = None
        self._marker = extra.get(_DEFAULT_MARKER_KEY) or self._load_marker()
        _gi = str(_env_or_extra(extra, "MAX_GROUP_ISOLATION", "group_isolation", "true")).lower()
        self._group_isolation = _gi not in {"0", "false", "no"}
        self._interactive = None  # Task 11: interactive-обвязка
        self._drafts: Dict[Tuple[str, int], Dict[str, Any]] = {}
        self._chat_users: Dict[str, Tuple[str, str]] = {}
        # post_id -> chat_id канала (сессия ветки комментариев)
        self._comment_posts: Dict[str, int] = {}  # Task 12: streaming-превью

    # ── фабрики для подмены в тестах ──
    def _make_client(self) -> MaxClient:
        _dlp = str(_env_or_extra(self._extra, "MAX_DISABLE_LINK_PREVIEW",
                                 "disable_link_preview", "true")).lower()
        return MaxClient(self._token, base_url=self._api_base or None,
                         disable_link_preview=_dlp not in {"0", "false", "no"})

    def _make_transport(self):
        from .transports import PollingTransport
        return PollingTransport(self._client, initial_marker=self._marker,
                                on_marker=self._persist_marker)

    @staticmethod
    def _load_marker() -> Optional[int]:
        """Маркер из файла персиста (переживает рестарт гейтвея)."""
        with contextlib.suppress(Exception):
            return int((_marker_file().read_text().strip() or "0")) or None
        return None

    def _persist_marker(self, marker: int) -> None:
        self._marker = marker
        extra = getattr(self.config, "extra", None)
        if extra is not None:
            extra[_DEFAULT_MARKER_KEY] = marker
        path = _marker_file()
        with contextlib.suppress(Exception):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(str(marker))
            os.replace(tmp, path)

    @property
    def name(self) -> str:
        return "MAX"

    async def _fake_connect_for_tests(self, client) -> None:  # без lock/команд
        self._client = client
        self._uploader = Uploader(client)
        self._bot_user_id = 999

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self._token:
            logger.error("max: MAX_ACCESS_TOKEN не задан")
            return self._fail("config_missing", "MAX_ACCESS_TOKEN обязателен", retryable=False)
        self._client = self._client or self._make_client()
        try:
            me = await self._client.get_me()
        except MaxApiError as exc:
            return self._fail("max_auth" if exc.status == 401 else "max_connect",
                              str(exc), retryable=exc.status != 401)
        self._bot_user_id = me.user_id
        self._mention_re = re.compile(rf"\[([^\]]*)\]\(max://user/{me.user_id}\)")
        self._bot_username = (me.username or "").lower().lstrip("@")
        self._username_re = (re.compile(rf"(?<![\w@])@{re.escape(self._bot_username)}\b")
                             if self._bot_username else None)
        # token lock: два профиля не съедают один токен
        try:
            from gateway.status import acquire_scoped_lock
            identity = hashlib.sha1(self._token.encode()).hexdigest()[:16]
            ok, _ = acquire_scoped_lock("max", identity)
            if not ok:
                return self._fail("lock_conflict", "токен MAX занят другим профилем", retryable=False)
            self._lock_identity = identity
        except ImportError:
            pass
        self._uploader = Uploader(self._client)
        self._interactive = InteractiveDispatcher(self)
        try:
            if self._updates_mode == "webhook":
                url = _env_or_extra(self._extra, "MAX_WEBHOOK_URL", "webhook_url")
                if not url:
                    self._fail("config_missing", "webhook-режим требует MAX_WEBHOOK_URL", retryable=False)
                    await self.disconnect()
                    return False
                from .transports import WebhookTransport
                self._transport = self._transport or WebhookTransport(
                    self._client, url=url,
                    port=int(_env_or_extra(self._extra, "MAX_WEBHOOK_PORT", "webhook_port", 8443)),
                    secret=_env_or_extra(self._extra, "MAX_WEBHOOK_SECRET", "webhook_secret") or None,
                    update_types=["message_created", "message_callback", "bot_started"])
            else:
                self._transport = self._transport or self._make_transport()
            await self._transport.start(self._handle_update)
        except Exception:
            await self.disconnect()
            raise
        if self._register_commands:
            try:
                await self._client.set_commands(
                    [{"name": name, "description": descr} for name, descr in GATEWAY_COMMANDS])
            except Exception as e:
                logger.warning("max: не удалось зарегистрировать команды: %s", e)
        self._mark_connected()
        logger.info("max: подключен как %s (user_id=%s, режим %s)", me.name, me.user_id, self._updates_mode)
        return True

    def _fail(self, code: str, message: str, *, retryable: bool) -> bool:
        self._set_fatal_error(code, message, retryable=retryable)
        return False

    async def disconnect(self) -> None:
        if self._transport:
            with contextlib.suppress(Exception):
                await self._transport.stop()
            self._transport = None
        if self._lock_identity:
            with contextlib.suppress(Exception):
                from gateway.status import release_scoped_lock
                release_scoped_lock("max", self._lock_identity)
            self._lock_identity = None
        if self._client:
            with contextlib.suppress(Exception):
                await self._client.close()
        self._mark_disconnected()

    # ── outbound ──
    @staticmethod
    def _segment(text: str, limit: int = 3900) -> List[str]:
        parts: List[str] = []
        while len(text) > limit:
            cut = text.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            parts.append(text[:cut].rstrip())
            text = text[cut:].lstrip()
        if text.strip():
            parts.append(text.strip())
        return parts or [""]

    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="not connected")
        await self._cleanup_drafts(chat_id)  # Task 12: удаляем streaming-превью
        last_mid: Optional[str] = None
        # сессия ветки комментариев канала: ответ уходит комментарием к посту
        if str(chat_id) in self._comment_posts:
            try:
                for chunk in self._segment(comment_markdown(content)):
                    last_mid = await self._client.post_comment(str(chat_id), chunk)
            except MaxApiError as exc:
                return SendResult(success=False, error=str(exc))
            return SendResult(success=True, message_id=last_mid)
        try:
            for chunk in self._segment(sanitize_markdown(content)):
                last_mid = await self._client.send_message(
                    int(chat_id), chunk, reply_to_mid=reply_to)
        except MaxApiError as exc:
            return SendResult(success=False, error=str(exc))
        # текстовая навигация ядра («/commands 2») — дублируем кнопками-страницами
        if self._interactive and _PAGE_NAV_RE.search(content):
            cmd = _PAGE_NAV_RE.search(content).group(1)
            header = re.search(r"(\d+)\s*/\s*(\d+)", content[:250])  # «страница 3/5»
            total = int(header.group(2)) if header else 0
            if total > 1:
                pages = list(range(1, total + 1))[:14]
            else:
                pages = sorted({1, *(int(p) for c, p in _PAGE_NAV_RE.findall(content) if c == cmd)})
            if len(pages) > 1:
                with contextlib.suppress(Exception):
                    await self._interactive.send_page_nav(chat_id, cmd, pages)
        return SendResult(success=True, message_id=last_mid)

    async def on_page_nav(self, cb) -> None:
        """Нажатие кнопки-страницы (payload pg:<chat_id>:<cmd>:<page>): синтезируем команду."""
        parts = cb.payload.split(":")
        if len(parts) != 4:
            return
        _, chat_id, cmd, page = parts
        user_id, user_name = self._chat_users.get(str(chat_id), ("", ""))
        chat_type = "dm" if str(chat_id).isdigit() else "group"
        source = self.build_source(
            chat_id=str(chat_id), chat_name=str(chat_id), chat_type=chat_type,
            user_id=user_id, user_name=user_name)
        event = MessageEvent(text=f"/{cmd} {page}", message_type=MessageType.TEXT,
                             source=source, message_id=None)
        await self.handle_message(event)

    # ── streaming-превью (Task 12) ──
    def supports_draft_streaming(self, chat_type=None, metadata=None, chat_id=None) -> bool:
        return True

    def prefers_fresh_final_streaming(self, content=None, metadata=None) -> bool:
        return True

    async def send_draft(self, chat_id: str, draft_id: int, content: str,
                         metadata: Optional[Dict[str, Any]] = None) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="not connected")
        key = (str(chat_id), int(draft_id))
        entry = self._drafts.get(key)
        now = time.monotonic()
        preview = sanitize_markdown(content)[:3900] + " ▌"
        if entry is None:
            try:
                mid = await self._client.send_message(int(chat_id), preview, notify=False)
            except MaxApiError as exc:
                return SendResult(success=False, error=str(exc))
            # «last» в прошлом: первая правка идёт сразу, троттлер меряет интервал между PUT
            self._drafts[key] = {"message_id": mid, "last": now - _DRAFT_MIN_INTERVAL}
            return SendResult(success=True, message_id=None)
        if now - entry["last"] < _DRAFT_MIN_INTERVAL:
            return SendResult(success=True, message_id=None)  # скип промежуточного чанка
        try:
            await self._client.edit_message(entry["message_id"], preview)
            entry["last"] = now
        except MaxApiError as exc:
            if 400 <= exc.status < 500 and exc.status != 429:
                self._drafts.pop(key, None)  # финал доедет обычным send()
        return SendResult(success=True, message_id=None)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if not self._client:
            return
        with contextlib.suppress(Exception):  # в DM может не работать — молча
            await self._client.chat_action(int(chat_id), "typing_on")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        try:
            chat = await self._client.get_chat(int(chat_id))
            return {"name": chat.get("title") or chat.get("name") or chat_id,
                    "type": "group" if chat.get("type") in ("chat", "channel") else "dm",
                    "chat_id": chat_id}
        except Exception:
            return {"name": chat_id, "type": "dm", "chat_id": chat_id}

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        try:
            return await self._client.delete_message(message_id)
        except Exception:
            return False

    async def _send_attachment(self, chat_id: str, path: str, kind: str,
                               caption: Optional[str]) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="not connected")
        # индикатор «отправляет фото/видео/аудио/файл…» перед передачей
        with contextlib.suppress(Exception):
            await self._client.chat_action(
                int(chat_id), {"image": "sending_photo", "video": "sending_video",
                               "audio": "sending_audio", "file": "sending_file"}.get(kind, "typing_on"))
        try:
            token = await self._uploader.upload(path, kind)
            att = [{"type": kind, "payload": {"token": token}}]
            mid = await self._client.send_message(
                int(chat_id), sanitize_markdown(caption) if caption else "", attachments=att)
        except MaxApiError as exc:
            return SendResult(success=False, error=str(exc))
        return SendResult(success=True, message_id=mid)

    async def send_image(self, chat_id: str, image_url: str,
                         caption: Optional[str] = None, metadata=None, **kwargs) -> SendResult:
        """Отправить картинку по URL — фетчит сервер MAX (SSRF-безопасно)."""
        if not self._client:
            return SendResult(success=False, error="not connected")
        try:
            mid = await self._client.send_image_by_url(
                int(chat_id), str(image_url), sanitize_markdown(caption) if caption else "")
        except MaxApiError as exc:
            return SendResult(success=False, error=str(exc))
        return SendResult(success=True, message_id=mid)

    async def send_image_file(self, chat_id, image_path, caption=None, reply_to=None,
                              metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, image_path, "image", caption)

    async def send_document(self, chat_id, file_path, caption=None, file_name=None,
                            reply_to=None, metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, file_path, "file", caption)

    async def send_voice(self, chat_id, audio_path, caption=None, reply_to=None,
                         metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, audio_path, "audio", caption)

    async def send_video(self, chat_id, video_path, caption=None, reply_to=None,
                         metadata=None, **kwargs) -> SendResult:
        return await self._send_attachment(chat_id, video_path, "video", caption)

    # ── интерактивные кнопки: делегирование диспетчеру ──
    async def _send_exec_approval_prompt(self, prompt) -> "SendResult":
        """Контракт hermes 0.21.2: без переопределения этого hook'а ядро шлёт текстовый /approve."""
        if self._interactive:
            return await self._interactive.send_exec_approval_prompt(prompt)
        return await super()._send_exec_approval_prompt(prompt)

    async def send_slash_confirm(self, chat_id, title, message, session_key, confirm_id,
                                 metadata=None) -> "SendResult":
        if self._interactive:
            return await self._interactive.send_slash_confirm(
                chat_id, title, message, session_key, confirm_id, metadata=metadata)
        return await super().send_slash_confirm(
            chat_id, title, message, session_key, confirm_id, metadata=metadata)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key,
                           metadata=None) -> "SendResult":
        if self._interactive:
            return await self._interactive.send_clarify(
                chat_id, question, choices, clarify_id, session_key, metadata=metadata)
        return await super().send_clarify(
            chat_id, question, choices, clarify_id, session_key, metadata=metadata)

    async def send_choice_picker(self, chat_id, title, choices, session_key,
                                 on_choice_selected, metadata=None) -> "SendResult":
        if self._interactive:
            return await self._interactive.send_choice_picker(
                chat_id, title, choices, session_key, on_choice_selected, metadata=metadata)
        return await super().send_choice_picker(
            chat_id, title, choices, session_key, on_choice_selected, metadata=metadata)

    async def send_model_picker(self, chat_id=None, providers=None, current_model="",
                                current_provider="", session_key="", on_model_selected=None,
                                metadata=None) -> "SendResult":
        """Пикер /model: если интерактив недоступен — SendResult(False), ядро уйдёт в текст."""
        if self._interactive and on_model_selected:
            return await self._interactive.send_model_picker(
                chat_id, providers, current_model, current_provider, session_key,
                on_model_selected, metadata=metadata)
        return SendResult(success=False, error="интерактив недоступен")

    # api_* обёртки, которые использует диспетчер:
    async def api_send(self, chat_id, text, attachments=None):
        return await self._client.send_message(int(chat_id), text, attachments=attachments)

    async def api_answer(self, callback_id, text=None):
        return await self._client.answer_callback(callback_id, text)

    async def api_edit(self, message_id, text, attachments=None):
        return await self._client.edit_message(message_id, text, attachments=attachments)

    async def _cleanup_drafts(self, chat_id: str) -> None:
        for key in [k for k in self._drafts if k[0] == str(chat_id)]:
            entry = self._drafts.pop(key)
            with contextlib.suppress(Exception):
                await self._client.delete_message(entry["message_id"])

    def _remember_post_channel(self, post_id: str, channel_id: int) -> None:
        """Реестр пост→канал: память комментариев канала — общая на весь канал."""
        import json as _json
        try:
            from hermes_constants import get_hermes_home
            d = get_hermes_home() / "maxbot-chat-memory"
            d.mkdir(parents=True, exist_ok=True)
            f = d / "_channel_posts.json"
            reg = {}
            try:
                reg = _json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
            if reg.get(post_id) != channel_id:
                reg[post_id] = channel_id
                tmp = f.with_suffix(".tmp")
                tmp.write_text(_json.dumps(reg, ensure_ascii=False), encoding="utf-8")
                tmp.replace(f)
        except Exception:
            logger.debug("max: реестр пост→канал не обновлён", exc_info=True)

    async def _on_comment(self, update) -> None:
        """Комментарий к посту канала: сессия = ветка поста (chat_id = post_id)."""
        raw = (update.raw.get("message") or {})
        recipient = raw.get("recipient") or {}
        post_id = recipient.get("post_id")
        if not post_id:
            return
        channel_id = int(recipient.get("chat_id") or 0)
        self._comment_posts[str(post_id)] = channel_id
        self._remember_post_channel(str(post_id), channel_id)
        msg = update.message
        author = (msg.sender.name or f"id{msg.sender.user_id}") if msg.sender else "канал"
        text = msg.body.text or ""
        if not text.strip():
            return
        source = self.build_source(chat_id=str(post_id), chat_name=f"канал:{channel_id}/пост:{post_id}",
                                   chat_type="group", user_id=str(msg.sender.user_id) if msg.sender else "",
                                   user_name=author)
        event = MessageEvent(text=f"[комментарий] {author}: {text}", message_type=MessageType.TEXT,
                             source=source, message_id=msg.body.mid, raw_message=raw)
        await self.handle_message(event)

    def _purge_chat_state(self, chat_id: str) -> None:
        """bot_removed/dialog_removed: чистим кэши чата."""
        self._chat_users.pop(chat_id, None)
        if self._interactive:
            self._interactive.picker_state.pop(chat_id, None)
            self._interactive.model_picker_state.pop(chat_id, None)

    async def _handle_update(self, update) -> None:
        try:
            if update.update_type == "message_created" and update.message:
                await self._on_message(update.message)
            elif update.update_type == "message_callback" and update.callback:
                logger.info("max: message_callback raw=%s", str(update.callback.raw)[:400])
                if self._interactive:
                    await self._interactive.dispatch(update.callback)
            elif update.update_type == "bot_started":
                chat_id = update.chat_id
                if chat_id:
                    with contextlib.suppress(Exception):
                        await self._client.send_message(
                            int(chat_id), _GREETING, attachments=[_greeting_keyboard()],
                            notify=True)
            elif update.update_type == "comment_created" and update.message:
                await self._on_comment(update)
            elif update.update_type in ("bot_removed", "dialog_removed"):
                if update.chat_id:
                    self._purge_chat_state(str(update.chat_id))
            else:
                # наблюдаемость: что MAX реально присылает (группы, bot_added, упоминания и т.п.)
                logger.info("max: апдейт %s пропущен (chat_id=%s user=%s marker=%s) raw=%s",
                            update.update_type, update.chat_id,
                            update.user.user_id if update.user else None, update.marker,
                            str(update.raw)[:400])
        except Exception:
            logger.exception("max: ошибка обработки апдейта %s", update.update_type)

    async def _on_message(self, msg) -> None:
        if msg.sender and msg.sender.user_id == self._bot_user_id:
            return  # своё сообщение
        if msg.chat_type == "channel":
            return
        # /geo и /contact — локальные команды плагина: шлём request-кнопки MAX
        stripped = (msg.body.text or "").strip().lower().lstrip("/").split(maxsplit=1)[0] \
            if (msg.body.text or "").strip() else ""
        if stripped in {"geo", "contact"} and self._interactive:
            with contextlib.suppress(Exception):
                await self._interactive.send_request_buttons(
                    str(msg.chat_id), "geo" if stripped == "geo" else "contact")
            return
        if msg.sender:
            self._chat_users[str(msg.chat_id)] = (
                str(msg.sender.user_id), (msg.sender.name or ""))
        chat_type = "dm" if msg.chat_type == "dialog" else "group"
        text = msg.body.text or ""
        if chat_type == "group":
            passed, text = await self._group_gate(msg, text)
            if not passed:
                logger.info(
                    "max: групповое сообщение chat=%s от user=%s без упоминания бота — пропущено "
                    "(text=%r atts=%s)",
                    msg.chat_id, msg.sender.user_id if msg.sender else None,
                    (text or "")[:120], [a.type for a in msg.body.attachments])
                return
        media_paths, media_types, extra_text = await self._collect_media(msg)
        combined = "\n".join(x for x in (text.strip(), extra_text) if x)
        if not combined and not media_paths:
            return
        source = self.build_source(
            chat_id=str(msg.chat_id), chat_name=str(msg.chat_id), chat_type=chat_type,
            user_id=str(msg.sender.user_id) if msg.sender else "",
            user_name=(msg.sender.name if msg.sender else "") or "")
        reply_mid = None
        if msg.link and msg.link.get("type") == "reply":
            reply_mid = str(msg.link.get("mid") or "") or None
        event = MessageEvent(
            text=combined or " ",
            message_type=MessageType.TEXT,
            source=source, raw_message=msg.raw, message_id=msg.body.mid,
            media_urls=media_paths, media_types=media_types, reply_to_message_id=reply_mid)
        if chat_type == "group" and self._group_isolation:
            event.channel_prompt = _GROUP_ISOLATION_PROMPT
        await self.handle_message(event)

    async def _group_gate(self, msg, text: str):
        if self._mention_re and self._mention_re.search(text):
            return True, self._mention_re.sub("", text).strip()
        # в группах MAX упоминает бота просто "@username" текстом
        if self._username_re and self._username_re.search(text):
            cleaned = self._username_re.sub("", text).strip()
            return True, cleaned or text
        if msg.link and msg.link.get("type") == "reply" and msg.link.get("mid"):
            with contextlib.suppress(Exception):
                replied = await self._client.get_message(str(msg.link["mid"]))
                if replied and replied.sender and replied.sender.user_id == self._bot_user_id:
                    return True, text
        return False, text

    async def _refreshed_attachments(self, msg) -> list:
        """Апдейты MAX приходят с пустым payload у location/contact — догружаем полное сообщение."""
        needs_refresh = any(
            att.type in ("location", "contact", "share", "sticker") and not att.payload
            for att in msg.body.attachments)
        if not needs_refresh or not msg.body.mid:
            return msg.body.attachments
        with contextlib.suppress(Exception):
            fresh = await self._client.get_message(str(msg.body.mid))
            for fa in fresh.body.attachments:
                logger.info("max: догруженное вложение type=%s payload=%s",
                            fa.type, str(fa.payload)[:250])
            return fresh.body.attachments
        return msg.body.attachments

    async def _collect_media(self, msg):
        paths, types, texts = [], [], []
        attachments = await self._refreshed_attachments(msg)
        for att in attachments:
            logger.info("max: вложение type=%s payload=%s", att.type, str(att.payload)[:250])
            try:
                if att.type == "image":
                    if path := await self._download_cached(att, cache_image_from_bytes, ".jpg", _MEDIA_EXT_BY_MIME):
                        paths.append(path), types.append(_mime_for(path, "image/jpeg"))
                elif att.type == "audio":
                    if path := await self._download_cached(att, cache_audio_from_bytes, ".mp3", _MEDIA_EXT_BY_MIME):
                        # mime обязателен: ядро гоняет STT только по audio/* из media_types
                        paths.append(path), types.append(_mime_for(path, "audio/mpeg"))
                    if (tr := att.payload.get("transcription")):
                        texts.append(f"[расшифровка голосового] {tr}")
                elif att.type in ("file", "video"):
                    ext = _file_ext(att.payload.get("filename"))
                    if path := await self._download_cached(att, cache_document_from_bytes, ext, _MEDIA_EXT_BY_MIME, is_doc=True):
                        paths.append(path)
                        types.append(_mime_for(path, "video/mp4") if att.type == "video"
                                     else "application/octet-stream")
                    if att.type == "video" and att.payload.get("token"):
                        with contextlib.suppress(Exception):
                            info = await self._client.get_video_info(str(att.payload["token"]))
                            if info:
                                texts.append("[видео: %s]" % ", ".join(
                                    f"{k}={info[k]}" for k in ("duration", "size", "width", "height")
                                    if info.get(k) is not None))
                elif att.type == "sticker":
                    if (code := att.payload.get("code")):
                        from .state import remember_sticker
                        remember_sticker(code)  # агент сможет переотправить через max_sticker
                    # превью-картинка стикера (если доступна) — агент увидит её через vision
                    if path := await self._download_cached(att, cache_image_from_bytes, ".png",
                                                           _MEDIA_EXT_BY_MIME):
                        paths.append(path)
                        types.append("image/png")
                    texts.append("[стикер]")
                elif att.type == "share":
                    title = att.payload.get("title") or att.payload.get("url") or "шеринг"
                    texts.append(f"[шеринг] {title}")
                elif att.type == "contact":
                    vcf = att.payload.get("vcf_info") or ""
                    tel, fn = _vcf_field(vcf, "TEL"), _vcf_field(vcf, "FN")
                    if not fn:
                        info = att.payload.get("max_info") or {}
                        fn = " ".join(x for x in (
                            info.get("name") or info.get("first_name"),
                            info.get("last_name") or "") if x).strip()
                        if info.get("user_id"):
                            fn = f"{fn} (id {info['user_id']})".strip()
                    texts.append(f"[контакт] {fn} {tel}".strip())
                elif att.type == "location":
                    lat = att.payload.get("latitude") or att.payload.get("lat")
                    lon = (att.payload.get("longitude") or att.payload.get("lon")
                           or att.payload.get("lng"))
                    if lat is None and msg.body.mid:
                        # апдейт приходит без координат — догружаем полное сообщение
                        with contextlib.suppress(Exception):
                            fresh = await self._client.get_message(str(msg.body.mid))
                            for fa in fresh.body.attachments:
                                if fa.type == "location":
                                    lat = fa.payload.get("latitude") or fa.payload.get("lat")
                                    lon = (fa.payload.get("longitude") or fa.payload.get("lon")
                                           or fa.payload.get("lng"))
                    if lat is not None:
                        texts.append(
                            f"[геолокация] {lat}, {lon} "
                            f"( https://www.openstreetmap.org/?mlat={lat}&mlon={lon} )")
                    else:
                        # MAX Bot API не отдаёт координаты боту даже в полном сообщении
                        texts.append("[геолокация] пользователь поделился геопозицией "
                                     "(координаты недоступны через Bot API)")
            except Exception:
                logger.exception("max: вложение %s не обработано", att.type)
        return paths, types, "\n".join(texts)

    async def _download_cached(self, att, cache_fn, default_ext, mime_map=None, is_doc=False):
        url = att.payload.get("url") or att.payload.get("preview_url")
        if not url and (msg_mid := att.payload.get("mid")):
            with contextlib.suppress(Exception):
                fresh = await self._client.get_message(str(msg_mid))
                url = next((a.payload.get("url") for a in fresh.body.attachments
                            if a.type == att.type and a.payload.get("url")), None)
        if not url:
            return None
        async with _httpx.AsyncClient(timeout=60.0, follow_redirects=True) as hc:
            async with hc.stream("GET", str(url)) as resp:
                resp.raise_for_status()
                # RAM-кап: проверяем ДО чтения тела (content-length) и ВО ВРЕМЯ (chunked)
                declared = int(resp.headers.get("content-length") or 0)
                if declared > _MAX_DOWNLOAD_BYTES:
                    logger.warning("max: вложение %s (%d Б) больше капа %d Б — не скачиваем",
                                   att.type, declared, _MAX_DOWNLOAD_BYTES)
                    return None
                mime = resp.headers.get("content-type", "").split(";")[0].strip()
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _MAX_DOWNLOAD_BYTES:
                        logger.warning("max: вложение %s превысило кап при стриминге — прерываем",
                                       att.type)
                        return None
        data = bytes(buf)
        ext = mime_map.get(mime, default_ext) if mime_map else default_ext
        if ext == ".bin":
            ext = _sniff_ext(data)  # MAX не дал имя, content-type generic — смотрим магику
        if is_doc:
            return cache_fn(data, f"max_attachment{ext}")
        return cache_fn(data, ext)


def check_requirements() -> bool:
    return bool(get_scoped_secret("MAX_ACCESS_TOKEN", ""))


def validate_config(config) -> bool:
    extra = getattr(config, "extra", None) or {}
    return bool(get_scoped_secret("MAX_ACCESS_TOKEN", "") or extra.get("access_token"))


def is_connected(config) -> bool:
    return validate_config(config)
