"""Переключалка профилей: карта чат→профиль, список профилей."""
import json

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot import profile_switch


def test_map_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "work")
    assert profile_switch.load_map(tmp_path) == {"500": "work"}
    assert json.loads(
        (tmp_path / "maxbot-chat-memory" / "_chat_profiles.json").read_text())["500"] == "work"


def test_map_unknown_profile_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "ghost")
    assert profile_switch.load_map(tmp_path) == {}


def test_available_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work", "family"})
    assert profile_switch.available_profiles() == ["default", "family", "work"]


def test_profile_description_from_soul():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(profile_switch, "_soul_text",
                        lambda name: "# Заголовок\n\nПомощник по коду и DevOps\n\nПодробности ниже")
    assert profile_switch.profile_description("work") == "Помощник по коду и DevOps"
    long = "Очень длинное описание профиля, которое точно не влезет в лимит кнопки"
    monkeypatch.setattr(profile_switch, "_soul_text", lambda name: long)
    assert len(profile_switch.profile_description("work")) <= 48
    monkeypatch.setattr(profile_switch, "_soul_text", lambda name: "")
    assert profile_switch.profile_description("work") == ""
    monkeypatch.undo()


def _upd_text(text, chat_id=500, chat_type="dialog"):
    from maxbot.models import parse_update
    return parse_update({
        "update_type": "message_created", "marker": 1,
        "message": {"body": {"mid": "m1", "text": text, "attachments": []},
                    "recipient": {"chat_id": chat_id, "chat_type": chat_type},
                    "sender": {"user_id": 42, "name": "Петя"}, "timestamp": 1},
    })


class _Runner:
    class config:
        multiplex_profiles = True


class _RunnerOff:
    class config:
        multiplex_profiles = False


