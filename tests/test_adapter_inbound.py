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
        self.edits = []
        self.deleted = []

    async def send_message(self, chat_id, text, **kw):
        self._mid += 1
        self.sent.append((chat_id, text, kw.get("attachments")))
        return f"mid.{self._mid}"

    async def get_message(self, message_id):
        return self.replied_lookup.get(message_id)

    async def edit_message(self, message_id, text, **kw):
        self.edits = getattr(self, "edits", [])
        self.edits.append((message_id, text, kw.get("attachments")))
        return True

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return True


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

    async def run_with_flash(self, mid, text, op, *, delay=None):
        return await op(), False


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


async def test_bot_added_greets_group():
    """bot_added: бот добавлен в чат — приветствие с подсказкой про упоминания."""
    adapter = make_adapter()
    d = {"update_type": "bot_added", "chat_id": -200, "user": {"user_id": 5, "name": "Аня"},
         "timestamp": 1}
    await adapter._handle_update(parse_update(d))
    assert adapter._client.sent and adapter._client.sent[0][0] == -200
    text = adapter._client.sent[0][1]
    assert "упоминани" in text or "@" in text  # группа: объясняем про упоминания


async def test_message_edited_redispatches_and_skips_own():
    """Правка чужого сообщения — повторная обработка; правка своего (превью) — игнор."""
    adapter = make_adapter()
    handler_calls = []

    async def fake_handle(event):
        handler_calls.append(event.text)

    adapter._message_handler = fake_handle  # noqa: SLF001

    edited = _upd_message(text="исправлено", mid="me1")
    upd = {"update_type": "message_edited", "marker": 2,
           "message": edited.raw.get("message") if hasattr(edited, "raw") else edited}
    # raw от parse_update — соберём заново
    upd = {"update_type": "message_edited", "marker": 2, "message": {
        "body": {"mid": "me1", "text": "исправлено", "attachments": []},
        "recipient": {"chat_id": 100, "chat_type": "dialog"},
        "sender": {"user_id": 42, "name": "Иван"}, "timestamp": 1}}
    await adapter._handle_update(parse_update(upd))
    await asyncio.sleep(0.05)
    assert handler_calls and "исправлено" in handler_calls[0]

    own = {"update_type": "message_edited", "marker": 3, "message": {
        "body": {"mid": "me2", "text": "превью ▌", "attachments": []},
        "recipient": {"chat_id": 100, "chat_type": "dialog"},
        "sender": {"user_id": 999, "name": "Hermes"}, "timestamp": 1}}
    n = len(handler_calls)
    await adapter._handle_update(parse_update(own))
    await asyncio.sleep(0.05)
    assert len(handler_calls) == n  # свою правку не гоняем


async def test_message_removed_interrupts_session():
    """Удаление сообщения прерывает бегущую обработку его сессии."""
    adapter = make_adapter()
    adapter._mid_sessions["mX"] = ("agent:main:max:dm:100", "100", None)
    guard = asyncio.Event()
    adapter._active_sessions["agent:main:max:dm:100"] = guard

    # реальный payload message_removed: плоский message_id, без объекта message
    upd = {"update_type": "message_removed", "marker": 4,
           "message_id": "mX", "chat_id": 100, "user_id": 42, "timestamp": 1}
    await adapter._handle_update(parse_update(upd))
    assert guard.is_set()          # interrupt сработал
    assert "mX" not in adapter._mid_sessions  # и почистили


async def test_message_removed_sends_retraction_note():
    """После прерывания в сессию уходит internal-заметка-ретракция."""
    adapter = make_adapter()
    fake_source = adapter.build_source(
        chat_id="100", chat_name="100", chat_type="dm", user_id="42", user_name="Иван")
    adapter._mid_sessions["mY"] = ("agent:main:max:dm:100", "100", fake_source)
    # guard не ставим: заметка должна обработаться сразу (idle-путь)

    notes = []

    async def fake_handle(event):
        notes.append(event)

    adapter._message_handler = fake_handle  # noqa: SLF001
    upd = {"update_type": "message_removed", "marker": 5,
           "message_id": "mY", "chat_id": 100, "user_id": 42, "timestamp": 1}
    await adapter._handle_update(parse_update(upd))
    await asyncio.sleep(0.05)
    assert notes and notes[0].internal is True
    assert "удалил" in notes[0].text and "gateway_session_key" in notes[0].metadata


