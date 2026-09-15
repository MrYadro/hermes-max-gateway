"""max_geo: отправка геопозиции вложением MAX."""
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot import geo_tool  # noqa: E402
from maxbot.geo_tool import _GEO_SCHEMA, _max_geo_handler, register_geo_tool  # noqa: E402


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


async def test_geo_sends_location_attachment(monkeypatch):
    sent = {}

    class C(FakeClient):
        async def send_message(self, chat_id, text, *, attachments=None, **kw):
            sent["args"] = (chat_id, text, attachments)
            return "mid.1"

    monkeypatch.setattr(geo_tool, "_MaxClient", lambda *a, **kw: C())
    monkeypatch.setattr(geo_tool, "_secret", lambda name, default="": "TOKEN" if "TOKEN" in name else default)
    monkeypatch.setenv("MAX_SESSION_CHAT_ID", "777")
    import maxbot.geo_tool as G
    monkeypatch.setattr(G, "_session_chat_id", lambda: 777)

    res = await _max_geo_handler({"latitude": 55.7558, "longitude": 37.6173})
    assert "✅" in res
    chat_id, text, atts = sent["args"]
    assert chat_id == 777
    assert atts == [{"type": "location", "latitude": 55.7558, "longitude": 37.6173}]


async def test_geo_validates_coordinates(monkeypatch):
    res = await _max_geo_handler({"latitude": 200, "longitude": 10})
    assert "❌" in res and "Широта" in res
    res = await _max_geo_handler({"latitude": 10})
    assert "❌" in res


def test_geo_schema_shape():
    assert _GEO_SCHEMA["description"]  # полный OpenAI-def, а не голые параметры
    params = _GEO_SCHEMA["parameters"]
    assert params["properties"]["latitude"]["type"] == "number"
    assert params["properties"]["longitude"]["type"] == "number"
    assert set(params["required"]) == {"latitude", "longitude"}


def test_register_registers_geo_and_group_tools():
    tools = []

    class FakeCtx:
        def __getattr__(self, method):
            return lambda *a, **kw: None

        def register_tool(self, **kw):
            tools.append(kw.get("name"))

        def register_transcription_provider(self, provider):
            pass

    import maxbot
    maxbot.register(FakeCtx())
    assert "max_geo" in tools
    assert "max_group" in tools and "max_channel" in tools  # раньше спали без регистрации
    assert "max_sticker" in tools


def test_register_geo_tool_direct():
    tools = []

    class FakeCtx:
        def register_tool(self, **kw):
            tools.append(kw.get("name"))

    register_geo_tool(FakeCtx())
    assert tools == ["max_geo"]
