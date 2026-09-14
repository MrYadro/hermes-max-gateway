"""max_contact: отправка контакта (vCard) в MAX."""
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.contact_tool import _build_vcf, _max_contact_handler, register_contact_tool  # noqa: E402


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
    import maxbot.contact_tool as T
    import maxbot.max_api as api

    monkeypatch.setattr(T, "_secret", lambda name, default="": "TOKEN")
    monkeypatch.setattr(T, "_session_chat_id", lambda: 555)
    monkeypatch.setattr(api, "MaxClient", lambda *a, **kw: client)
    return T


async def test_contact_sends_vcard(monkeypatch):
    c = FakeClient()
    _patch(monkeypatch, c)
    res = await _max_contact_handler(
        {"name": "Иван Петров", "phone": "+79990001122"})
    assert "✅" in res
    chat_id, text, atts = c.sent[0]
    assert chat_id == 555 and text == ""
    assert atts[0]["type"] == "contact"
    vcf = atts[0]["payload"]["vcf_info"]
    assert "FN:Иван Петров" in vcf and "+79990001122" in vcf


async def test_contact_validates_input(monkeypatch):
    c = FakeClient()
    _patch(monkeypatch, c)
    res = await _max_contact_handler({"phone": "+7999"})
    assert "❌" in res and not c.sent
    res = await _max_contact_handler({"name": "Без телефона"})
    assert "❌" in res and not c.sent


def test_build_vcf_shape():
    vcf = _build_vcf("Тест", "+7")
    assert vcf.startswith("BEGIN:VCARD") and vcf.endswith("END:VCARD\r\n")
    assert "VERSION:3.0" in vcf and "TEL;TYPE=cell:+7" in vcf


def test_register_contact_tool():
    tools = []

    class FakeCtx:
        def register_tool(self, **kw):
            tools.append(kw.get("name"))

    register_contact_tool(FakeCtx())
    assert tools == ["max_contact"]


def test_register_all_tools_includes_contact():
    tools = []

    class FakeCtx:
        def __getattr__(self, method):
            return lambda *a, **kw: None

        def register_tool(self, **kw):
            tools.append(kw.get("name"))

    import maxbot
    maxbot.register(FakeCtx())
    assert "max_contact" in tools and "max_geo" in tools and "max_sticker" in tools