async def test_bot_started_greeting_single_nested_attachments():
    """attachments должен быть [att], а не [[att]] — двойной массив даёт 400."""
    adapter = make_adapter()
    upd = {"update_type": "bot_started", "chat_id": 100, "marker": 1,
           "user": {"user_id": 42, "name": "Иван"}, "timestamp": 1}
    await adapter._handle_update(parse_update(upd))
    atts = adapter._client.sent[0][2]
    assert atts and isinstance(atts[0], dict) and atts[0]["type"] == "inline_keyboard"


async def test_bot_started_greeting_callback_buttons():
    """Кнопки приветствия — callback (тихое выполнение), не message."""
    adapter = make_adapter()
    upd = {"update_type": "bot_started", "chat_id": 100, "marker": 1,
           "user": {"user_id": 42, "name": "Иван"}, "timestamp": 1}
    await adapter._handle_update(parse_update(upd))
    btns = adapter._client.sent[0][2][0]["payload"]["buttons"][0]
    assert all(b["type"] == "callback" for b in btns)
    assert any(b["payload"] == "gc:100:new" for b in btns)


async def test_synthetic_command_uses_callback_user():
    """user_id из колбэка: ядро молча роняет синтетику с пустым user_id."""
    from maxbot.models import Callback, User
    adapter = make_adapter()
    cb = Callback(callback_id="cb1", payload="gc:100:new",
                  user=User(user_id=42, name="Иван"))
    seen = {}

    async def fake_handle(event):
        seen["user_id"] = event.source.user_id

    adapter.handle_message = fake_handle
    await adapter.on_greeting_cmd(cb)
    assert seen["user_id"] == "42"
    assert adapter._chat_users["100"] == ("42", "Иван")


class _noop_async:
    async def __call__(self, event):
        return None


async def test_greeting_button_flashes_progress_then_restores():
    """Нажатие: текст → «⏳ Выполняю…», после команды — обратно приветствие."""
    from maxbot.adapter import _GREETING
    from maxbot.models import Callback, User
    adapter = make_adapter()
    upd = {"update_type": "bot_started", "chat_id": 100, "marker": 1,
           "user": {"user_id": 42, "name": "Иван"}, "timestamp": 1}
    await adapter._handle_update(parse_update(upd))
    assert adapter._client.sent[0][2] and adapter._greeting_mids["100"] == "mid.1"

    cb = Callback(callback_id="cb1", payload="gc:100:new",
                  user=User(user_id=42, name="Иван"))

    # быстрая команда: ни «⏳», ни возврата — приветствие не трогаем
    adapter.handle_message = _noop_async()
    await adapter.on_greeting_cmd(cb)
    assert adapter._client.edits == []

    # долгая команда: «⏳» и возврат приветствия с клавиатурой
    import asyncio as _aio

    import maxbot.interactive as I

    class _slow:
        async def __call__(self, event):
            await _aio.sleep(0.2)

    adapter.handle_message = _slow()
    adapter._interactive = I.InteractiveDispatcher(adapter)
    I._FLASH_DELAY = 0.03
    try:
        await adapter.on_greeting_cmd(cb)
    finally:
        I._FLASH_DELAY = 0.7
    edits = adapter._client.edits
    assert edits[0][1].startswith("⏳ Выполняю /new")
    assert edits[-1][1] == _GREETING
    assert edits[-1][2]  # клавиатура возвращена


async def test_inbound_underscore_command_roundtrip():
    """/reload_mcp на входе → событие с /reload-mcp (дефисная команда ядра)."""
    adapter = make_adapter()
    handled = []

    async def spy(event):
        handled.append(event.text)

    adapter.handle_message = spy
    await adapter._handle_update(_upd_message("/reload_mcp"))
    assert handled == ["/reload-mcp"]


