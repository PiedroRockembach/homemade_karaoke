"""Fixtures comuns: app de teste com diretório de dados temporário, cliente HTTP vindo de loopback.

Sem rede e sem GPU. Use `client` para requisições "do host" (Host/Origin permitidos).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from karaoke.app import create_app
from karaoke.config import Config

TEST_PORT = 8765
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(port=TEST_PORT, data_dir=tmp_path / "data", device="cpu", open_browser=False)


@pytest.fixture
def app(config: Config) -> FastAPI:
    return create_app(config)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 50000)) as c:
        yield c
