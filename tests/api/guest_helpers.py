"""Fixtures do modo convidado: app com GuestService usando listener falso e interfaces simuladas.

`host` = TestClient do loopback (com lifespan); `lan()` = TestClient que chega "pelo listener da LAN"
(`scope.server` = 192.168.1.16:8765, cliente 192.168.1.x), compartilhando o mesmo loop/portal do host.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from karaoke.guest.netinfo import LanAddress
from tests.conftest import TEST_PORT

LAN_IP = "192.168.1.16"
WIFI_IP = "192.168.1.4"
LAN_BASE = f"http://{LAN_IP}:{TEST_PORT}"
LAN_ORIGIN = LAN_BASE
ADDRESSES = [
    LanAddress(LAN_IP, "enp6s0", "ethernet", True),
    LanAddress(WIFI_IP, "wlan0", "wifi", False),
]


class FakeListener:
    """Sem sockets: registra start/stop (o listener real é testado em tests/unit/test_guest_listener.py)."""

    def __init__(self) -> None:
        self.running = False
        self.calls: list[tuple[Any, ...]] = []
        self.fail: Exception | None = None

    async def start(self, address: str, port: int) -> None:
        self.calls.append(("start", address, port))
        if self.fail is not None:
            raise self.fail
        self.running = True

    async def stop(self) -> None:
        self.calls.append(("stop",))
        self.running = False

    def request_stop(self) -> None:
        self.calls.append(("request_stop",))


class GuestEnv:
    def __init__(self, app: Any, host: TestClient):
        self.app = app
        self.host = host
        self.listener = FakeListener()
        self.service = app.state.guest
        self.service.listener = self.listener
        self.service.discover = lambda: list(ADDRESSES)
        self._clients: list[TestClient] = []

    def lan(self, ip: str = "192.168.1.50", port: int = 40000) -> TestClient:
        c = TestClient(self.app, base_url=LAN_BASE, client=(ip, port), headers={"Origin": LAN_ORIGIN})
        c.portal = self.host.portal  # mesmo loop do app (sem rodar o lifespan de novo)
        self._clients.append(c)
        return c

    def enable(self, address: str | None = None) -> dict[str, Any]:
        r = self.host.post("/api/guest/enable", json={"address": address} if address else {})
        assert r.status_code == 200, r.text
        return r.json()

    def token(self) -> str:
        return self.service.state()["url"].split("#t=")[1]

    def join(self, name: str = "Bia", ip: str = "192.168.1.50") -> TestClient:
        c = self.lan(ip)
        r = c.post("/api/guest/session", json={"token": self.token(), "name": name})
        assert r.status_code == 201, r.text
        return c
