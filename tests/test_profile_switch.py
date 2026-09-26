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
