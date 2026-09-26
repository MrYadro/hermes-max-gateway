"""Переключалка профилей: per-chat карта chat_id → профиль (JSON в HERMES_HOME).

Штамп ``source.profile`` — официальный приоритет №1 маршрутизации ядра
(base.py `_session_key_profile`); требуется ``gateway.multiplex_profiles: true``.
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_cache: Dict[Path, tuple] = {}  # home -> (mtime_ns, map)


def _store(home: Path) -> Path:
    return home / "maxbot-chat-memory" / "_chat_profiles.json"


def _existing_profiles() -> Set[str]:
    """{'default', ...именованные}; каталоги профилей сканируем напрямую
    (SOUL.md — маркер живого профиля, как в hermes_cli.container_boot)."""
    names: Set[str] = {"default"}
    try:
        from hermes_constants import get_default_hermes_root
        root = Path(get_default_hermes_root()) / "profiles"
        if root.is_dir():
            names |= {p.name for p in root.iterdir() if (p / "SOUL.md").exists()}
    except Exception:
        logger.debug("profile_switch: скан профилей не удался", exc_info=True)
    return names


def profile_exists(name: str) -> bool:
    return bool(name) and name in _existing_profiles()


def available_profiles() -> List[str]:
    return sorted(_existing_profiles())


def load_map(home: Path) -> Dict[str, str]:
    f = _store(home)
    try:
        mtime = f.stat().st_mtime_ns
    except OSError:
        _cache.pop(home, None)
        return {}
    cached = _cache.get(home)
    if cached and cached[0] == mtime:
        return dict(cached[1])
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data = {str(k): str(v) for k, v in data.items() if _NAME_RE.match(str(v))}
    except Exception:
        data = {}
    _cache[home] = (mtime, data)
    return dict(data)


def set_profile(home: Path, chat_key: str, profile: str) -> None:
    """Записать профиль чата; несуществующий профиль молча игнорируется."""
    if not _NAME_RE.match(profile or "") or not profile_exists(profile):
        return
    f = _store(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    data = load_map(home)
    data[str(chat_key)] = profile
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    _cache.pop(home, None)
