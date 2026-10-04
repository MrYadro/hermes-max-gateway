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


# ── гейт прав бота-админа (кэш BOT_RIGHTS ← bot_admin_permissions_changed) ──

class FakeClient:
    def __init__(self, membership=None):
        self.membership = membership
        self.membership_calls = 0
        self.writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_membership(self, chat_id):
        self.membership_calls += 1
        return self.membership

    async def pin_message(self, chat_id, mid):
        self.writes.append(("pin", mid))
        return True

    async def unpin_message(self, chat_id):
        self.writes.append(("unpin",))
        return True

    async def add_admin(self, chat_id, user_id, permissions=None):
        self.writes.append(("add_admin", user_id))
        return True

    async def remove_admin(self, chat_id, user_id):
        self.writes.append(("remove_admin", user_id))
        return True

    async def kick_member(self, chat_id, user_id):
        self.writes.append(("kick", user_id))
        return True

    async def patch_chat(self, chat_id, **fields):
        self.writes.append(("rename", fields))
        return {}

    async def get_members(self, chat_id):
        return [{"user_id": 1}]

    async def get_admins(self, chat_id):
        return [{"user_id": 2}]

    async def get_pinned_message(self, chat_id):
        return {}

    async def get_chat(self, chat_id):
        return {"chat_id": chat_id}

    async def leave_chat(self, chat_id):
        self.writes.append(("leave",))
        return True


def _gate(monkeypatch, client):
    import maxbot.group_tool as G
    import maxbot.max_api as api

    monkeypatch.setattr(G, "get_scoped_secret", lambda name, default="": "TOKEN")
    monkeypatch.setattr(G, "_session_chat_id", lambda: 555)
    monkeypatch.setattr(api, "MaxClient", lambda *a, **kw: client)


async def test_group_admin_actions_blocked_without_rights(monkeypatch):
    from maxbot import state

    state.BOT_RIGHTS.clear()
    state.remember_bot_rights("555", is_admin=True, permissions=["read_all_messages"])
    c = FakeClient()
    _gate(monkeypatch, c)
    for args, right in (({"action": "pin", "message_id": "m"}, "pin_message"),
                        ({"action": "add_admin", "user_id": 7}, "add_admins"),
                        ({"action": "kick", "user_id": 7}, "add_remove_members"),
                        ({"action": "rename", "title": "X"}, "change_chat_info")):
        res = await _max_group_handler(args)
        assert "❌" in res and right in res, (args, res)
    assert c.writes == [] and c.membership_calls == 0  # отказ без API-вызовов


async def test_group_admin_actions_allowed_with_rights(monkeypatch):
    from maxbot import state

    state.BOT_RIGHTS.clear()
    state.remember_bot_rights("555", is_admin=True, permissions=[
        "pin_message", "add_admins", "add_remove_members", "change_chat_info"])
    c = FakeClient()
    _gate(monkeypatch, c)
    res = await _max_group_handler({"action": "pin", "message_id": "m"})
    assert "✅" in res
    res = await _max_group_handler({"action": "add_admin", "user_id": 7})
    assert "✅" in res
    assert [w[0] for w in c.writes] == ["pin", "add_admin"]


async def test_group_reads_and_leave_not_gated(monkeypatch):
    from maxbot import state

    state.BOT_RIGHTS.clear()
    c = FakeClient(membership={"is_admin": False, "permissions": None})
    _gate(monkeypatch, c)
    for action in ("my_permissions", "members", "admins", "pinned", "info", "leave"):
        res = await _max_group_handler({"action": action})
        assert "❌" not in res, (action, res)
    # гейт не добавил своих membership-вызовов (только my_permissions ходил сам)
    assert c.membership_calls == 1


async def test_group_not_admin_lazy_cached(monkeypatch):
    from maxbot import state

    state.BOT_RIGHTS.clear()
    c = FakeClient(membership={"is_admin": False, "permissions": None})
    _gate(monkeypatch, c)
    res = await _max_group_handler({"action": "pin", "message_id": "m"})
    assert "не админ" in res and c.writes == []
    await _max_group_handler({"action": "kick", "user_id": 7})
    assert c.membership_calls == 1  # отказ закэширован


async def test_group_fail_open_when_membership_unavailable(monkeypatch):
    from maxbot import state
    from maxbot.max_api import MaxApiError

    state.BOT_RIGHTS.clear()
    c = FakeClient(membership={"is_admin": True, "permissions": ["pin_message"]})

    async def boom(chat_id):
        raise MaxApiError(0, "net", code="network")

    c.get_membership = boom
    _gate(monkeypatch, c)
    res = await _max_group_handler({"action": "pin", "message_id": "m"})
    assert "✅" in res and c.writes == [("pin", "m")]
    assert state.bot_rights("555") is None
