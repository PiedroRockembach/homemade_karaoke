"""Fixtures dos testes de API do modo convidado (ver tests/api/guest_helpers.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.api.guest_helpers import GuestEnv
from tests.conftest import BASE_URL


@pytest.fixture
def guest_env(app):
    with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 50000)) as host:
        yield GuestEnv(app, host)
