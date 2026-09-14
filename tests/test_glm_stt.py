"""glm-video STT: аудио → mp4 → мультимодальная модель через video_url."""
import os

import pytest

pytest.importorskip("agent.transcription_provider")

from maxbot import glm_stt  # noqa: E402


def make_provider():
    return glm_stt.GlmVideoStt()


def test_provider_identity():
    p = make_provider()
    assert p.name == "glm"
    assert p.default_model() == "glm-5.3-flash"


def test_is_available_requires_key_and_ffmpeg(monkeypatch):
    p = make_provider()
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setattr(glm_stt.shutil, "which", lambda b: "/usr/bin/ffmpeg")
    assert p.is_available() is False

    monkeypatch.setenv("GLM_API_KEY", "k" * 40)
    monkeypatch.setattr(glm_stt.shutil, "which", lambda b: None)
    assert p.is_available() is False

    monkeypatch.setattr(glm_stt.shutil, "which", lambda b: "/usr/bin/ffmpeg")
    assert p.is_available() is True


def test_transcribe_wraps_audio_into_mp4_and_calls_model(monkeypatch, tmp_path):
    calls = {}

    def fake_to_mp4(src):
        calls["src"] = src
        f = tmp_path / "wrapped.mp4"
        f.write_bytes(b"MP4DATA")
        return str(f)

    def fake_post(b64, model, prompt):
        calls["b64_len"] = len(b64)
        calls["model"] = model
        calls["prompt"] = prompt
        return "  привет мир  "

    monkeypatch.setattr(glm_stt, "_to_mp4", fake_to_mp4)
    monkeypatch.setattr(glm_stt, "_post_chat", fake_post)

    src = tmp_path / "voice.ogg"
    src.write_bytes(b"OGG")
    res = make_provider().transcribe(str(src), language="ru", prompt="Гермес")

    assert res["success"] is True and res["transcript"] == "привет мир"
    assert res["provider"] == "glm"
    assert calls["src"] == str(src)
    assert calls["model"] == "glm-5.3-flash"
    assert calls["b64_len"] > 0
    assert "ru" in calls["prompt"] and "Гермес" in calls["prompt"]


def test_transcribe_model_override_and_errors(monkeypatch, tmp_path):
    p = make_provider()

    def boom(*a, **kw):
        raise RuntimeError("api down")

    def fake_ok(b64, model, prompt):
        return "текст"

    wav = tmp_path / "v.wav"
    wav.write_bytes(b"WAV")

    wrapped = tmp_path / "wrapped.mp4"
    wrapped.write_bytes(b"MP4")

    monkeypatch.setattr(glm_stt, "_to_mp4",
                        lambda src: (_ for _ in ()).throw(RuntimeError("no ffmpeg")))
    res = p.transcribe(str(wav))
    assert res["success"] is False and res["transcript"] == "" and "no ffmpeg" in res["error"]

    def fake_wrap(src):
        wrapped.write_bytes(b"MP4")  # transcribe удаляет обёртку после чтения
        return str(wrapped)

    monkeypatch.setattr(glm_stt, "_to_mp4", fake_wrap)
    monkeypatch.setattr(glm_stt, "_post_chat", boom)
    res = p.transcribe(str(wav))
    assert res["success"] is False and "api down" in res["error"]

    monkeypatch.setattr(glm_stt, "_post_chat", fake_ok)
    res = p.transcribe(str(wav), model="glm-custom")
    assert res["success"] is True and res["transcript"] == "текст"


def test_to_mp4_invokes_ffmpeg_with_black_frame(monkeypatch, tmp_path):
    cmds = {}

    class FakeRun:
        def __call__(self, cmd, **kw):
            cmds["cmd"] = cmd
            open(cmd[-1], "wb").write(b"mp4")

    monkeypatch.setattr(glm_stt.subprocess, "run", FakeRun())
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"OGG")
    out = glm_stt._to_mp4(str(audio))
    cmd = cmds["cmd"]
    assert cmd[0] == "ffmpeg"
    assert str(audio) in cmd and cmd[-1] == out
    assert "-shortest" in cmd  # видео-дорожка режется по длине аудио


def test_post_chat_sends_video_url(monkeypatch):
    import json as _json

    sent = {}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return _json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["auth"] = req.headers.get("Authorization")
        sent["body"] = _json.loads(req.data.decode())
        return FakeResp({"choices": [{"message": {"content": "текст ответа"}}]})

    monkeypatch.setenv("GLM_API_KEY", "KEY123")
    monkeypatch.setenv("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")
    expected_auth = "Bearer " + os.environ["GLM_API_KEY"]
    monkeypatch.setattr(glm_stt.urllib.request, "urlopen", fake_urlopen)
    out = glm_stt._post_chat("QkFTRTY0", "glm-5.3-flash", "промпт")
    assert out == "текст ответа"
    assert sent["url"].endswith("/chat/completions")
    assert sent["auth"] == expected_auth
    content = sent["body"]["messages"][0]["content"]
    assert content[0]["type"] == "video_url"
    assert content[0]["video_url"]["url"].startswith("data:video/mp4;base64,QkFTRTY0")
    assert content[1]["type"] == "text"


def test_register_registers_provider():
    registered = []

    class FakeCtx:
        def __getattr__(self, method):
            return lambda *a, **kw: None

        def register_transcription_provider(self, provider):
            registered.append(provider)

    import maxbot
    maxbot.register(FakeCtx())
    assert any(getattr(p, "name", "") == "glm" for p in registered)
