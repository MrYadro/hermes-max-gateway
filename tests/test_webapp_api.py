"""Мини-апп API: валидация initData, авторизация, state/set."""
import hashlib
import hmac
import json
import urllib.parse

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot.webapp_api import validate_init_data

TOKEN = "synthetic-bot-token"


def _make_init_data(pairs: dict, token: str, *, hours_ago: float = 0.0, hash_override=None,
                    dup_hash=False):
    """initData по официальной схеме: values URL-encoded, подпись HMAC."""
    params = {k: urllib.parse.quote(str(v), safe="") for k, v in pairs.items()}
    sorted_pairs = sorted(pairs.items())
    launch = "\n".join(f"{k}={v}" for k, v in sorted_pairs)
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    good_hash = hmac.new(secret, launch.encode(), hashlib.sha256).hexdigest()
    h = hash_override if hash_override is not None else good_hash
    parts = [f"{k}={params[k]}" for k, _ in sorted_pairs]
    parts.append(f"hash={h}")
    if dup_hash:
        parts.append(f"hash={h}")
    return "&".join(parts)


USER = {"id": 13, "first_name": "Петя", "username": None, "language_code": "ru"}


def _valid(now=1_700_000_000.0):
    return _make_init_data(
        {"auth_date": int(now - 60), "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, hours_ago=0.0)


def test_valid_signature_returns_user_id():
    assert validate_init_data(_valid(), TOKEN, now=1_700_000_000.0) == 13


def test_bad_signature_rejected():
    data = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        "wrong-token")
    assert validate_init_data(data, TOKEN, now=1_700_000_000.0) is None


def test_tampered_hash_rejected():
    data = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, hash_override="deadbeef" * 8)
    assert validate_init_data(data, TOKEN, now=1_700_000_000.0) is None


def test_stale_auth_date_rejected():
    data = _make_init_data(
        {"auth_date": 1_700_000_000 - 7200, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN)
    assert validate_init_data(data, TOKEN, now=1_700_000_000.0) is None


def test_duplicated_or_missing_hash_rejected():
    dup = _make_init_data(
        {"auth_date": 1699999940, "query_id": "q-1", "user": json.dumps(USER)},
        TOKEN, dup_hash=True)
    assert validate_init_data(dup, TOKEN, now=1_700_000_000.0) is None
    assert validate_init_data("query_id=q-1", TOKEN, now=1_700_000_000.0) is None