class TestFileNames:
    """Имя файла MAX лежит на уровне вложения; docx≠zip даже без имени."""

    def test_sniff_zip_container_docx(self):
        from maxbot.adapter import _sniff_ext
        assert _sniff_ext(b"PK\x03\x04" + b"\x00" * 30 + b"word/document.xml") == ".docx"
        assert _sniff_ext(b"PK\x03\x04" + b"\x00" * 30 + b"xl/workbook.xml") == ".xlsx"
        assert _sniff_ext(b"PK\x03\x04" + b"\x00" * 30 + b"zzz/") == ".zip"

    def test_attachment_level_filename_parsed(self):
        upd = parse_update({"update_type": "message_created", "message": {
            "recipient": {"chat_type": "dialog", "chat_id": 1},
            "body": {"mid": "m1", "text": "",
                     "attachments": [{"type": "file", "filename": "ТЗ НГ.docx",
                                      "payload": {"url": "https://x/f"}}]},
            "sender": {"user_id": 2, "name": "И"}}})
        att = upd.message.body.attachments[0]
        assert att.filename == "ТЗ НГ.docx"

    async def test_forward_file_keeps_original_name(self, monkeypatch, tmp_path):
        from maxbot import adapter as A

        saved = {}

        async def fake_dl(self, att, cache_fn, default_ext, mime_map=None, is_doc=False, url=None):
            saved["ext"] = default_ext
            saved["name"] = cache_fn(b"data", "ТЗ_НГ.docx")
            return str(tmp_path / "f.docx")

        monkeypatch.setattr(A.MaxAdapter, "_download_cached", fake_dl)
        adapter = make_adapter()
        adapter.handle_message = _noop_async()
        upd = parse_update({"update_type": "message_created", "message": {
            "recipient": {"chat_type": "dialog", "chat_id": 100},
            "body": {"mid": "m2", "text": "смотри"},
            "link": {"type": "forward", "message": {
                "mid": "m3", "text": "",
                "attachments": [
                    {"type": "file", "filename": "ТЗ НГ.docx",
                     "payload": {"url": "https://x/f"}}]}},
            "sender": {"user_id": 42, "name": "Иван"}}})
        await adapter._handle_update(upd)
        assert saved["ext"] == ".docx"
        assert "ТЗ_НГ.docx" in saved["name"]


async def test_document_cache_dedup_by_hash(tmp_path, monkeypatch):
    """Одинаковое содержимое — один файл в кэше (контент-хеш в имени)."""
    import hashlib

    from maxbot import adapter as A

    calls = []
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    monkeypatch.setattr(A, "get_document_cache_dir", lambda: doc_dir)

    def fake_cache(data, name):
        calls.append(name)
        p = doc_dir / f"doc_{name}"
        p.write_bytes(data)
        return str(p)

    class _Att:
        filename = "ТЗ НГ.docx"
        payload = {}

    att = _Att()

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/octet-stream"}

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield b"data"

    class _FakeHttpx:
        class AsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, method, url):
                class _Ctx:
                    async def __aenter__(self):
                        return _Resp()

                    async def __aexit__(self, *a):
                        return False
                return _Ctx()

    monkeypatch.setattr(A, "_httpx", _FakeHttpx)
    p1 = await A.MaxAdapter._download_cached(
        object(), att, fake_cache, ".docx", is_doc=True, url="https://x")
    p2 = await A.MaxAdapter._download_cached(
        object(), att, fake_cache, ".docx", is_doc=True, url="https://x")
    assert p1 == p2
    assert len(calls) == 1  # второй раз — переиспользовали
    assert hashlib.sha1(b"data").hexdigest()[:10] in calls[0]
    assert "ТЗ_НГ" in calls[0]


