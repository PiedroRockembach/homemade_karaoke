"""ADR-0006 §5.4: no `wsl-nat` o QR/URL/Host usam o IP do Windows e o listener abre no `eth0` do WSL."""

from __future__ import annotations

from types import SimpleNamespace

from karaoke.guest.netinfo import LanAddress
from tests.api.guest_helpers import LAN_IP
from tests.unit.test_winnet import ETH0, FakeRun, make_env


def test_wsl_nat_announces_windows_ip_and_binds_eth0(guest_env):
    svc = guest_env.service
    svc.net = make_env(FakeRun())
    svc.net.probe_now()
    guest_env.service.discover = lambda: [LanAddress(ETH0, "eth0", "other", True)]
    st = guest_env.enable()
    assert ("start", ETH0, svc.port) in guest_env.listener.calls
    assert st["platform"] == "wsl-nat" and st["address"] == LAN_IP
    assert st["url"].startswith(f"http://{LAN_IP}:{svc.port}/guest#t=")
    assert st["addresses"][0]["bind_address"] == ETH0
    assert st["firewall_hint"] == {"tool": "windows", "command": "karaoke.bat --configurar-rede"}
    assert [d["code"] for d in st["diagnostics"]] == ["wsl_nat_unconfigured", "windows_network_public"]
    assert svc.allowed_hosts() == frozenset({f"{LAN_IP}:{svc.port}"})
    assert svc.accepts_connection(SimpleNamespace(scope={"server": (ETH0, svc.port)}))
    assert not svc.accepts_connection(SimpleNamespace(scope={"server": (LAN_IP, svc.port)}))


def test_linux_state_has_platform_and_no_bind(guest_env):
    st = guest_env.enable()
    assert st["platform"] == "linux" and st["diagnostics"] == []
    assert all(a["bind_address"] is None for a in st["addresses"])
