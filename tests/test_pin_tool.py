"""max_pin: гейт по правам бота-админа (chat.admin_permissions → pin_message)."""
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot import state  # noqa: E402
from maxbot.pin_tool import _max_pin_handler  # noqa: E402


class FakeClient:
    def __init__(self, membership=None, membership_error=None):
        self.membership = membership
        self.membership_error = membership_error
        self.membership_calls = 0
        self.pins = []
        self.unpins = 0
        self.pinned_reads = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_membership(self, chat_id):
        self.membership_calls += 1
        if self.membership_error:
            raise self.membership_error
        return self.membership

    async def pin_message(self, chat_id, message_id):
        self.pins.append((chat_id, message_id))
        return True

    async def unpin_message(self, chat_id):
        self.unpins += 1
        return True

    async def get_pinned_message(self, chat_id):
        self.pinned_reads += 1
        return {}


def _tool_with_client(monkeypatch, client):
    import maxbot.pin_tool as T

    monkeypatch.setattr(T, "_secret", lambda name, default="": "TOKEN")
    monkeypatch.setattr(T, "_session_chat_id", lambda: 555)
    import maxbot.max_api as api
    monkeypatch.setattr(api, "MaxClient", lambda *a, **kw: client)


async def test_pin_blocked_when_right_missing(monkeypatch):
    """Право сняли (событие обновило кэш) — отказ без единого API-вызова."""
    state.BOT_RIGHTS.clear()
    state.remember_bot_rights("555", is_admin=True, permissions=["read_all_messages"])
    c = FakeClient(membership={"is_admin": True, "permissions": ["pin_message"]})
    _tool_with_client(monkeypatch, c)
    res = await _max_pin_handler({"action": "pin", "message_id": "mid.1"})
    assert "❌" in res and "pin_message" in res
    assert c.pins == [] and c.membership_calls == 0  # кэш есть —membership не трогаем


async def test_pin_allowed_with_right(monkeypatch):
    state.BOT_RIGHTS.clear()
    state.remember_bot_rights("555", is_admin=True, permissions=["pin_message"])
    c = FakeClient()
    _tool_with_client(monkeypatch, c)
    res = await _max_pin_handler({"action": "pin", "message_id": "mid.1"})
    assert "✅" in res and c.pins == [(555, "mid.1")]


async def test_pin_lazy_fetches_membership_once(monkeypatch):
    """Кэша нет (перезапуск) — тянем GET /chats/{id}/members/me и кэшируем."""
    state.BOT_RIGHTS.clear()
    c = FakeClient(membership={"is_admin": True, "permissions": ["pin_message", "write"]})
    _tool_with_client(monkeypatch, c)
    res = await _max_pin_handler({"action": "pin", "message_id": "mid.1"})
    assert "✅" in res and c.pins == [(555, "mid.1")]
    res2 = await _max_pin_handler({"action": "unpin"})
    assert "✅" in res2
    assert c.membership_calls == 1  # второй вызов берёт из кэша


async def test_pin_blocked_not_admin_lazy(monkeypatch):
    state.BOT_RIGHTS.clear()
    c = FakeClient(membership={"is_admin": False, "permissions": None})
    _tool_with_client(monkeypatch, c)
    res = await _max_pin_handler({"action": "pin", "message_id": "mid.1"})
    assert "❌" in res and c.pins == []
    res2 = await _max_pin_handler({"action": "pin", "message_id": "mid.1"})
    assert "❌" in res2
    assert c.membership_calls == 1  # отказ закэширован


async def test_pin_fail_open_when_membership_unavailable(monkeypatch):
    """membership недоступен (сеть/403) — не блокируем, даём API самому ответить."""
    from maxbot.max_api import MaxApiError

    state.BOT_RIGHTS.clear()
    c = FakeClient(membership_error=MaxApiError(0, "net", code="network"))
    _tool_with_client(monkeypatch, c)
    res = await _max_pin_handler({"action": "pin", "message_id": "mid.1"})
    assert "✅" in res and c.pins == [(555, "mid.1")]
    assert state.bot_rights("555") is None  # ничего не закэшировали


async def test_pinned_read_not_gated(monkeypatch):
    """Чтение закрепа не требует прав — membership не дёргаем."""
    state.BOT_RIGHTS.clear()
    c = FakeClient(membership={"is_admin": False, "permissions": None})
    _tool_with_client(monkeypatch, c)
    res = await _max_pin_handler({"action": "pinned"})
    assert "закреплённого" in res
    assert c.pinned_reads == 1 and c.membership_calls == 0