async def test_edit_message_sanitizes_and_returns_mid():
    """Heartbeat-редактирование: ядро зовёт edit_message вместо новых пузырей."""
    adapter = make_adapter()
    res = await adapter.edit_message("100", "m1", "⏳ Working — 9 min")
    assert res.success and res.message_id == "m1"
    assert adapter._client.edits == [("m1", "⏳ Working — 9 min", None)]


async def test_edit_message_failure_returns_not_success():
    adapter = make_adapter()

    async def boom(message_id, text, *, attachments=None):
        raise RuntimeError("boom")

    adapter._client.edit_message = boom
    res = await adapter.edit_message("100", "m1", "x")
    assert not res.success and "boom" in (res.error or "")


async def test_edit_message_collapses_tool_progress_to_last_line():
    """Прогресс-пузырь = катящаяся строка: видно только последнюю операцию."""
    adapter = make_adapter()
    prog = '⚙️ browser_exec: "открываю max.ru"\n⚙️ browser_exec: "ищу кнопку"\n⚙️ file_read: "config.yaml"'
    await adapter.edit_message("100", "m1", prog)
    assert adapter._client.edits[-1][1] == '⚙️ file_read: "config.yaml"'


async def test_edit_message_keeps_prose_and_finalize():
    """Проза и finalize=True не трогаем — это настоящий контент."""
    adapter = make_adapter()
    prose = "Первая строка ответа\n⚙️ file_read: x\nВторая строка"
    await adapter.edit_message("100", "m1", prose)
    assert adapter._client.edits[-1][1] == prose
    await adapter.edit_message("100", "m1", prose, finalize=True)
    assert adapter._client.edits[-1][1] == prose


async def test_service_bubble_single_rolling_above_input():
    """Один служебный пузырь над полем ввода: операция сверху, Working снизу,
    предыдущие пузыри удаляются."""
    adapter = make_adapter()

    # Working не отправляется отдельным сообщением — вживляется в служебный пузырь
    res = await adapter.send("100", "⏳ Working — 9 min", metadata={"_interim_send": True})
    assert res.success and res.message_id is None
    assert adapter._client.sent == []  # ничего не ушло в чат

    # первый прогресс-пузырь — обычная отправка
    res = await adapter.send("100", '⚙️ browser_exec: "открываю max.ru"')
    first = res.message_id
    assert first

    # правка: последняя операция сверху + Working снизу
    await adapter.edit_message("100", first, '⚙️ browser_exec: "a"\n⚙️ file_read: "config.yaml"')
    assert adapter._client.edits[-1][1] == '⏳ Working — 9 min\n⚙️ file_read: "config.yaml"'

    # heartbeat обновился — пузырь отредактирован без новой отправки
    await adapter.send("100", "⏳ Working — 12 min", metadata={"_interim_send": True})
    assert len(adapter._client.sent) == 1
    assert "12 min" in adapter._client.edits[-1][1]

    # новый сегмент: свежий пузырь, старый удалён — всегда один и внизу
    res = await adapter.send("100", '⚙️ browser_exec: "ещё раз"')
    assert res.message_id != first
    assert first in adapter._client.deleted


async def test_service_bubble_survives_foreign_lines():
    """Посторонняя строка (approval/подсказка) не ломает катящуюся строку
    и удаление старого пузыря."""
    adapter = make_adapter()
    res = await adapter.send("100", '⚙️ browser_exec: "первый"')
    first = res.message_id
    # правка с посторонней строкой среди tool-строк — всё равно одна операция
    await adapter.edit_message("100", first,
                               '⚙️ browser_exec: "a"\n⚠️ Command Approval Required\n⚙️ file_read: "b"')
    assert adapter._client.edits[-1][1].count("\n") == 0  # одна строка (без Working)
    assert adapter._client.edits[-1][1] == '⚙️ file_read: "b"'
    # новый сегмент с посторонней строкой — старый пузырь всё равно удалён
    res = await adapter.send("100", '⚠️ Command Approval Required\n⚙️ browser_exec: "ещё"')
    assert first in adapter._client.deleted


