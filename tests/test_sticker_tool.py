"""max_sticker: переотправка стикеров по коду."""
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot import state
from maxbot.sticker_tool import _max_sticker_handler, register_sticker_tool  # noqa: E402


class FakeClient:
    def __init__(self):
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_message(self, chat_id, text, *, attachments=None, **kw):
        self.sent.append((chat_id, text, attachments))
        return "mid.9"


def _tool_with_client(monkeypatch, client):
    import maxbot.sticker_tool as T

    monkeypatch.setattr(T, "_secret", lambda name, default="": "TOKEN")
    monkeypatch.setattr(T, "_session_chat_id", lambda: 555)
    import maxbot.max_api as api
    monkeypatch.setattr(api, "MaxClient", lambda *a, **kw: client)
    return T


async def test_sticker_send_by_code(monkeypatch):
    c = FakeClient()
    _tool_with_client(monkeypatch, c)
    res = await _max_sticker_handler({"action": "send", "code": "abc123"})
    assert "✅" in res
    assert c.sent[0][0] == 555
    assert c.sent[0][2] == [{"type": "sticker", "payload": {"code": "abc123"}}]


async def test_sticker_send_last_seen_by_default(monkeypatch):
    state.SEEN_STICKERS.clear()
    state.remember_sticker("old1")
    state.remember_sticker("fresh1")
    c = FakeClient()
    _tool_with_client(monkeypatch, c)
    res = await _max_sticker_handler({"action": "send"})
    assert "✅" in res and "fresh1" in res
    assert c.sent[0][2][0]["payload"]["code"] == "fresh1"


async def test_sticker_recent_and_no_seen(monkeypatch):
    state.SEEN_STICKERS.clear()
    res = await _max_sticker_handler({"action": "recent"})
    assert "нет" in res.lower()
    res = await _max_sticker_handler({"action": "send"})
    assert "❌" in res
    state.remember_sticker("zz9")
    res = await _max_sticker_handler({"action": "recent"})
    assert "zz9" in res


def test_adapter_remembers_sticker_codes(monkeypatch):
    import asyncio

    from maxbot.adapter import MaxAdapter
    from maxbot.models import Attachment, Message, MessageBody

    class FakeCfg:
        extra = {}

    async def fake_refresh(msg):
        return msg.body.attachments

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        return None

    state.SEEN_STICKERS.clear()
    adapter = MaxAdapter(FakeCfg(), client=None, transport=None)
    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)
    msg = Message(body=MessageBody(text="", attachments=[
        Attachment(type="sticker", payload={"code": "s77", "url": "https://x/s.png"})]),
        chat_id=1, chat_type="dialog")

    _, _, texts = asyncio.run(adapter._collect_media(msg))
    assert [s["code"] for s in state.recent_stickers()] == ["s77"]
    # агент видит код в тексте — может переотправить или сослаться
    assert "s77" in texts


def test_adapter_known_sticker_skips_vision(monkeypatch):
    """Стикер из каталога: описание текстом, картинка не скачивается — токены экономятся."""
    import asyncio

    from maxbot.adapter import MaxAdapter
    from maxbot.models import Attachment, Message, MessageBody

    class FakeCfg:
        extra = {}

    async def fake_refresh(msg):
        return msg.body.attachments

    dl_calls = []

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        dl_calls.append(att.type)
        return "/tmp/x.png"

    adapter = MaxAdapter(FakeCfg(), client=None, transport=None)
    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)
    msg = Message(body=MessageBody(text="", attachments=[
        Attachment(type="sticker", payload={"code": "50828bb", "url": "https://x/s.png"})]),
        chat_id=1, chat_type="dialog")

    paths, types, texts = asyncio.run(adapter._collect_media(msg))
    assert dl_calls == []          # превью не качаем: стикер известен
    assert paths == [] and types == []
    assert "50828bb" in texts and "Плачущий" in texts  # описание из каталога

    # неизвестный стикер — прежний путь: картинка + код
    msg2 = Message(body=MessageBody(text="", attachments=[
        Attachment(type="sticker", payload={"code": "zz999", "url": "https://x/s.png"})]),
        chat_id=1, chat_type="dialog")
    paths2, types2, texts2 = asyncio.run(adapter._collect_media(msg2))
    assert dl_calls == ["sticker"] and types2 == ["image/png"]
    assert "zz999" in texts2


async def test_sticker_find_by_keyword():
    res = await _max_sticker_handler({"action": "find", "query": "мишка сердце"})
    assert "5082fbb" in res  # «Мишка держит сердце»
    res2 = await _max_sticker_handler({"action": "find", "query": "злой"})
    assert "50829bb" in res2  # «Злой разгневанный медвежонок»
    res3 = await _max_sticker_handler({"action": "find", "query": "неттакоготовара"})
    assert "нет стикеров" in res3.lower()


def test_register_sticker_tool_direct():
    tools = []

    class FakeCtx:
        def register_tool(self, **kw):
            tools.append(kw.get("name"))

    register_sticker_tool(FakeCtx())
    assert tools == ["max_sticker"]
