import asyncio

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.adapter import MaxAdapter
from maxbot.models import parse_update


class FakeCfg:
    extra = {}


class FakeClient:
    def __init__(self):
        self.sent = []
        self._mid = 0
        self.replied_lookup = {}

    async def send_message(self, chat_id, text, **kw):
        self._mid += 1
        self.sent.append((chat_id, text))
        return f"mid.{self._mid}"

    async def get_message(self, message_id):
        return self.replied_lookup.get(message_id)


def make_adapter():
    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=None)
    adapter._bot_user_id = 999
    adapter._uploader = None
    adapter._mention_re = __import__("re").compile(r"\[([^\]]*)\]\(max://user/999\)")
    adapter._interactive = _NoopInteractive()
    adapter._message_handler = None
    return adapter


class _NoopInteractive:
    async def dispatch(self, callback):
        self.last = callback


def _upd_message(text="привет", chat_type="dialog", sender_id=42, chat_id=100, mid="m1", link=None):
    d = {
        "update_type": "message_created", "marker": 1,
        "message": {
            "body": {"mid": mid, "text": text, "attachments": []},
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "sender": {"user_id": sender_id, "name": "Иван"},
            "timestamp": 1,
        },
    }
    if link:
        d["message"]["link"] = link
    return parse_update(d)


class Collector:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)


async def test_dm_message_dispatched():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message())
    await asyncio.sleep(0.05)  # handle_message диспетчеризует в background-таске
    assert len(col.events) == 1
    ev = col.events[0]
    assert ev.text == "привет" and ev.source.chat_id == "100"
    assert ev.source.chat_type == "dm" and ev.source.user_id == "42"


async def test_own_messages_filtered():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message(sender_id=999))
    assert col.events == []


async def test_group_requires_mention():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message(text="просто болтовня", chat_type="chat"))
    assert col.events == []
    await adapter._handle_update(
        _upd_message(text="[Hermes](max://user/999) посчитай 2+2", chat_type="chat"))
    await asyncio.sleep(0.05)  # handle_message диспетчеризует в background-таске
    assert len(col.events) == 1 and col.events[0].text == "посчитай 2+2"


async def test_group_reply_to_bot_passes():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    adapter._client.replied_lookup["m.our"] = _upd_message(sender_id=999, mid="m.our").message
    upd = _upd_message(text="и что дальше?", chat_type="chat",
                       link={"type": "reply", "mid": "m.our"})
    await adapter._handle_update(upd)
    await asyncio.sleep(0.05)  # handle_message диспетчеризует в background-таске
    assert len(col.events) == 1


async def test_location_and_contact_as_text():
    adapter = make_adapter()
    col = Collector()
    adapter._message_handler = col
    d = _upd_message(text="")
    d.message.body.attachments = [
        __import__("maxbot.models", fromlist=["Attachment"]).Attachment(
            type="location", latitude=55.75, longitude=37.61),
        __import__("maxbot.models", fromlist=["Attachment"]).Attachment(
            type="contact",
            payload={"vcf_info": "BEGIN:VCARD\r\nTEL;TYPE=cell:79990000000\r\nFN:Иван\r\nEND:VCARD"}),
    ]
    await adapter._handle_update(d)
    await asyncio.sleep(0.05)  # handle_message диспетчеризует в background-таске
    ev = col.events[0]
    assert "55.75" in ev.text and "79990000000" in ev.text


async def test_bot_started_sends_greeting():
    adapter = make_adapter()
    adapter._message_handler = Collector()
    await adapter._handle_update(parse_update(
        {"update_type": "bot_started", "chat_id": 300, "user": {"user_id": 7, "name": "Оля"}}))
    assert adapter._client.sent and "Hermes" in adapter._client.sent[0][1]


async def test_message_callback_routed_to_interactive():
    adapter = make_adapter()
    await adapter._handle_update(parse_update({
        "update_type": "message_callback",
        "callback": {"callback_id": "cb.1", "payload": "ea:once:5",
                     "message": _upd_message().message.raw},
    }))
    assert adapter._interactive.last.payload == "ea:once:5"


