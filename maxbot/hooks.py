"""Хуки регистрации плагина: env/yaml-конфиг, визард, cron-доставка."""
import contextlib
import logging
import os
from typing import Any, Dict, Optional

from gateway.platforms._shared import get_scoped_secret

from .markdown import sanitize_markdown

logger = logging.getLogger(__name__)

_YAML_KEYS = {
    "access_token": ("MAX_ACCESS_TOKEN", str),
    "allowed_users": ("MAX_ALLOWED_USERS", str),
    "allow_all_users": ("MAX_ALLOW_ALL_USERS", str),
    "home_channel": ("MAX_HOME_CHANNEL", str),
    "updates_mode": ("MAX_UPDATES_MODE", str),
    "webhook_url": ("MAX_WEBHOOK_URL", str),
    "webhook_port": ("MAX_WEBHOOK_PORT", int),
    "webhook_secret": ("MAX_WEBHOOK_SECRET", str),
    "api_base": ("MAX_API_BASE", str),
    "disable_link_preview": ("MAX_DISABLE_LINK_PREVIEW", str),
    "group_isolation": ("MAX_GROUP_ISOLATION", str),
}


def _env_enablement() -> Optional[Dict[str, Any]]:
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "").strip()
    if not token:
        return None
    seed: Dict[str, Any] = {"access_token": token}
    if mode := get_scoped_secret("MAX_UPDATES_MODE", "").strip():
        seed["updates_mode"] = mode
    if base := get_scoped_secret("MAX_API_BASE", "").strip():
        seed["api_base"] = base
    if home := (get_scoped_secret("MAX_HOME_CHANNEL", "").strip()
                or get_scoped_secret("MAX_HOME_CHANNEL")):
        seed["home_channel"] = {"chat_id": home,
                                "name": get_scoped_secret("MAX_HOME_CHANNEL_NAME", home)}
    return seed


def _apply_yaml_config(yaml_cfg: Dict[str, Any], platform_cfg=None) -> Optional[Dict[str, Any]]:
    if not isinstance(yaml_cfg, dict) or not yaml_cfg:
        return None
    extra: Dict[str, Any] = {}
    for key, (env, _conv) in _YAML_KEYS.items():
        value = yaml_cfg.get(key)
        if value in (None, "", [], {}):
            continue
        extra[key] = value
        if not os.getenv(env):  # env > yaml
            with contextlib.suppress(Exception):
                os.environ[env] = str(value)
    return extra or None


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


async def _standalone_send(pconfig, chat_id: str, message: str, *, thread_id=None,
                           media_files=None, force_document=False) -> Dict[str, Any]:
    from .adapter import MaxAdapter
    from .max_api import MaxClient
    from .uploads import Uploader

    extra = getattr(pconfig, "extra", None) or {}
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "") or extra.get("access_token")
    if not token or not chat_id:
        return {"error": "max: нужен MAX_ACCESS_TOKEN и chat_id"}
    base = get_scoped_secret("MAX_API_BASE", "") or extra.get("api_base") or None
    async with MaxClient(token, base_url=base) as client:
        try:
            undelivered, uploaded = [], []
            for path in media_files or []:
                if not path or not os.path.exists(path):
                    continue
                ext = os.path.splitext(str(path))[1].lower()
                kind = "image" if ext in _IMAGE_EXTS else "file"
                try:
                    att_token = await Uploader(client).upload(str(path), kind)
                    uploaded.append((att_token, kind))
                except Exception as exc:
                    logger.warning("max standalone: вложение %s не загружено: %s", path, exc)
                    undelivered.append(os.path.basename(str(path)))
            if undelivered:
                message = f"{message}\n[не доставлено: {', '.join(undelivered)}]"
            last_mid = None
            for chunk in MaxAdapter._segment(sanitize_markdown(message)):
                last_mid = await client.send_message(int(chat_id), chunk)
            for att_token, kind in uploaded:
                last_mid = await client.send_message(
                    int(chat_id), "", attachments=[{"type": kind, "payload": {"token": att_token}}])
            return {"success": True, "message_id": last_mid}
        except Exception as exc:
            return {"error": f"max standalone send: {exc}"}


def interactive_setup() -> None:
    """Визард `hermes gateway setup`. Ленивые импорты CLI."""
    from hermes_cli.setup import (
        get_env_value,
        print_header,
        print_info,
        print_success,
        print_warning,
        prompt,
        prompt_yes_no,
        save_env_value,
    )

    print_header("MAX")
    if get_env_value("MAX_ACCESS_TOKEN"):
        print_info("MAX: уже настроен")
        if not prompt_yes_no("Перенастроить MAX?", False):
            return
    print_info("Подключите Hermes к боту MAX (https://business.max.ru → Чат-боты).")
    token = prompt("Токен доступа бота (MAX_ACCESS_TOKEN)", password=True)
    if not token:
        print_warning("Токен обязателен — отмена")
        return
    save_env_value("MAX_ACCESS_TOKEN", token.strip())
    if prompt_yes_no("Разрешить всем пользователям писать боту? (небезопасно)", False):
        save_env_value("MAX_ALLOW_ALL_USERS", "true")
    else:
        allowed = prompt("Разрешённые user_id через запятую", default="")
        save_env_value("MAX_ALLOWED_USERS", allowed.replace(" ", ""))
    home = prompt("chat_id для cron-доставки (MAX_HOME_CHANNEL, можно пропустить)", default="")
    if home:
        save_env_value("MAX_HOME_CHANNEL", home)
    print_success("MAX настроен. Перезапустите гейтвей: hermes gateway restart")
