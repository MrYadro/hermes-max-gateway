"""max_share: карточка ссылки (share-вложение)."""
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.share_tool import _max_share_handler, register_share_tool  # noqa: E402


class FakeClient:
    def __init__(self):
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_message(self, chat_id, text, *, attachments=None, **kw):
        self.sent.append((chat_id, text, attachments))
        return "mid.1"


def _patch(monkeypatch, client):
    import maxbot.max_api as api
    import maxbot.share_tool as T

    monkeypatch.setattr(T, "_secret", lambda name, default="": "TOKEN")
    monkeypatch.setattr(T, "_session_chat_id", lambda: 555)
    monkeypatch.setattr(api, "MaxClient", lambda *a, **kw: client)


async def test_share_sends_card(monkeypatch):
    c = FakeClient()
    _patch(monkeypatch, c)
    res = await _max_share_handler({"url": "https://example.com/x", "text": "смотри"})
    assert "✅" in res
    chat_id, text, atts = c.sent[0]
    assert chat_id == 555
    assert atts == [{"type": "share", "payload": {"url": "https://example.com/x"}}]
    assert text == "смотри"


async def test_share_requires_nonempty_text(monkeypatch):
    """API: share не принимается с пустым text — подставляем дефолт."""
    c = FakeClient()
    _patch(monkeypatch, c)
    await _max_share_handler({"url": "https://example.com"})
    assert c.sent[0][1]  # текст не пустой


async def test_share_validates_url(monkeypatch):
    c = FakeClient()
    _patch(monkeypatch, c)
    res = await _max_share_handler({"url": "example.com"})
    assert "❌" in res and not c.sent


def test_register_share_tool():
    tools = []

    class FakeCtx:
        def register_tool(self, **kw):
            tools.append(kw.get("name"))

    register_share_tool(FakeCtx())
    assert tools == ["max_share"]
