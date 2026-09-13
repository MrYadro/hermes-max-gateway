"""Inline-кнопки MAX: approve/deny, slash-confirm, clarify, choice-picker."""
import contextlib
import inspect
import itertools
import logging
from typing import Any, Dict, List, Optional

from .markdown import sanitize_markdown
from .models import Callback

logger = logging.getLogger(__name__)


def keyboard_attachment(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"type": "inline_keyboard",
            "payload": {"buttons": [[{"type": "callback", **btn} for btn in row]
                                    for row in rows]}}


# ── обёртки над резолверами ядра (переопределяются в тестах) ──
def _resolve_approval(session_key: str, choice: str) -> int:
    from tools.approval import resolve_gateway_approval
    return resolve_gateway_approval(session_key, choice)


async def _resolve_slash_confirm(session_key: str, confirm_id: str, choice: str) -> Optional[str]:
    from tools.slash_confirm import resolve
    return await resolve(session_key, confirm_id, choice)


def _resolve_clarify(clarify_id: str, response: str) -> None:
    from tools.clarify_gateway import resolve_gateway_clarify
    return resolve_gateway_clarify(clarify_id, response)


def _mark_clarify_awaiting(clarify_id: str) -> None:
    from tools.clarify_gateway import mark_awaiting_text
    mark_awaiting_text(clarify_id)


_EA_LABELS = {"once": "✅ Одобрено (один раз)", "session": "✅ Одобрено — сессия",
              "always": "✅ Одобрить всегда", "deny": "❌ Отклонено"}
_SC_LABELS = {"once": "✅ Один раз", "always": "♾️ Всегда", "cancel": "❌ Отмена"}


