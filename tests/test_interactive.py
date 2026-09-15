import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.interactive import InteractiveDispatcher, keyboard_attachment
from maxbot.models import parse_update


class FakeAdapterApi:
    """Только то, что нужно диспетчеру от адаптера."""

    def __init__(self):
        self.sent = {}       # chat_id -> последняя (text, attachments)
        self.answered = []
        self.edited = {}
        self._mid = 0

    async def api_send(self, chat_id, text, attachments=None):
        self._mid += 1
        self.sent[chat_id] = (text, attachments)
        return f"mid.{self._mid}"

    async def api_answer(self, callback_id, text=None):
        self.answered.append((callback_id, text))

    async def api_edit(self, message_id, text, attachments=None):
        self.edited[message_id] = (text, attachments)


def make_dispatcher():
    api = FakeAdapterApi()
    disp = InteractiveDispatcher(api)  # api — объект с api_send/api_answer/api_edit
    return disp, api


def _cb(payload, chat_id=100):
    # реальный message_callback MAX не содержит message — только user и payload
    return parse_update({
        "update_type": "message_callback",
        "callback": {"callback_id": "cb.9", "payload": payload,
                     "user": {"user_id": 1, "name": "Иван"}},
    }).callback


def test_keyboard_attachment_shape():
    att = keyboard_attachment([[{"type": "callback", "text": "Ок", "payload": "ea:100:once:1"}]])
    assert att["type"] == "inline_keyboard"
    assert att["payload"]["buttons"][0][0]["payload"] == "ea:100:once:1"


async def test_exec_approval_buttons_and_resolve(monkeypatch):
    import types

    from maxbot import interactive as I

    resolved = []
    monkeypatch.setattr(I, "_resolve_approval",
                        lambda session_key, choice: resolved.append((session_key, choice)) or 1)
    disp, api = make_dispatcher()
    prompt = types.SimpleNamespace(
        chat_id="100", session_key="sess:1", text="⚠️ rm -rf /tmp/x — опасно",
        actions=[("Разрешить", "once", "primary"), ("Отклонить", "deny", "danger")],
        command="rm -rf /tmp/x", description="dangerous command", smart_denied=False)
    await disp.send_exec_approval_prompt(prompt)
    text, atts = api.sent["100"]
    payloads = [b["payload"] for row in atts[0]["payload"]["buttons"] for b in row]
    assert "ea:100:once:1" in payloads and "ea:100:deny:1" in payloads
    assert "rm -rf /tmp/x" in text
    await disp.dispatch(_cb("ea:100:once:1"))
    assert resolved == [("sess:1", "once")]
    assert api.answered and api.edited["mid.1"][0]
    # кнопки исчезают: MAX удаляет вложения только по пустому массиву
    assert api.edited["mid.1"][1] == []


async def test_buttons_removed_after_picker_choice(monkeypatch):
    from maxbot import interactive as I

    async def fake_clarify(cid, response):
        return None

    monkeypatch.setattr(I, "_resolve_clarify", fake_clarify)
    disp, api = make_dispatcher()
    picked = []

    async def on_choice(chat_id, value):
        picked.append(value)
        return None

    await disp.send_choice_picker("100", "Модель:", [
        {"label": "большая", "value": "big"}, {"label": "малая", "value": "small"}],
        "sess:1", on_choice)
    await disp.dispatch(_cb("cp:100:0"))
    assert picked == ["big"]
    assert api.edited["mid.1"][1] == []  # клавиатура снята


async def test_clarify_other_keeps_buttons(monkeypatch):
    from maxbot import interactive as I

    marked = []
    monkeypatch.setattr(I, "_mark_clarify_awaiting", lambda cid: marked.append(cid))
    disp, api = make_dispatcher()
    await disp.send_clarify("100", "Какой формат?", ["A", "B"], "cl.1", "sess:1")
    await disp.dispatch(_cb("cl:100:cl.1:-1"))
    assert marked == ["cl.1"]
    # «Другое» НЕ закрывает уточнение — кнопки остаются (attachments не трогаем)
    assert api.edited["mid.1"][1] is None


async def test_exec_approval_smart_denied(monkeypatch):
    import types

    disp, api = make_dispatcher()
    prompt = types.SimpleNamespace(
        chat_id="100", session_key="s", text="⚠️ cmd",
        actions=[("Разрешить", "once", "primary"), ("Отклонить", "deny", "danger")],
        command="cmd", description="dangerous command", smart_denied=True)
    await disp.send_exec_approval_prompt(prompt)
    _, atts = api.sent["100"]
    payloads = [b["payload"] for row in atts[0]["payload"]["buttons"] for b in row]
    assert payloads == ["ea:100:once:1", "ea:100:deny:1"]