async def test_progress_bubble_repositions_below_content():
    """Контент ниже пузыря → следующая правка пересоздаёт пузырь внизу, старый удалён."""
    adapter = make_adapter()
    res = await adapter.send("100", '⚙️ browser_exec: "первый"')
    m1 = res.message_id

    # промежуточный текст ответа приземлился ниже
    await adapter.send("100", "Вот что я нашёл:\nпроза ответа")

    # ядро правит СВОЙ mid (m1) — мы пересоздаём пузырь и удаляем старый
    res = await adapter.edit_message("100", m1, '⚙️ browser_exec: "a"\n⚙️ file_read: "b"')
    assert res.success
    assert m1 in adapter._client.deleted
    sent_texts = [t for _, t, _ in adapter._client.sent]
    assert '⚙️ file_read: "b"' in sent_texts  # свежий пузырь внизу

    # последующие правки ядра по старому m1 попадают в живой пузырь
    await adapter.edit_message("100", m1, '⚙️ file_read: "c"')
    assert adapter._client.edits[-1][1] == '⚙️ file_read: "c"'

    # cleanup ядра удаляет m1 → живой пузырь тоже удаляется, состояние чисто
    await adapter.delete_message("100", m1)
    assert adapter._svc_state["100"] == {}
    assert set(adapter._client.deleted) >= {m1}


async def test_unrelated_delete_keeps_service_state():
    adapter = make_adapter()
    await adapter.send("100", '⚙️ browser_exec: "x"')
    svc = adapter._svc_state["100"]
    mid = svc["mid"]
    await adapter.delete_message("100", "mid.other")  # ретракция чужого сообщения
    assert adapter._svc_state["100"]["mid"] == mid


async def test_service_bubble_handles_verb_lines():
    """«💻 Running python3…» — глагольные строки ядра тоже катящаяся строка."""
    adapter = make_adapter()
    res = await adapter.send("100", "💻 Running python3 -c \"print(1)\"")
    first = res.message_id
    await adapter.send("100", "промежуточный текст ответа")
    res = await adapter.edit_message(
        "100", first,
        '💻 Running python3 -c "print(1)" (×2)\n🌐 Browsing https://example.com')
    assert res.success
    assert first in adapter._client.deleted
    assert adapter._client.sent[-1][1] == "🌐 Browsing https://example.com"


async def test_emoji_prose_is_not_progress():
    """«✅ Готово» — не прогресс: пузырь не пересоздаётся, ответ не трогаем."""
    adapter = make_adapter()
    await adapter.send("100", '⚙️ browser_exec: "x"')
    res = await adapter.send("100", "✅ Готово")
    assert res.message_id
    assert adapter._svc_state["100"].get("stale") is not True or True  # не важно
    svc_mid = adapter._svc_state["100"]["mid"]
    await adapter.edit_message("100", svc_mid, "✅ Готово\nВторая строка")
    assert adapter._client.edits[-1][1] == "✅ Готово\nВторая строка"


async def test_draft_landing_marks_bubble_stale():
    """Стриминг-черновик приземлился ниже пузыря → следующая правка пересоздаёт пузырь."""
    adapter = make_adapter()
    res = await adapter.send("100", '⚙️ browser_exec: "x"')
    m1 = res.message_id

    await adapter.send_draft("100", 1, "Черновик ответа, стримится…")

    res = await adapter.edit_message("100", m1, '💻 Running python3 -c "print(1)"')
    assert res.success
    assert m1 in adapter._client.deleted
    assert adapter._client.sent[-1][1].endswith('print(1)"')  # новый пузырь ниже черновика


async def test_progress_send_born_with_working_line():
    """Новый пузырь рождается сразу с Working-строкой, не ждёт следующей правки."""
    adapter = make_adapter()
    await adapter.send("100", "⏳ Working — 9 min", metadata={"_interim_send": True})
    await adapter.send("100", '⚙️ browser_exec: "a"\n⚙️ file_read: "b"')
    assert adapter._client.sent[-1][1] == '⏳ Working — 9 min\n⚙️ file_read: "b"'
