"""STT-провайдер «glm-video»: аудио → mp4 → мультимодальная модель (video_url).

Модели zai в подписке не принимают чистый аудио-ввод (только text/image_url/
video_url), но слышат аудиодорожку внутри video_url. Провайдер заворачивает
голосовое в mp4 одним чёрным кадром и отправляет на chat/completions.

Включение: stt.provider: glm-video (модель — GLM_STT_MODEL, по умолчанию
glm-5.3-flash; ключ/эндпоинт — GLM_API_KEY/GLM_BASE_URL, наследуя основной).
"""
import base64
import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request

try:  # окружение без hermes — модуль всё ещё импортируем (регистрация невозможна)
    from agent.transcription_provider import TranscriptionProvider as _TranscriptionProvider
except ImportError:  # pragma: no cover
    _TranscriptionProvider = object

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "glm-5.3-flash"
_API_TIMEOUT = 180
_FFMPEG_TIMEOUT = 300
_MAX_MP4_BYTES = 24 * 1024 * 1024  # base64 ~4/3 — держим запрос в разумных пределах


def _glm_env():
    key = os.getenv("GLM_API_KEY") or ""
    base = (os.getenv("GLM_BASE_URL") or "https://api.z.ai/api/coding/paas/v4").rstrip("/")
    return key, base


def _to_mp4(audio_path: str) -> str:
    """Аудио → mp4 (чёрный кадр 1 fps + исходная дорожка), длительность по аудио."""
    fd, out = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", "color=c=black:s=160x120:r=1:d=86400",
           "-i", audio_path, "-shortest",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "64k", out]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(out)
        raise
    return out


def _post_chat(mp4_b64: str, model: str, prompt: str) -> str:
    key, base = _glm_env()
    body = {
        "model": model,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": [
            {"type": "video_url",
             "video_url": {"url": f"data:video/mp4;base64,{mp4_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
        data = json.loads(resp.read())
    return (data["choices"][0]["message"].get("content") or "").strip()


class GlmVideoStt(_TranscriptionProvider):
    """Транскрипция голосовых мультимодальной моделью через video_url-контейнер."""

    @property
    def name(self) -> str:
        return "glm"

    @property
    def display_name(self) -> str:
        return "GLM video-url STT (maxbot)"

    def is_available(self) -> bool:
        key, _ = _glm_env()
        return bool(key) and shutil.which("ffmpeg") is not None

    def default_model(self):
        return os.getenv("GLM_STT_MODEL") or DEFAULT_MODEL

    def transcribe(self, file_path, *, model=None, language=None, **extra):
        try:
            used_model = model or self.default_model()
            prompt = ("Расшифруй дословно речь из аудиодорожки видео. "
                      "Выведи только расшифровку, без комментариев.")
            if language:
                prompt += f" Язык речи: {language}."
            if extra.get("prompt"):
                prompt += f" Словарь/контекст: {extra['prompt']}"
            mp4 = _to_mp4(str(file_path))
            try:
                raw = open(mp4, "rb").read()
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(mp4)
            if len(raw) > _MAX_MP4_BYTES:
                return {"success": False, "transcript": "", "provider": self.name,
                        "error": f"mp4 слишком большой: {len(raw)} байт"}
            text = _post_chat(base64.b64encode(raw).decode(), used_model, prompt).strip()
            if not text:
                return {"success": False, "transcript": "", "provider": self.name,
                        "error": "пустой ответ модели"}
            return {"success": True, "transcript": text, "provider": self.name}
        except Exception as exc:  # noqa: BLE001 — контракт: не поднимать
            logger.warning("glm-video STT: %s", exc)
            return {"success": False, "transcript": "", "provider": self.name,
                    "error": str(exc)}