async def test_clarify_buttons_and_other(monkeypatch):
    from maxbot import interactive as I
    awaited = []
    monkeypatch.setattr(I, "_mark_clarify_awaiting", lambda *args: awaited.append(args))
    monkeypatch.setattr(I, "_resolve_clarify",
                        lambda cid, resp: awaited.append((cid, resp)) or None)
    disp, api = make_dispatcher()
    await disp.send_clarify("100", "Какой план?", ["быстро", "надёжно"], "cl.7", "sess:2")
    _, atts = api.sent["100"]
    payloads = [b["payload"] for row in atts[0]["payload"]["buttons"] for b in row]
    assert payloads == ["cl:100:cl.7:0", "cl:100:cl.7:1", "cl:100:cl.7:-1"]
    await disp.dispatch(_cb("cl:100:cl.7:-1"))
    assert awaited == [("cl.7",)]
    await disp.dispatch(_cb("cl:100:cl.7:1"))
    assert awaited[-1] == ("cl.7", "надёжно")


async def test_slash_confirm_and_picker(monkeypatch):
    from maxbot import interactive as I

    async def fake_resolve(sk, cid, ch):  # контракт ядра: async resolve
        return f"resolved:{ch}"

    monkeypatch.setattr(I, "_resolve_slash_confirm", fake_resolve)
    disp, api = make_dispatcher()
    await disp.send_slash_confirm("100", "/reload-mcp", "Перезагрузить?", "sess:3", "cf.1")
    await disp.dispatch(_cb("sc:100:cancel:cf.1"))
    assert api.edited["mid.1"][0].startswith("❌")
    assert api.sent["100"][0] == "resolved:cancel"  # результат ядра уходит в чат, не <coroutine>
    picked = []
    await disp.send_choice_picker(
        "100", "Модель", [{"value": "a", "label": "A", "is_current": False},
                          {"value": "b", "label": "B", "is_current": True}],
        "sess:4",
        on_choice_selected=lambda cid, v: picked.append((cid, v)) or _async_none())
    await disp.dispatch(_cb("cp:100:0"))
    assert picked == [("100", "a")]


async def test_picker_uses_core_callback_contract():
    async def on_selected(chat_id, value):
        return f"Модель: {value}"

    disp, api = make_dispatcher()
    await disp.send_choice_picker("100", "Модель",
                                  [{"value": "big", "label": "Big", "is_current": False}], "s", on_selected)
    await disp.dispatch(_cb("cp:100:0"))
    assert api.sent["100"][0] == "Модель: big"


async def test_picker_out_of_range_marks_stale_and_pops():
    calls = []
    disp, api = make_dispatcher()
    await disp.send_choice_picker("100", "Модель", [{"value": "big", "label": "Big"}], "s",
                                  on_choice_selected=lambda cid, v: calls.append((cid, v)) or None)
    await disp.dispatch(_cb("cp:100:5"))  # индекс за пределами списка
    assert api.edited["mid.1"][0] == "⌛ Вариант устарел"
    assert disp.picker_state == {}
    await disp.dispatch(_cb("cp:100:0"))  # повторное нажатие — уже обработано
    assert calls == []
    assert api.answered[-1] == ("cb.9", "⌛ Уже обработано")


def _async_none():
    import asyncio
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


async def test_model_picker_buttons_and_callback():
    disp, api = make_dispatcher()
    calls = []

    async def on_selected(chat_id, model_id, provider_slug):
        calls.append((chat_id, model_id, provider_slug))
        return f"Модель переключена: {model_id}"

    providers = [{"slug": "zai", "label": "Z.AI", "models": ["glm-5.3", "glm-5.2"]}]
    res = await disp.send_model_picker("100", providers, "glm-5.3", "zai", "sess:9", on_selected)
    assert res.success
    text, atts = api.sent["100"]
    rows = atts[0]["payload"]["buttons"]
    assert rows[0][0]["text"] == "✓ glm-5.3" and rows[0][0]["payload"] == "mp:100:0"
    assert rows[1][0]["text"] == "glm-5.2" and rows[1][0]["payload"] == "mp:100:1"
    await disp.dispatch(_cb("mp:100:1"))
    assert calls == [("100", "glm-5.2", "zai")]
    assert api.sent["100"][0] == "Модель переключена: glm-5.2"


async def test_greeting_button_runs_command_with_toast(monkeypatch):
    disp, api = make_dispatcher()
    ran = []

    async def fake_cmd(cb):
        ran.append(cb.payload)

    monkeypatch.setattr(disp.api, "on_greeting_cmd", fake_cmd, raising=False)
    await disp.dispatch(_cb("gc:100:new"))
    assert ran == ["gc:100:new"]
    assert api.answered and "⏳" in api.answered[0][1]
