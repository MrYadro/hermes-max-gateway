#!/usr/bin/env python3
"""Обновить vendored-схему MAX Bot API из официального репозитория.

Скачивает schema.yaml (github.com/max-messenger/api-schema), сравнивает с
tests/fixtures/max-api-schema.yaml и перезаписывает при отличии.

Коды выхода: 0 — схема не изменилась; 3 — обновлена (прогони
`pytest -q tests/test_parity.py tests/test_schema_conformance.py` — они
покажут, какие константы подогнать); 1 — ошибка сети.

Автоматизация: .github/workflows/parity.yml (плановый прогон).
"""
import pathlib
import sys
import urllib.request

URL = "https://raw.githubusercontent.com/max-messenger/api-schema/main/schema.yaml"
FIXTURE = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "max-api-schema.yaml"


def main() -> int:
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "hermes-max-gateway-parity"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as exc:  # сеть/DNS/TLS
        print(f"parity: не удалось скачать схему: {exc}", file=sys.stderr)
        return 1
    old = FIXTURE.read_bytes() if FIXTURE.exists() else b""
    if data == old:
        print("parity: схема не изменилась")
        return 0
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_bytes(data)
    print(f"parity: схема обновлена ({len(old)} → {len(data)} байт); "
          "прогоните тесты паритета", )
    return 3


if __name__ == "__main__":
    sys.exit(main())
