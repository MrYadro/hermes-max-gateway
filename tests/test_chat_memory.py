"""Per-chat память: изоляция групп, глобальный блок только в DM."""
import json

import pytest

pytest.importorskip("agent.memory_provider")

from maxbot.chat_memory import ChatMemoryProvider

DM = "agent:main:max:dm:123456789"
GROUP = "agent:main:max:group:chat:-100200300400"


def make(tmp_path):
    p = ChatMemoryProvider()
    assert p.name == "maxbot"
    p.initialize(DM, hermes_home=str(tmp_path))
    return p


def test_group_notes_isolated_from_dm(tmp_path):
    p = make(tmp_path)
    p._bind(GROUP)
    assert "ok" in p.handle_tool_call("chat_memory_save", {"text": "мы решили ставить шкаф в угол"},
                                      session_id=GROUP)
    assert "мы решили" in p.prefetch("что решили?", session_id=GROUP)
    assert "мы решили" not in p.prefetch("что?", session_id=DM)


def test_dm_gets_builtin_block_group_does_not(tmp_path):
    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "MEMORY.md").write_text("пользователь любит Hasense", encoding="utf-8")
    p = make(tmp_path)
    assert "Hasense" in p.prefetch("q", session_id=DM)
    assert "Hasense" not in p.prefetch("q", session_id=GROUP)


def test_save_list_forget_roundtrip(tmp_path):
    p = make(tmp_path)
    p.handle_tool_call("chat_memory_save", {"text": "факт 1"}, session_id=DM)
    out = json.loads(p.handle_tool_call("chat_memory_list", {}, session_id=DM))
    assert len(out["notes"]) == 1
    json.loads(p.handle_tool_call("chat_memory_forget", {"id": out["notes"][0]["id"]}, session_id=DM))
    assert json.loads(p.handle_tool_call("chat_memory_list", {}, session_id=DM))["notes"] == []


def test_persistence_across_restart(tmp_path):
    p = make(tmp_path)
    p.handle_tool_call("chat_memory_save", {"text": "пережить рестарт"}, session_id=GROUP)
    p2 = ChatMemoryProvider()
    p2.initialize(GROUP, hermes_home=str(tmp_path))
    assert "пережить рестарт" in p2.prefetch("q", session_id=GROUP)


def test_channel_posts_share_memory(tmp_path):
    (tmp_path / "maxbot-chat-memory").mkdir()
    (tmp_path / "maxbot-chat-memory" / "_channel_posts.json").write_text(
        '{"mid.777": 500, "mid.888": 500}', encoding="utf-8")
    p = make(tmp_path)
    p.handle_tool_call("chat_memory_save", {"text": "правило канала: без спама"},
                       session_id="agent:main:max:group:mid.777")
    assert "без спама" in p.prefetch("q", session_id="agent:main:max:group:mid.888")


def test_uuid_session_resolves_via_index(tmp_path):
    import json as _json
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "sessions.json").write_text(_json.dumps({
        "agent:main:max:group:-5550011:987654321": {"session_id": "20260914_x", "session_key": "k"}}),
        encoding="utf-8")
    p = make(tmp_path)
    p.handle_tool_call("chat_memory_save", {"text": "групповой факт"}, session_id="20260914_x")
    assert "групповой факт" in p.prefetch("q", session_id="20260914_x")
    assert "групповой факт" not in p.prefetch("q", session_id=DM)


def test_group_chat_prefix_key(tmp_path):
    p = make(tmp_path)
    p._bind("agent:main:max:group:chat:123456789:987654321")
    assert p._key == "max-group-123456789"
