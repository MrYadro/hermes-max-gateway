"""Паритет типов подписки с официальной OpenAPI-схемой MAX.

Источник: https://github.com/max-messenger/api-schema → schema.yaml
(копия в tests/fixtures/max-api-schema.yaml; обновляй при выходе новой схемы —
эти тесты покажут диф один-в-один).
"""
import pathlib

import yaml

from maxbot.models import POLLING_UPDATE_TYPES, WEBHOOK_ONLY_TYPES, WEBHOOK_UPDATE_TYPES

SCHEMA_PATH = pathlib.Path(__file__).parent / "fixtures" / "max-api-schema.yaml"


def _schema_update_types() -> set:
    spec = yaml.safe_load(SCHEMA_PATH.read_text())
    mapping = spec["components"]["schemas"]["Update"]["discriminator"]["mapping"]
    assert mapping, "в схеме исчез discriminator.mapping объекта Update"
    return set(mapping)


def test_webhook_types_full_parity_with_schema():
    """Webhook подписываемся на ВСЕ типы событий из схемы — без ручного отбора."""
    assert set(WEBHOOK_UPDATE_TYPES) == _schema_update_types()


def test_polling_types_schema_minus_webhook_only():
    """Polling: все типы схемы, кроме webhook-only (по changelog API их в /updates не отдаёт)."""
    schema = _schema_update_types()
    assert WEBHOOK_ONLY_TYPES <= schema
    assert set(POLLING_UPDATE_TYPES) == schema - WEBHOOK_ONLY_TYPES
    assert "bot_admin_permissions_changed" in WEBHOOK_ONLY_TYPES