class InteractiveDispatcher:
    def __init__(self, adapter_api):
        """adapter_api — объект с `await api_send(chat_id, text, attachments)`,
        `await api_answer(callback_id, text)`, `await api_edit(message_id, text, attachments)`."""
        self.api = adapter_api
        self._counter = itertools.count(1)
        self.approval_state: Dict[int, str] = {}
        self.slash_state: Dict[str, str] = {}
        self.clarify_state: Dict[str, str] = {}
        self.picker_state: Dict[str, dict] = {}
        self.model_picker_state: Dict[str, dict] = {}
        self._clarify_choices: Dict[str, Dict[int, str]] = {}

    # ── промпты ──
    async def send_exec_approval_prompt(self, prompt):
        """Контракт hermes 0.21.2: base.send_exec_approval собирает prompt и зовёт этот hook."""
        approval_id = next(self._counter)
        _ea_icons = {"once": "✅", "session": "🔁", "always": "♾️", "deny": "❌"}
        flat = [{"text": f"{_ea_icons.get(choice, '•')} {label}",
                 "payload": f"ea:{prompt.chat_id}:{choice}:{approval_id}"}
                for label, choice, _style in (prompt.actions or [])]
        if not flat:
            from gateway.platforms.base import SendResult
            return SendResult(success=False, error="Нет действий")
        rows = [flat[i:i + 2] for i in range(0, len(flat), 2)]
        mid = await self.api.api_send(prompt.chat_id, sanitize_markdown(prompt.text),
                                      [keyboard_attachment(rows)])
        self.approval_state[approval_id] = prompt.session_key
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_slash_confirm(self, chat_id, title, message, session_key, confirm_id,
                                 metadata=None):
        rows = [[{"text": "✅ Один раз", "payload": f"sc:{chat_id}:once:{confirm_id}"},
                 {"text": "♾️ Всегда", "payload": f"sc:{chat_id}:always:{confirm_id}"},
                 {"text": "❌ Отмена", "payload": f"sc:{chat_id}:cancel:{confirm_id}"}]]
        mid = await self.api.api_send(
            chat_id, sanitize_markdown(f"⚙️ *{title}*\n{message}"), [keyboard_attachment(rows)])
        self.slash_state[confirm_id] = session_key
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key,
                           metadata=None):
        rows, payloads = [], []
        for i, choice in enumerate(choices or []):
            rows.append([{"text": str(choice)[:64], "payload": f"cl:{chat_id}:{clarify_id}:{i}"}])
            payloads.append(f"cl:{clarify_id}:{i}")
        rows.append([{"text": "✍️ Другое", "payload": f"cl:{chat_id}:{clarify_id}:-1"}])
        mid = await self.api.api_send(chat_id, f"❓ {question}", [keyboard_attachment(rows)])
        self.clarify_state[clarify_id] = session_key
        self._clarify_choices[clarify_id] = {i: str(c) for i, c in enumerate(choices or [])}
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_choice_picker(self, chat_id, title, choices, session_key,
                                 on_choice_selected, metadata=None):
        rows = []
        for i, choice in enumerate(choices or []):
            label = str(choice.get("label") or choice.get("value") or "")
            if choice.get("is_current"):
                label = f"✓ {label}"
            rows.append({"text": label, "payload": f"cp:{chat_id}:{i}"})
        grid = [rows[i:i + 2] for i in range(0, len(rows), 2)]
        mid = await self.api.api_send(chat_id, str(title), [keyboard_attachment(grid)])
        self.picker_state[str(chat_id)] = {
            "choices": choices, "session_key": session_key,
            "on_choice_selected": on_choice_selected, "message_id": mid}
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_model_picker(self, chat_id, providers, current_model,
                                current_provider, session_key, on_model_selected,
                                metadata=None):
        """Плоский пикер моделей для /model: одна модель — один ряд (MAX ≤30 рядов)."""
        entries = []  # [(provider_slug, model_id, label)]
        for p in providers or []:
            slug = str(p.get("slug") or "")
            for m in (p.get("models") or []):
                model_id = str(m[0] if isinstance(m, (tuple, list)) else m)
                entries.append((slug, model_id, model_id))
                if len(entries) >= 28:  # запас под ряд «ещё» и лимит 30 рядов MAX
                    break
            if len(entries) >= 28:
                break
        if not entries:
            from gateway.platforms.base import SendResult
            return SendResult(success=False, error="Нет доступных моделей")
        rows = []
        for i, (slug, model_id, _) in enumerate(entries):
            label = model_id[:60]
            if model_id == current_model and slug == current_provider:
                label = f"✓ {label}"
            rows.append([{"text": label, "payload": f"mp:{chat_id}:{i}"}])
        if (total := sum(len(p.get("models") or []) for p in providers or [])) > len(entries):
            rows.append([{"text": f"🔎 …ещё {total - len(entries)} — уточните: /model <имя>",
                          "payload": "mp:-1"}])
        mid = await self.api.api_send(
            chat_id, "🤖 Выберите модель:", [keyboard_attachment(rows)])
        self.model_picker_state[str(chat_id)] = {
            "entries": entries, "session_key": session_key,
            "on_model_selected": on_model_selected, "message_id": mid}
        from gateway.platforms.base import SendResult
        return SendResult(success=True, message_id=mid)

    async def send_request_buttons(self, chat_id, kind: str):
        """Кнопки-запросы MAX: request_geo_location / request_contact (шаринг с данными)."""
        if kind == "geo":
            btn = {"type": "request_geo_location", "text": "📍 Отправить локацию"}
            text = "📍 Поделитесь геопозицией — я увижу координаты:"
        else:
            btn = {"type": "request_contact", "text": "👤 Отправить контакт"}
            text = "👤 Поделитесь контактом:"
        await self.api.api_send(chat_id, text, [keyboard_attachment([[btn]])])

    async def send_page_nav(self, chat_id, cmd: str, pages: list):
        """Кнопки-страницы для текстовой навигации ядра («/commands 2» и т.п.)."""
        flat = [{"text": f"📄 {p}", "payload": f"pg:{chat_id}:{cmd}:{p}"} for p in pages]
        rows = [flat[j:j + 7] for j in range(0, len(flat), 7)]  # MAX: до 7 кнопок в ряду
        await self.api.api_send(chat_id, "📄 Страницы:", [keyboard_attachment(rows)])

    # ── диспетчер нажатий ──
    async def dispatch(self, cb: Callback) -> None:
        payload = cb.payload or ""
        logger.info("max: callback payload=%r chat=%s user=%s", payload,
                    cb.message.chat_id if cb.message else None,
                    cb.message.sender.user_id if cb.message and cb.message.sender else None)
        try:
            if payload.startswith("ea:"):
                await self._on_exec_approval(cb)
            elif payload.startswith("sc:"):
                await self._on_slash_confirm(cb)
            elif payload.startswith("cl:"):
                await self._on_clarify(cb)
            elif payload.startswith("cp:"):
                await self._on_picker(cb)
            elif payload.startswith("mp:"):
                await self._on_model_picker(cb)
            elif payload.startswith("pg:"):
                await self.api.on_page_nav(cb)
        except Exception:
            logger.exception("max: callback %r не обработан", payload)

    async def _finish(self, cb: Callback, toast: str, edit_text: str,
                      edit_attachments: Optional[list] = None) -> None:
        with contextlib.suppress(Exception):
            await self.api.api_answer(cb.callback_id, toast)
        if cb.message and cb.message.body.mid:
            with contextlib.suppress(Exception):
                await self.api.api_edit(cb.message.body.mid, edit_text, edit_attachments)

    async def _on_exec_approval(self, cb: Callback) -> None:
        parts = (cb.payload.split(":") + ["", "", "", ""])[:4]
        _, _chat, choice, _id = parts
        session_key = self.approval_state.pop(int(_id), None) if _id.isdigit() else None
        if not session_key:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Подтверждение уже обработано")
            return
        count = _resolve_approval(session_key, choice)
        label = _EA_LABELS.get(choice, "Готово") if count else "⌛ Истекло ожидание"
        user = cb.message.sender.name if cb.message and cb.message.sender else ""
        await self._finish(cb, label, f"{label}" + (f" — {user}" if user else ""))

    async def _on_slash_confirm(self, cb: Callback) -> None:
        _, _chat, choice, confirm_id = (cb.payload.split(":") + ["", "", ""])[:4]
        session_key = self.slash_state.pop(confirm_id, None)
        if not session_key:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        result_text = await _resolve_slash_confirm(session_key, confirm_id, choice)
        await self._finish(cb, _SC_LABELS.get(choice, "Готово"),
                           _SC_LABELS.get(choice, "Готово"))
        if result_text:
            with contextlib.suppress(Exception):
                await self.api.api_send(_chat or "", sanitize_markdown(str(result_text)))

    async def _on_clarify(self, cb: Callback) -> None:
        _, _chat, clarify_id, idx_s = (cb.payload.split(":") + ["", "", ""])[:4]
        if idx_s == "-1":
            # «Другое» не закрывает уточнение: пользователь может нажать кнопку после
            if not self.clarify_state.get(clarify_id):
                await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
                return
            _mark_clarify_awaiting(clarify_id)
            await self._finish(cb, "✍️ Напишите ответ", "✍️ Напишите свой ответ следующим сообщением")
            return
        session_key = self.clarify_state.pop(clarify_id, None)
        if not session_key:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        # восстановить текст выбора из кнопок исходного сообщения нельзя —
        # резолвер ядра принимает текст; берём из payload-таблицы, сохранённой при отправке
        choice_text = self._clarify_choices.get(clarify_id, {}).get(int(idx_s), str(idx_s))
        _resolve_clarify(clarify_id, choice_text)
        await self._finish(cb, f"✅ Выбрано: {choice_text}", f"✅ Выбрано: {choice_text}")

    async def _on_picker(self, cb: Callback) -> None:
        try:
            idx = int(cb.payload.split(":")[2] if len(cb.payload.split(":")) > 2 else "0")
        except ValueError:
            return
        chat_id = cb.payload.split(":")[1] if len(cb.payload.split(":")) > 1 else ""
        state = self.picker_state.get(chat_id)
        if not state:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        choices = state.get("choices") or []
        if not (0 <= idx < len(choices)):
            self.picker_state.pop(chat_id, None)
            await self._finish(cb, "⌛ Вариант устарел", "⌛ Вариант устарел")
            return
        choice = choices[idx]
        label = str(choice.get("label") or choice.get("value") or "")
        handler = state.get("on_choice_selected")
        result_text = None
        if handler:
            result = handler(chat_id, choice.get("value"))
            if inspect.isawaitable(result):
                result_text = await result
            else:
                result_text = result
        self.picker_state.pop(chat_id, None)
        await self._finish(cb, f"☑️ {label}", f"☑️ Выбрано: {label}")
        if result_text and cb.message:
            with contextlib.suppress(Exception):
                await self.api.api_send(chat_id, sanitize_markdown(str(result_text)))

    async def _on_model_picker(self, cb: Callback) -> None:
        try:
            idx = int(cb.payload.split(":")[2] if len(cb.payload.split(":")) > 2 else "0")
        except ValueError:
            return
        chat_id = cb.payload.split(":")[1] if len(cb.payload.split(":")) > 1 else ""
        state = self.model_picker_state.get(chat_id)
        if not state:
            await self._finish(cb, "⌛ Уже обработано", "⌛ Уже обработано")
            return
        entries = state.get("entries") or []
        if not (0 <= idx < len(entries)):
            self.model_picker_state.pop(chat_id, None)
            await self._finish(cb, "⌛ Вариант устарел", "⌛ Вариант устарел — введите /model <имя>")
            return
        provider_slug, model_id, _ = entries[idx]
        handler = state.get("on_model_selected")
        result_text = None
        if handler:
            result = handler(chat_id, model_id, provider_slug)
            if inspect.isawaitable(result):
                result_text = await result
            else:
                result_text = result
        self.model_picker_state.pop(chat_id, None)
        await self._finish(cb, f"☑️ {model_id}", f"☑️ Модель: {model_id}")
        if result_text:
            with contextlib.suppress(Exception):
                await self.api.api_send(chat_id, sanitize_markdown(str(result_text)))