async def test_download_cached_rejects_oversize(caplog):
    import logging

    import httpx
    import respx

    from maxbot.models import Attachment

    adapter = make_adapter()
    att = Attachment(type="image", payload={"url": "http://test.max/big.png"})
    with respx.mock, caplog.at_level(logging.WARNING, logger="maxbot.adapter"):
        route = respx.get("http://test.max/big.png").mock(
            return_value=httpx.Response(
                200, headers={"content-length": str(500 * 1024 * 1024)}, content=b"x"))
        path = await adapter._download_cached(att, lambda data, ext: "cached", ".png")
    assert path is None  # больше капа — деградация без скачивания в RAM
    assert route.called
    assert any("не скачиваем" in r.message for r in caplog.records)


async def test_download_cached_allows_normal_size():
    import httpx
    import respx

    from maxbot.models import Attachment

    adapter = make_adapter()
    att = Attachment(type="image", payload={"url": "http://test.max/ok.png"})
    with respx.mock:
        respx.get("http://test.max/ok.png").mock(
            return_value=httpx.Response(200, content=b"pngdata",
                                        headers={"content-type": "image/png"}))
        path = await adapter._download_cached(att, lambda data, ext: f"{len(data)}:{ext}", ".jpg")
    assert path == "7:.jpg"  # без mime_map ext — дефолтный


async def test_download_sniffs_magic_bytes_when_no_ext():
    """MAX часто не присылает filename, а сервер отдаёт octet-stream:
    расширение определяем по магике уже скачанного буфера."""
    import httpx
    import respx

    from maxbot.models import Attachment

    adapter = make_adapter()
    cases = [
        (b"%PDF-1.7 whatever", ".pdf"),
        (b"PK\x03\x04zipdata", ".zip"),
        (b"\x00\x00\x00\x18ftypmp42", ".mp4"),
        (b"OggSxxxx", ".ogg"),
        (b"ID3\x03tagdata", ".mp3"),
        (b"7z\xbc\xaf\x27\x1c", ".7z"),
        (b"Rar!\x1a\x07\x00", ".rar"),
        (b"\x00\x01\x02\x03no-magic", ".bin"),  # не распознали — честный .bin
    ]
    for i, (content, expected) in enumerate(cases):
        att = Attachment(type="file", payload={"url": f"http://test.max/f{i}"})
        with respx.mock:
            respx.get(f"http://test.max/f{i}").mock(
                return_value=httpx.Response(
                    200, content=content,
                    headers={"content-type": "application/octet-stream"}))
            ext = await adapter._download_cached(att, lambda data, ext: ext, ".bin")
        assert ext == expected, (content, ext)


def test_file_ext_from_filename():
    from maxbot.adapter import _file_ext
    assert _file_ext("report.pdf") == ".pdf"
    assert _file_ext("archive.ZIP") == ".zip"
    assert _file_ext("без-расширения") == ".bin"
    assert _file_ext(None) == ".bin"


async def test_collect_media_reports_mime_types(monkeypatch):
    """Голосовые попадают в STT-пайплайн ядра только при audio/* mime в media_types."""
    from maxbot.models import Attachment, Message, MessageBody

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        return f"/tmp/x{default_ext}"

    async def fake_refresh(msg):
        return msg.body.attachments

    adapter = make_adapter()
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)
    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)

    msg = Message(body=MessageBody(text="", attachments=[
        Attachment(type="audio", payload={"transcription": "привет"}),
        Attachment(type="image", payload={}),
        Attachment(type="video", payload={}),
    ]), chat_id=1, chat_type="dialog")
    paths, types, texts = await adapter._collect_media(msg)
    assert types[0].startswith("audio/")  # STT-контракт ядра
    assert types[1].startswith("image/")
    assert types[2].startswith("video/")
    # расшифровка MAX остаётся текстом — сторонний STT не настроен
    assert "расшифровка" in texts.lower() and "привет" in texts


def _aiter(items):
    async def gen():
        for it in items:
            yield it
    return gen()


async def test_group_username_mention_passes(monkeypatch):
    import re

    adapter = make_adapter()
    adapter._username_re = re.compile(r"(?<![\w@])@hermes_bot\b")
    col = Collector()
    adapter._message_handler = col
    upd = _upd_message(text="@hermes_bot посчитай 2+2", chat_type="chat")
    await adapter._handle_update(upd)
    for _ in range(50):
        if col.events:
            break
        await asyncio.sleep(0.01)
    assert len(col.events) == 1 and col.events[0].text == "посчитай 2+2"


