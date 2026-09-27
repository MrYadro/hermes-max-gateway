"""HTTP-API мини-аппа «Assistants»: initData-авторизация, state/set, статика."""
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import unquote

from gateway.platforms._shared import get_scoped_secret

from . import profile_switch

logger = logging.getLogger(__name__)

_INIT_HEADER = "X-WebApp-InitData"


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 3600,
                       now: Optional[float] = None) -> Optional[int]:
    """Официальная схема dev.max.ru/docs/webapps/validation.
    Возвращает user_id при валидной подписи и свежем auth_date, иначе None."""
    try:
        pairs: List[Tuple[str, str]] = []
        for chunk in (init_data or "").split("&"):
            if "=" not in chunk:
                continue
            k, v = chunk.split("=", 1)
            pairs.append((k, unquote(v)))
        hashes = [v for k, v in pairs if k == "hash"]
        if len(hashes) != 1:
            return None
        original = hashes[0]
        params = dict(pairs)
        launch = "\n".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hash")
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, launch.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, original):
            return None
        auth_date = int(params.get("auth_date") or 0)
        ts = now if now is not None else time.time()
        if auth_date <= 0 or not 0 < ts - auth_date <= max_age:
            return None
        user = json.loads(params.get("user") or "{}")
        return int(user.get("id") or 0) or None
    except Exception:
        return None


def _plugin_home():
    from .adapter import _plugin_home as _home
    return _home()


def _admin_ids_from_env() -> set:
    raw = get_scoped_secret("MAX_ASSISTANT_ADMINS", "")
    if not str(raw).strip():
        raw = get_scoped_secret("MAX_ALLOWED_USERS", "")
    ids = set()
    for part in str(raw or "").replace(" ", "").split(","):
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def admin_ids() -> set:
    return _admin_ids_from_env()


def _header(headers: Optional[Mapping[str, str]], name: str) -> str:
    """Case-insensitive lookup: CIMultiDict даёт его через .get, обычный dict — нет."""
    h = headers or {}
    val = h.get(name)
    if val:
        return str(val)
    lowered = name.lower()
    for k, v in h.items():
        if str(k).lower() == lowered and v:
            return str(v)
    return ""


def authorized_user_id(headers: Mapping[str, str], remote: str,
                       query: Dict[str, str]) -> Optional[int]:
    dev = str(get_scoped_secret("MAX_ASSISTANT_DEV", "") or "").strip().lower() in {"1", "true", "yes"}
    proxied = bool(_header(headers, "X-Forwarded-For") or _header(headers, "X-Real-IP"))
    if dev and remote in {"127.0.0.1", "::1"} and not proxied:
        raw = str(query.get("dev_user_id") or "").strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
    data = _header(headers, _INIT_HEADER)
    if not data:
        return None
    token = get_scoped_secret("MAX_ACCESS_TOKEN", "")
    return validate_init_data(data, token)


def _state_payload(user_id: int) -> Dict[str, Any]:
    home = _plugin_home()
    chats_map = profile_switch.load_map(home)
    known = profile_switch.known_chats(home)
    merged = dict(known)
    for key in chats_map:
        merged.setdefault(key, {"type": "unknown"})
    chats = [{"chat_id": k, "chat_type": v.get("type", "unknown"),
              "title": v.get("title", ""), "profile": chats_map.get(k, "default")}
             for k, v in sorted(merged.items())]
    profiles = [{"name": n, "description": profile_switch.profile_description(n)}
                for n in profile_switch.available_profiles()]
    return {"chats": chats, "profiles": profiles,
            "me": {"user_id": user_id, "is_admin": user_id in _admin_ids_from_env()}}


async def _handle_state(request):
    from aiohttp import web

    uid = authorized_user_id(request.headers, request.remote, request.query)
    if uid is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    if uid not in _admin_ids_from_env():
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response(_state_payload(uid))


async def _handle_set(request):
    from aiohttp import web

    uid = authorized_user_id(request.headers, request.remote, request.query)
    if uid is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    if uid not in _admin_ids_from_env():
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad json"}, status=400)
    chat_id = str((body or {}).get("chat_id") or "").strip()
    profile = str((body or {}).get("profile") or "").strip()
    if not chat_id or not profile_switch.profile_exists(profile):
        return web.json_response({"error": "unknown profile"}, status=400)
    profile_switch.set_profile(_plugin_home(), chat_id, profile)
    return web.json_response({"ok": True, "state": _state_payload(uid)})


def _dist_dir() -> Path:
    return Path(__file__).parent / "webapp" / "dist"


async def _handle_static(request):
    from aiohttp import web

    dist = _dist_dir()
    tail = request.match_info.get("tail", "")
    if not tail:
        f = dist / "index.html"
    else:
        f = (dist / tail).resolve()
        if not f.is_relative_to(dist.resolve()):
            return web.json_response({"error": "not found"}, status=404)
    if not f.is_file():
        return web.json_response({"error": "not found"}, status=404)
    if f.name == "index.html":
        return web.FileResponse(f, headers={"Cache-Control": "no-cache"})
    return web.FileResponse(f)


def build_routes(adapter) -> List[Tuple[str, Any, Any]]:
    return [
        ("GET", "/max/app/state", _handle_state),
        ("POST", "/max/app/set", _handle_set),
        ("GET", "/max/app/{tail:.*}", _handle_static),
    ]
