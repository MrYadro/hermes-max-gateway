"""Подключаем исходники hermes-agent к sys.path (адаптер импортирует gateway.*)."""
import os
import sys
from pathlib import Path

import pytest


def _add_hermes_to_path() -> None:
    candidates = [os.environ.get("HERMES_AGENT_SRC"), "../hermes-agent", "hermes-agent"]
    for cand in candidates:
        if not cand:
            continue
        root = Path(cand).resolve()
        if (root / "gateway" / "platforms" / "base.py").exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return


_add_hermes_to_path()


@pytest.fixture(scope="session", autouse=True)
def _max_platform_member():
    """Platform("max") разрешается только для зарегистрированных платформ.
    В юнит-тестах адаптер конструируется напрямую — создаём псевдо-член enum."""
    try:
        from gateway.config import Platform
        Platform._add_pseudo_member("max")
    except Exception:
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def _max_test_token():
    """connect()-тест идёт с FakeCfg(extra={}) и без env: даём детерминированный
    тестовый токен, чтобы scoped-чтение MAX_ACCESS_TOKEN его видело. Реальный токен
    разработчика не используем — иначе lock-identity совпадёт с прод-гейтвеем."""
    import os
    os.environ["MAX_ACCESS_TOKEN"] = "unit-test-max-token"
    yield
