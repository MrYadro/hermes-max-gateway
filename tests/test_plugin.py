"""Проверка манифеста плагина и точки входа (без hermes-agent)."""
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parent.parent / "maxbot"


def test_plugin_yaml_manifest():
    manifest = yaml.safe_load((PKG / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["kind"] == "platform"
    assert manifest["name"] == "max-platform"
    required = {e["name"] for e in manifest["requires_env"]}
    assert "MAX_ACCESS_TOKEN" in required
    optional = {e["name"] for e in manifest["optional_env"]}
    assert {"MAX_ALLOWED_USERS", "MAX_HOME_CHANNEL", "MAX_UPDATES_MODE"} <= optional


def test_package_exposes_register():
    import maxbot

    assert callable(getattr(maxbot, "register", None))
