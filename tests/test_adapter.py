
import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.adapter import MaxAdapter, check_requirements


class FakeCfg:
    def __init__(self, extra=None):
        self.extra = extra or {}


class FakeClient:
    """Двойник MaxClient для тестов адаптера."""

    def __init__(self):
        self.sent = []          # (chat_id, text)
        self.edited = {}
        self.deleted = []
        self.actions = []
        self._mid = 0

    async def get_me(self):
        from maxbot.models import User
        return User(user_id=999, name="Bot")

    async def send_message(self, chat_id, text, **kw):
        self._mid += 1
        self.sent.append((chat_id, text))
        return f"mid.{self._mid}"

    async def edit_message(self, message_id, text, **kw):
        self.edited[message_id] = text
        return True

    async def delete_message(self, message_id):
        self.deleted.append(message_id)
        return True

    async def chat_action(self, chat_id, action="typing_on"):
        self.actions.append((chat_id, action))
        return True

    async def set_commands(self, commands):
        self.commands = commands
        return True

    async def get_chat(self, chat_id):
        return {"chat_id": chat_id, "type": "chat", "title": "Группа"}


class FakeTransport:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self, on_update):
        self.started = True
        self.on_update = on_update

    async def stop(self):
        self.stopped = True

    def healthy(self):
        return self.started and not self.stopped


def make_adapter(client=None, transport=None):
    adapter = MaxAdapter(FakeCfg(), client=client, transport=transport)
    return adapter


async def test_connect_registers_bot_commands_and_locks():
    client, transport = FakeClient(), FakeTransport()
    adapter = make_adapter(client, transport)
    assert await adapter.connect()
    assert adapter.is_connected and transport.started
    assert client.commands and client.commands[0] == {"name": "new", "description": "Новая сессия"}
    await adapter.disconnect()


async def test_send_sanitizes_segments_and_returns_last_mid():
    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)
    result = await adapter.send("100", "# Заголовок\n" + "длинный текст " * 600)
    assert result.success and result.message_id == f"mid.{len(client.sent)}"
    assert "**Заголовок**" == client.sent[0][1].split("\n")[0]
    assert all(len(t) <= 3900 for _, t in client.sent)


async def test_send_media_via_uploader(tmp_path, monkeypatch):
    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)

    async def fake_upload(path, kind):
        return "TOK-" + kind

    monkeypatch.setattr(adapter._uploader, "upload", fake_upload)
    f = tmp_path / "img.png"
    f.write_bytes(b"\x89PNG")
    res = await adapter.send_image_file("100", str(f), caption="каптион")
    assert res.success
    chat_id, text = client.sent[-1]
    assert chat_id == 100


async def test_delete_and_typing():
    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)
    assert await adapter.delete_message("100", "mid.5")
    assert "mid.5" in client.deleted
    await adapter.send_typing("100")
    assert client.actions == [(100, "typing_on")]


def test_check_requirements_env(monkeypatch):
    monkeypatch.delenv("MAX_ACCESS_TOKEN", raising=False)
    assert check_requirements() is False
    monkeypatch.setenv("MAX_ACCESS_TOKEN", "T")
    assert check_requirements() is True


async def test_connect_webhook_without_url_releases_lock(monkeypatch):
    monkeypatch.setenv("MAX_UPDATES_MODE", "webhook")
    adapter = make_adapter(FakeClient(), FakeTransport())
    assert await adapter.connect() is False          # config_missing
    assert adapter._lock_identity is None            # lock освобождён
    await adapter.disconnect()                       # безопасен после неудачи


async def test_send_attachment_error_returns_failure(monkeypatch, tmp_path):
    from maxbot.max_api import MaxApiError

    client = FakeClient()
    adapter = make_adapter(client, FakeTransport())
    await adapter._fake_connect_for_tests(client)

    async def boom(path, kind):
        raise MaxApiError(400, "upload failed", code="bad")

    monkeypatch.setattr(adapter._uploader, "upload", boom)
    f = tmp_path / "img.png"
    f.write_bytes(b"x")
    res = await adapter.send_image_file("100", str(f))
    assert res.success is False and "upload failed" in res.error


async def test_marker_survives_restart_via_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = MaxAdapter(FakeCfg(), client=FakeClient(), transport=FakeTransport())
    adapter._persist_marker(7)
    assert (tmp_path / "maxbot_marker").read_text().strip() == "7"

    fresh = MaxAdapter(FakeCfg(), client=FakeClient(), transport=FakeTransport())
    assert fresh._marker == 7                       # файл прочитан при конструировании
    assert fresh._make_transport().marker == 7      # polling стартует с сохранённого маркера


async def test_marker_in_memory_extra_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "maxbot_marker").write_text("3")
    adapter = MaxAdapter(FakeCfg(extra={"marker": 9}), client=FakeClient(),
                         transport=FakeTransport())
    assert adapter._marker == 9                     # живой процесс знает свежее значение


async def test_connect_warns_when_set_commands_fails(caplog):
    import logging

    class FailingCommandsClient(FakeClient):
        async def set_commands(self, commands):
            raise RuntimeError("boom")

    client, transport = FailingCommandsClient(), FakeTransport()
    adapter = make_adapter(client, transport)
    with caplog.at_level(logging.WARNING, logger="maxbot.adapter"):
        assert await adapter.connect()
    assert adapter.is_connected                       # сбой регистрации команд не фатален
    assert any("зарегистрировать команды" in r.message for r in caplog.records)
    await adapter.disconnect()