async def test_assistant_command_sends_picker(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _Interactive:
        def __init__(self):
            self.calls = []

        async def send_choice_picker(self, chat_id, title, choices, session_key,
                                     on_choice_selected, metadata=None):
            self.calls.append((chat_id, title, choices, on_choice_selected))
            return "m.pick"

    inter = _Interactive()
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr(profile_switch, "_soul_text", lambda name: "")
    await adapter._handle_update(_upd_text("/assistant"))
    assert len(inter.calls) == 1
    chat_id, title, choices, handler = inter.calls[0]
    labels = [c["label"] for c in choices]
    assert "work" in labels and "default" in labels
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    handler("500", "work")
    assert profile_switch.load_map(tmp_path) == {"500": "work"}


async def test_assistant_without_multiplex_explains(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _FC:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            return "m.1"

    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _RunnerOff()
    await adapter._handle_update(_upd_text("/assistant"))
    assert fc.sent and "multiplex_profiles" in fc.sent[0]


async def test_assistant_intercepts_before_group_gate(tmp_path, monkeypatch):
    """В группе /assistant работает без упоминания бота (как /geo, /contact)."""
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _Interactive:
        def __init__(self):
            self.calls = []

        async def send_choice_picker(self, chat_id, title, choices, session_key,
                                     on_choice_selected, metadata=None):
            self.calls.append(chat_id)
            return "m.pick"

    inter = _Interactive()
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    await adapter._handle_update(_upd_text("/assistant", chat_id=88, chat_type="chat"))
    assert inter.calls == ["88"]


class _Col:
    def __init__(self):
        self.events = []

    async def __call__(self, e):
        self.events.append(e)


async def _collect_event(adapter, upd):
    import asyncio

    col = _Col()
    adapter._message_handler = col
    await adapter._handle_update(upd)
    for _ in range(50):
        if col.events:
            break
        await asyncio.sleep(0.01)
    return col.events[0]


async def test_message_stamps_source_profile(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "work")
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    ev = await _collect_event(adapter, _upd_text("привет"))
    assert ev.source.profile == "work"


async def test_no_stamp_without_multiplex(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    profile_switch.set_profile(tmp_path, "500", "work")
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _RunnerOff()
    ev = await _collect_event(adapter, _upd_text("привет"))
    assert ev.source.profile in (None, "")


async def test_no_stamp_for_missing_profile(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    (tmp_path / "maxbot-chat-memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "maxbot-chat-memory" / "_chat_profiles.json").write_text(
        '{"500": "ghost"}', encoding="utf-8")
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default"})
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    ev = await _collect_event(adapter, _upd_text("привет"))
    assert ev.source.profile in (None, "")


async def test_group_chat_stamps_profile(tmp_path, monkeypatch):
    """Профиль для группового чата — тот же механизм (карта по chat_id)."""
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "88", "work")
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    adapter._username_re = __import__("re").compile(r"(?<![\w@])@hermes_bot\b")
    ev = await _collect_event(adapter, _upd_text("@hermes_bot привет", chat_id=88, chat_type="chat"))
    assert ev.source.profile == "work"


async def test_comment_stamps_channel_wide_profile(tmp_path, monkeypatch):
    """Комментарий наследует профиль канала (карта по chat_id канала)."""
    from maxbot.adapter import MaxAdapter
    from maxbot.models import parse_update

    class _Cfg:
        extra = {}

    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "work")
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter.gateway_runner = _Runner()
    upd = parse_update({
        "update_type": "comment_created", "marker": 9,
        "message": {"body": {"mid": "cm.1", "text": "Отличный пост!"},
                    "recipient": {"chat_id": 500, "post_id": "mid.777"},
                    "sender": {"user_id": 13, "name": "Петя"}, "timestamp": 1},
    })
    ev = await _collect_event(adapter, upd)
    assert ev.source.profile == "work"


class _RunnerAuth:
    class config:
        multiplex_profiles = True

    def __init__(self, allowed=True):
        self.allowed = allowed
        self.checked = []

    def _is_user_authorized_for_source(self, source):
        self.checked.append(source)
        return self.allowed


class _InteractiveRec:
    def __init__(self):
        self.calls = []

    async def send_choice_picker(self, chat_id, title, choices, session_key,
                                 on_choice_selected, metadata=None):
        self.calls.append((chat_id, title, choices, on_choice_selected))
        return "m.pick"


class _SendFC:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return "m.1"


async def test_assistant_text_form_switches_directly(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    inter = _InteractiveRec()
    fc = _SendFC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    await adapter._handle_update(_upd_text("/assistant work"))
    assert not inter.calls  # без пикера
    assert profile_switch.load_map(tmp_path) == {"500": "work"}
    assert fc.sent and "work" in fc.sent[0]


async def test_assistant_text_form_unknown_lists_profiles(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    inter = _InteractiveRec()
    fc = _SendFC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    await adapter._handle_update(_upd_text("/assistant ghost"))
    assert not inter.calls and profile_switch.load_map(tmp_path) == {}
    assert fc.sent and "work" in fc.sent[0]  # список доступных


async def test_assistant_sends_open_app_button(tmp_path, monkeypatch):
    """При MAX_WEBHOOK_URL пикер /assistant дополняется кнопкой open_app мини-аппа."""
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _FC(_SendFC):
        def __init__(self):
            super().__init__()
            self.keyboards = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            if kw.get("attachments"):
                self.keyboards.append(kw["attachments"])
            return "m.1"

    monkeypatch.setattr("maxbot.adapter.get_scoped_secret",
                        lambda name, default="": "https://bot.example" if name == "MAX_WEBHOOK_URL" else default)
    inter = _InteractiveRec()
    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr(profile_switch, "_soul_text", lambda name: "")
    await adapter._handle_update(_upd_text("/assistant"))
    assert inter.calls  # пикер отправлен
    assert fc.keyboards and fc.keyboards[0][0]["payload"]["buttons"][0][0]["type"] == "open_app"
    assert fc.keyboards[0][0]["payload"]["buttons"][0][0]["url"].endswith("/max/app/")


async def test_assistant_no_open_app_button_without_url(tmp_path, monkeypatch):
    """Без MAX_WEBHOOK_URL кнопка мини-аппа не отправляется."""
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _FC(_SendFC):
        def __init__(self):
            super().__init__()
            self.keyboards = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            if kw.get("attachments"):
                self.keyboards.append(kw["attachments"])
            return "m.1"

    monkeypatch.setattr("maxbot.adapter.get_scoped_secret", lambda name, default="": default)
    inter = _InteractiveRec()
    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    await adapter._handle_update(_upd_text("/assistant"))
    assert inter.calls  # пикер как обычно
    assert not fc.keyboards


async def test_assistant_direct_form_sends_open_app_button(tmp_path, monkeypatch):
    """Прямое переключение /assistant <имя> тоже сопровождается кнопкой open_app."""
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _FC(_SendFC):
        def __init__(self):
            super().__init__()
            self.keyboards = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            if kw.get("attachments"):
                self.keyboards.append(kw["attachments"])
            return "m.1"

    monkeypatch.setattr("maxbot.adapter.get_scoped_secret",
                        lambda name, default="": "https://bot.example" if name == "MAX_WEBHOOK_URL" else default)
    inter = _InteractiveRec()
    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    await adapter._handle_update(_upd_text("/assistant work"))
    assert not inter.calls
    assert profile_switch.load_map(tmp_path) == {"500": "work"}
    assert fc.keyboards and fc.keyboards[0][0]["payload"]["buttons"][0][0]["type"] == "open_app"
    assert fc.keyboards[0][0]["payload"]["buttons"][0][0]["url"].endswith("/max/app/")


async def test_assistant_picker_shows_descriptions(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    inter = _InteractiveRec()
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth()
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    monkeypatch.setattr(profile_switch, "_soul_text",
                        lambda name: "Помощник по коду и DevOps" if name == "work" else "")
    await adapter._handle_update(_upd_text("/assistant"))
    chat_id, title, choices, _h = inter.calls[0]
    labels = {c["label"] for c in choices}
    assert any(lb.startswith("work — Помощник по коду и DevOps") for lb in labels)
    assert "default" in labels  # без описания — просто имя
    assert "Текущий: default" in title


async def test_assistant_denied_for_unauthorized_user(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _Interactive:
        def __init__(self):
            self.calls = []

        async def send_choice_picker(self, *a, **kw):
            self.calls.append(a)

    class _FC:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            return "m.1"

    inter = _Interactive()
    fc = _FC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    runner = _RunnerAuth(False)
    adapter.gateway_runner = runner
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    await adapter._handle_update(_upd_text("/assistant"))
    assert not inter.calls and fc.sent and "доступ" in fc.sent[0].lower()


async def test_assistant_allowed_for_authorized_user(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    class _Interactive:
        def __init__(self):
            self.calls = []

        async def send_choice_picker(self, *a, **kw):
            self.calls.append(a)

    inter = _Interactive()
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = inter
    adapter._uploader = None
    adapter.gateway_runner = _RunnerAuth(True)
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    await adapter._handle_update(_upd_text("/assistant"))
    assert len(inter.calls) == 1


class _MediaFC:
    def __init__(self):
        self.commented = []
        self.sent = []

    async def post_comment(self, post_id, text):
        self.commented.append((post_id, text))
        return "cm.x"

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, kw.get("attachments")))
        return "m.1"


def test_known_chats_roundtrip(tmp_path):
    profile_switch.remember_chat(tmp_path, "500", "channel")
    profile_switch.remember_chat(tmp_path, "88", "group")
    assert profile_switch.known_chats(tmp_path) == {
        "500": {"type": "channel"}, "88": {"type": "group"}}


def test_known_chats_type_overwrite(tmp_path):
    profile_switch.remember_chat(tmp_path, "500", "group")
    profile_switch.remember_chat(tmp_path, "500", "channel")
    assert profile_switch.known_chats(tmp_path) == {"500": {"type": "channel"}}


async def test_message_and_comment_remember_chat(tmp_path, monkeypatch):
    from maxbot.adapter import MaxAdapter
    from maxbot.models import parse_update

    class _Cfg:
        extra = {}

    monkeypatch.setattr("maxbot.adapter._plugin_home", lambda: tmp_path)
    adapter = MaxAdapter(_Cfg(), client=None, transport=None)
    adapter._bot_user_id = 999
    adapter._interactive = None
    adapter._uploader = None
    adapter._username_re = __import__("re").compile(r"(?<![\w@])@hermes_bot\b")
    col = _Col()
    adapter._message_handler = col
    await adapter._handle_update(_upd_text("привет", chat_id=77, chat_type="dialog"))
    ev = await _collect_event(adapter, parse_update({
        "update_type": "comment_created", "marker": 9,
        "message": {"body": {"mid": "cm.1", "text": "ок"},
                    "recipient": {"chat_id": 500, "post_id": "mid.777"},
                    "sender": {"user_id": 13, "name": "Петя"}, "timestamp": 1},
    }))
    assert ev is not None
    known = profile_switch.known_chats(tmp_path)
    assert known.get("77") == {"type": "dm"} and known.get("500") == {"type": "channel"}


async def test_media_in_comment_thread_rejected_with_notice():
    """MAX-комментарии — только текст: медиа из ветки отклоняем с пояснением."""
    import pytest as _pytest

    _pytest.importorskip("gateway.platforms.base")
    from maxbot.adapter import MaxAdapter

    class _Cfg:
        extra = {}

    fc = _MediaFC()
    adapter = MaxAdapter(_Cfg(), client=fc, transport=None)
    adapter._uploader = None
    res = await adapter.send_image_file(
        "500", "/tmp/synthetic.png", caption="схема", metadata={"thread_id": "mid.777"})
    assert res.success is False and not fc.sent
    assert fc.commented and fc.commented[0][0] == "mid.777"
    assert "вложени" in fc.commented[0][1].lower() or "комментари" in fc.commented[0][1].lower()
