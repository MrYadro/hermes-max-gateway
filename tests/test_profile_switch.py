"""Переключалка профилей: карта чат→профиль, список профилей."""
import json

import pytest

pytest.importorskip("gateway.platforms.base")

from maxbot import profile_switch


def test_map_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "work")
    assert profile_switch.load_map(tmp_path) == {"500": "work"}
    assert json.loads(
        (tmp_path / "maxbot-chat-memory" / "_chat_profiles.json").read_text())["500"] == "work"


def test_map_unknown_profile_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work"})
    profile_switch.set_profile(tmp_path, "500", "ghost")
    assert profile_switch.load_map(tmp_path) == {}


def test_available_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_switch, "_existing_profiles", lambda: {"default", "work", "family"})
    assert profile_switch.available_profiles() == ["default", "family", "work"]
