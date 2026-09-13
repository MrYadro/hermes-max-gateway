import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.hooks import _apply_yaml_config, _env_enablement, _standalone_send


def test_env_enablement_none_without_token(monkeypatch):
    monkeypatch.delenv("MAX_ACCESS_TOKEN", raising=False)
    assert _env_enablement() is None


def test_env_enablement_seeds_extra_and_home(monkeypatch):
    monkeypatch.setenv("MAX_ACCESS_TOKEN", "T")
    monkeypatch.setenv("MAX_UPDATES_MODE", "polling")
    seed = _env_enablement()
    assert seed["access_token"] == "T" and seed["updates_mode"] == "polling"
    assert "home_channel" not in seed or isinstance(seed["home_channel"], dict)


def test_apply_yaml_config_maps_keys(monkeypatch):
    monkeypatch.delenv("MAX_ACCESS_TOKEN", raising=False)
    extra = _apply_yaml_config(
        {"access_token": "YTok", "updates_mode": "webhook", "home_channel": "-100"},
        platform_cfg=None)
    assert extra["access_token"] == "YTok" and extra["updates_mode"] == "webhook"
    import os
    assert os.environ.get("MAX_ACCESS_TOKEN") == "YTok"


async def test_standalone_send_segments(monkeypatch):
    import httpx
    import respx

    with respx.mock:
        sent = []
        respx.post("http://test.max/messages").mock(
            side_effect=lambda request: sent.append(request) or httpx.Response(
                200, json={"message": {"body": {"mid": f"m{len(sent)}"}}}))

        class _Cfg:
            extra = {"access_token": "T", "api_base": "http://test.max"}

        result = await _standalone_send(_Cfg(), "100", "x" * 9000)
    assert result["success"] is True and len(sent) == 3  # 9000 символов → 3 сегмента


async def test_standalone_send_media_files(tmp_path):
    import json

    import httpx
    import respx

    with respx.mock:
        respx.post("http://test.max/uploads", params={"type": "image"}).mock(
            return_value=httpx.Response(200, json={"url": "http://test.max/upload-slot"}))
        respx.post("http://test.max/upload-slot").mock(
            return_value=httpx.Response(200, json={"token": "TOK-IMG1"}))
        sent = []
        respx.post("http://test.max/messages").mock(
            side_effect=lambda request: sent.append(request) or httpx.Response(
                200, json={"message": {"body": {"mid": f"m{len(sent)}"}}}))

        class _Cfg:
            extra = {"access_token": "T", "api_base": "http://test.max"}

        img = tmp_path / "pic.jpg"
        img.write_bytes(b"\xff\xd8fakejpg")
        result = await _standalone_send(_Cfg(), "100", "вот картинка", media_files=[str(img)])

    assert result["success"] is True
    bodies = [json.loads(r.content) for r in sent]
    assert any("вот картинка" in b.get("text", "") for b in bodies)
    with_attachment = [b for b in bodies if b.get("attachments")]
    assert with_attachment, "вложение должно уехать отдельным сообщением"
    att = with_attachment[0]["attachments"][0]
    assert att["type"] == "image" and att["payload"]["token"] == "TOK-IMG1"


async def test_standalone_send_media_upload_failure_notes_undelivered(tmp_path):
    import json

    import httpx
    import respx

    with respx.mock:
        respx.post("http://test.max/uploads").mock(
            return_value=httpx.Response(400, json={"code": "bad", "message": "no"}))
        sent = []
        respx.post("http://test.max/messages").mock(
            side_effect=lambda request: sent.append(request) or httpx.Response(
                200, json={"message": {"body": {"mid": f"m{len(sent)}"}}}))

        class _Cfg:
            extra = {"access_token": "T", "api_base": "http://test.max"}

        img = tmp_path / "pic.jpg"
        img.write_bytes(b"\xff\xd8fakejpg")
        result = await _standalone_send(_Cfg(), "100", "текст", media_files=[str(img)])

    assert result["success"] is True  # текст доставлен, вложение — нет
    bodies = [json.loads(r.content) for r in sent]
    assert any("[не доставлено: pic.jpg]" in b.get("text", "") for b in bodies)