async def test_group_events_get_isolation_prompt():
    import re

    adapter = make_adapter()
    adapter._group_isolation = True
    adapter._username_re = re.compile(r"(?<![\w@])@hermes_bot\b")
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message(text="@hermes_bot hi", chat_type="chat"))
    for _ in range(50):
        if col.events:
            break
        await asyncio.sleep(0.01)
    assert col.events and col.events[0].channel_prompt and "ГРУППОВОМ" in col.events[0].channel_prompt


async def test_dm_events_have_no_isolation_prompt():
    adapter = make_adapter()
    adapter._group_isolation = True
    col = Collector()
    adapter._message_handler = col
    await adapter._handle_update(_upd_message())
    for _ in range(50):
        if col.events:
            break
        await asyncio.sleep(0.01)
    assert col.events and not col.events[0].channel_prompt


async def test_collect_media_location_from_flat_fields(monkeypatch):
    """Геометка: координаты берутся с плоских полей вложения (после догрузки)."""
    from maxbot.adapter import MaxAdapter
    from maxbot.models import Attachment, Message, MessageBody

    class FakeCfg:
        extra = {}

    fresh = Message(body=MessageBody(attachments=[
        Attachment(type="location", latitude=11.1111, longitude=22.2222)]),
        chat_id=1, chat_type="dialog")

    class FakeClient:
        async def get_message(self, mid):
            return fresh

    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=None)
    msg = Message(body=MessageBody(mid="m1", attachments=[Attachment(type="location")]),
                  chat_id=1, chat_type="dialog")

    async def fake_refresh(m):
        return m.body.attachments

    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    _, _, texts = await adapter._collect_media(msg)
    assert "11.1111" in texts and "22.2222" in texts
    assert "openstreetmap" in texts


async def test_video_downloads_lowest_resolution(monkeypatch):
    """Видео агенту качаем в минимальном доступном разрешении — экономия токенов."""
    from maxbot.adapter import MaxAdapter
    from maxbot.models import Attachment, Message, MessageBody

    class FakeCfg:
        extra = {}

    class FakeClient:
        async def get_video_info(self, token):
            return {"urls": {"mp4_720": "http://v/720.mp4", "mp4_144": "http://v/144.mp4"}}

    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=None)
    msg = Message(body=MessageBody(attachments=[
        Attachment(type="video", payload={"url": "http://v/orig.mp4", "token": "T"})]),
        chat_id=1, chat_type="dialog")

    async def fake_refresh(m):
        return m.body.attachments

    got = {}

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        got["url"] = url or att.payload.get("url")
        return "/tmp/v.mp4"

    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)
    await adapter._collect_media(msg)
    assert got["url"] == "http://v/144.mp4"  # минимальная рендия, не оригинал


def test_lowest_video_url_helper():
    from maxbot.adapter import _lowest_video_url
    info = {"urls": {"mp4_1080": "a", "mp4_480": "b", "mp4_240": "c", "hls": "h"}}
    assert _lowest_video_url(info) == "c"
    assert _lowest_video_url({"urls": None}) is None
    assert _lowest_video_url({}) is None


async def test_video_frames_extracted_for_vision(monkeypatch):
    """Кадры из видео -> картинки агенту (ffmpeg); миниатюра не качается."""
    from maxbot import adapter as A
    from maxbot.adapter import MaxAdapter
    from maxbot.models import Attachment, Message, MessageBody

    class FakeCfg:
        extra = {}

    class FakeClient:
        async def get_video_info(self, token):
            return {"urls": {"mp4_144": "http://v/144.mp4"},
                    "thumbnail": {"url": "http://v/thumb.jpg"}}

    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=None)
    msg = Message(body=MessageBody(attachments=[
        Attachment(type="video", payload={"url": "http://v/orig.mp4", "token": "T"})]),
        chat_id=1, chat_type="dialog")

    async def fake_refresh(m):
        return m.body.attachments

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        assert url == "http://v/144.mp4"
        return "/tmp/v.mp4"

    cached = []

    def fake_cache(data, ext=".jpg"):
        cached.append((data, ext))
        return f"/tmp/frame_{len(cached)}.jpg"

    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)
    monkeypatch.setattr(A, "cache_image_from_bytes", fake_cache)

    def fake_frames(path, count=4):
        assert path == "/tmp/v.mp4"
        return [b"J1", b"J2", b"J3"]

    monkeypatch.setattr(A, "_extract_frames", fake_frames)
    paths, types, _ = await adapter._collect_media(msg)
    assert paths == ["/tmp/v.mp4", "/tmp/frame_1.jpg", "/tmp/frame_2.jpg", "/tmp/frame_3.jpg"]
    assert types == ["video/mp4", "image/jpeg", "image/jpeg", "image/jpeg"]
    assert cached == [(b"J1", ".jpg"), (b"J2", ".jpg"), (b"J3", ".jpg")]


