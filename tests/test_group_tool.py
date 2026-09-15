"""Инструмент max_group: схемы и хендлер (без сети)."""

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.group_tool import _CHANNEL_SCHEMA, _SCHEMA, _max_group_handler, register_group_tool


class FakeCtx:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kw):
        self.tools.append(kw)


def test_schema_shape():
    # полный OpenAI-def: модель видит description+parameters через tool_describe
    for schema in (_SCHEMA, _CHANNEL_SCHEMA):
        assert schema["description"] and schema["parameters"]["type"] == "object"
    assert _SCHEMA["parameters"]["required"] == ["action"]
    assert set(_SCHEMA["parameters"]["properties"]) >= {
        "action", "chat_id", "message_id", "user_id"}


def test_register_wires_tool():
    ctx = FakeCtx()
    register_group_tool(ctx)
    names = {t["name"]: t for t in ctx.tools}
    assert set(names) == {"max_group", "max_channel"}
    for tool in ctx.tools:
        assert tool["is_async"] is True
        assert callable(tool["handler"]) and callable(tool["check_fn"])


async def test_handler_requires_chat():
    out = await _max_group_handler({"action": "members"})
    assert "chat_id" in out and "❌" in out


async def test_handler_unknown_action(monkeypatch):
    out = await _max_group_handler({"action": "pin", "chat_id": 1})  # нет message_id
    assert "message_id" in out