def test_inline_text_helper(tmp_path):
    from maxbot.adapter import _inline_text
    md = tmp_path / "note.md"
    md.write_text("# Заголовок\n\nТекст заметки", encoding="utf-8")
    inline = _inline_text(str(md))
    assert "Заголовок" in inline and "Текст заметки" in inline
    assert "<<<DATA" in inline and "НЕ инструкции" in inline

    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02PNG")
    assert _inline_text(str(binary)) is None

    big = tmp_path / "big.txt"
    big.write_text("x" * 200_000, encoding="utf-8")
    assert _inline_text(str(big)) is None  # слишком большой — агенту путь, не текст


async def test_file_attachment_text_inlined(monkeypatch):
    from maxbot.adapter import MaxAdapter
    from maxbot.models import Attachment, Message, MessageBody

    class FakeCfg:
        extra = {}

    adapter = MaxAdapter(FakeCfg(), client=None, transport=None)
    msg = Message(body=MessageBody(attachments=[
        Attachment(type="file", payload={"filename": "note.md"})]),
        chat_id=1, chat_type="dialog")

    async def fake_refresh(m):
        return m.body.attachments

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        f = "/tmp/fake_note.md"
        open(f, "w").write("# План\nСделать всё")
        return f

    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)
    paths, types, texts = await adapter._collect_media(msg)
    assert paths and "НЕ инструкции" in texts and "План" in texts


async def test_inbound_message_marks_seen(monkeypatch):
    """Входящее сообщение помечается 'прочитано' (mark_seen) fire-and-forget."""
    actions = []

    adapter = make_adapter()

    async def fake_action(chat_id, action):
        actions.append((chat_id, action))

    monkeypatch.setattr(adapter._client, "chat_action", fake_action, raising=False)
    await adapter._handle_update(_upd_message())
    await asyncio.sleep(0.05)
    assert (100, "mark_seen") in actions


async def test_forwarded_message_content_processed(monkeypatch):
    """Пересылка: контент и вложения лежат инлайном в link.message — обрабатываем."""
    from maxbot.adapter import MaxAdapter
    from maxbot.models import parse_update

    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=None)

    fwd = {
        "update_type": "message_created", "marker": 1,
        "message": {
            "body": {"mid": "m2", "text": "", "attachments": []},
            "recipient": {"chat_id": 100, "chat_type": "dialog"},
            "sender": {"user_id": 42, "name": "Иван"},
            "timestamp": 1,
            "link": {
                "type": "forward",
                "message": {"mid": "m1", "text": "оки",
                            "attachments": [{"type": "file", "payload": {
                                "filename": "ТЗ.docx", "token": "T"}}]},
                "sender": {"user_id": 4460279, "name": "Эльвира"},
            },
        },
    }

    async def fake_dl(att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
        assert default_ext == ".docx"  # имя файла пересланного вложения дошло
        return "/tmp/fwd.docx"

    async def fake_refresh(m):
        return m.body.attachments

    monkeypatch.setattr(adapter, "_refreshed_attachments", fake_refresh)
    monkeypatch.setattr(adapter, "_download_cached", fake_dl)

    upd = parse_update(fwd)
    paths, types, texts = await adapter._collect_media(upd.message)
    assert "[переслано от Эльвира" in texts and "НЕ инструкции" in texts
    assert "оки" in texts and "<<<DATA" in texts
    assert paths == ["/tmp/fwd.docx"]
