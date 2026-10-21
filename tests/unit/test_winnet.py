"""ADR-0006 §5–6: plataforma WSL, sonda do Windows (dados simulados, sem powershell real) e diagnósticos."""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from karaoke.guest import winnet
from karaoke.guest.netinfo import IFF_UP, Interface, LanAddress
from karaoke.guest.winnet import (
    NetEnvironment,
    build_candidates,
    build_diagnostics,
    is_wsl,
    parse_probe,
    resolve_platform,
)

ETH0 = "172.24.5.3"
WIN_WIFI = "192.168.1.16"
PORT = 8765


def probe_json(**over: object) -> str:
    data: dict[str, object] = {
        "ips": [
            {
                "address": WIN_WIFI,
                "alias": "Wi-Fi",
                "kind": "wifi",
                "has_gateway": True,
                "category": "Public",
            },
            {
                "address": "172.24.0.1",
                "alias": "vEthernet (WSL)",
                "kind": "ethernet",
                "has_gateway": False,
                "category": None,
            },
        ],
        "portproxy": [],
        "fw_rule": True,
        "hyperv_rule": None,
    }
    data.update(over)
    return json.dumps(data)


PROBE = parse_probe(probe_json())


def codes(diags: list[dict[str, object]]) -> list[object]:
    return [d["code"] for d in diags]


# ---- parse ------------------------------------------------------------------------------------------------
def test_parse_probe_ok_and_tolerant():
    p = parse_probe("﻿" + probe_json(portproxy={"listen": WIN_WIFI, "port": "8765", "connect": ETH0}))
    assert p is not None
    assert [(i.address, i.kind, i.has_gateway, i.category) for i in p.ips] == [
        (WIN_WIFI, "wifi", True, "Public"),
        ("172.24.0.1", "ethernet", False, None),
    ]
    assert p.portproxy[0].port == 8765 and p.portproxy[0].connect == ETH0  # objeto único (PS 5.1) vira lista
    assert p.fw_rule is True and p.hyperv_rule is None
    bad = parse_probe(
        json.dumps({"ips": [{"address": "x"}, {"address": "169.254.1.1"}, 3], "portproxy": None})
    )
    assert bad is not None and bad.ips == () and bad.portproxy == ()


@pytest.mark.parametrize("text", ["", "não é json", "[]", "null"])
def test_parse_probe_invalid(text):
    assert parse_probe(text) is None


# ---- plataforma ---------------------------------------------------------------------------------------------
def test_is_wsl():
    assert is_wsl("Linux version 5.15.167.4-microsoft-standard-WSL2", {})
    assert is_wsl("Linux version 6.9", {"WSL_DISTRO_NAME": "Ubuntu"})
    assert not is_wsl("Linux version 6.11.0-cachyos", {})


def test_resolve_platform():
    assert resolve_platform(False, "nat", [], ETH0, PROBE) == "linux"
    assert resolve_platform(True, "mirrored\n", [], None, None) == "wsl-mirrored"
    assert resolve_platform(True, "nat", [WIN_WIFI], ETH0, PROBE) == "wsl-nat"
    # heurística (sem wslinfo)
    assert resolve_platform(True, None, [WIN_WIFI], WIN_WIFI, PROBE) == "wsl-mirrored"
    assert resolve_platform(True, None, [ETH0], ETH0, PROBE) == "wsl-nat"
    assert resolve_platform(True, None, ["10.0.0.5"], "10.0.0.5", None) == "wsl-unknown"


# ---- candidatos ---------------------------------------------------------------------------------------------
DISCOVERED_NAT = [LanAddress(ETH0, "eth0", "other", True)]


def test_candidates_nat_use_windows_ips_with_bind():
    out = build_candidates("wsl-nat", DISCOVERED_NAT, PROBE, ETH0)
    assert [a.to_dict() for a in out] == [
        {
            "address": WIN_WIFI,
            "interface": "Wi-Fi (Windows)",
            "kind": "wifi",
            "recommended": True,
            "bind_address": ETH0,
        }
    ]
    assert (
        build_candidates("wsl-nat", DISCOVERED_NAT, None, ETH0) == DISCOVERED_NAT
    )  # sem sonda: volta ao 172.x


def test_candidates_mirrored_fix_kind_and_drop_no_gateway():
    discovered = [
        LanAddress(WIN_WIFI, "eth1", "ethernet", True),
        LanAddress("172.24.0.1", "eth2", "ethernet", False),
        LanAddress("10.8.0.2", "wg0", "vpn", False),
    ]
    out = build_candidates("wsl-mirrored", discovered, PROBE, None)
    assert [(a.address, a.kind, a.recommended, a.bind_address) for a in out] == [
        (WIN_WIFI, "wifi", True, None),
        ("10.8.0.2", "vpn", False, None),
    ]
    assert build_candidates("wsl-mirrored", discovered, None, None) == discovered


def test_candidates_linux_unchanged_and_advertise_override():
    discovered = [LanAddress("192.168.0.9", "enp6s0", "ethernet", True)]
    assert build_candidates("linux", discovered, None, None) == discovered
    adv = build_candidates("wsl-nat", DISCOVERED_NAT, PROBE, ETH0, advertise=WIN_WIFI)
    assert [(a.address, a.interface, a.kind, a.recommended, a.bind_address) for a in adv] == [
        (WIN_WIFI, "Wi-Fi (Windows)", "wifi", True, ETH0)
    ]
    adv_linux = build_candidates("linux", discovered, None, None, advertise="10.1.1.1")
    assert [(a.address, a.recommended, a.bind_address) for a in adv_linux] == [("10.1.1.1", True, None)]


# ---- diagnósticos ---------------------------------------------------------------------------------------------
def nat_candidates(probe):
    return build_candidates("wsl-nat", DISCOVERED_NAT, probe, ETH0)


def test_diagnostics_nat_unconfigured_and_public():
    diags = build_diagnostics("wsl-nat", PROBE, False, nat_candidates(PROBE), None, PORT, ETH0)
    assert codes(diags) == ["wsl_nat_unconfigured", "windows_network_public"]
    assert diags[0]["command"] == "karaoke.bat --configurar-rede" and diags[0]["severity"] == "warning"
    assert diags[1]["message"] == 'A rede "Wi-Fi" está como Pública no Windows; marque-a como Privada.'
    assert set(diags[0]) == {"code", "severity", "message", "command"}


def test_diagnostics_nat_stale_ok_and_firewall():
    stale = parse_probe(
        probe_json(portproxy=[{"listen": WIN_WIFI, "port": 8765, "connect": "172.24.9.9"}], fw_rule=False)
    )
    assert codes(build_diagnostics("wsl-nat", stale, False, [], WIN_WIFI, PORT, ETH0)) == [
        "wsl_portproxy_stale",
        "windows_firewall_rule_missing",
        "windows_network_public",
    ]
    ok = parse_probe(probe_json(portproxy=[{"listen": WIN_WIFI, "port": 8765, "connect": ETH0}]))
    assert codes(build_diagnostics("wsl-nat", ok, False, [], "10.0.0.1", PORT, ETH0)) == []
    other_port = parse_probe(probe_json(portproxy=[{"listen": WIN_WIFI, "port": 8766, "connect": ETH0}]))
    assert codes(build_diagnostics("wsl-nat", other_port, False, [], "10.0.0.1", PORT, ETH0)) == [
        "wsl_nat_unconfigured"
    ]


def test_diagnostics_mirrored_hyperv_unknown_vpn_linux():
    hv = parse_probe(probe_json(hyperv_rule=False))
    assert codes(build_diagnostics("wsl-mirrored", hv, False, [], "10.0.0.1", PORT, None)) == [
        "windows_hyperv_rule_missing"
    ]
    unknown = build_diagnostics("wsl-nat", None, True, DISCOVERED_NAT, None, PORT, ETH0)
    assert codes(unknown) == ["windows_ip_unknown"] and unknown[0]["command"] is None
    assert unknown[0]["severity"] == "info"
    assert build_diagnostics("wsl-nat", None, False, DISCOVERED_NAT, None, PORT, ETH0) == []  # sonda pendente
    vpn = [LanAddress("10.8.0.2", "wg0", "vpn", False)]
    assert codes(build_diagnostics("linux", None, False, vpn, None, PORT, None)) == ["vpn_present"]
    assert build_diagnostics("linux", None, False, [], None, PORT, None) == []


# ---- NetEnvironment (sistema simulado) ------------------------------------------------------------------------
class FakeRun:
    def __init__(self, wslinfo: str | Exception = "nat\n", probe: str | Exception = ""):
        self.wslinfo, self.probe_out, self.calls = wslinfo, probe or probe_json(), []

    def __call__(self, argv: list[str], timeout: float) -> str:
        self.calls.append((argv[0], timeout))
        out = self.wslinfo if argv[0] == "wslinfo" else self.probe_out
        if isinstance(out, Exception):
            raise out
        return out


def make_env(run: FakeRun, *, wsl: bool = True, environ: dict[str, str] | None = None, clock=lambda: 0.0):
    return NetEnvironment(
        environ=environ or {},
        proc_version=lambda: "Linux 5.15-microsoft-standard-WSL2" if wsl else "Linux 6.11-cachyos",
        read_interfaces=lambda: [Interface("lo", "127.0.0.1", IFF_UP), Interface("eth0", ETH0, IFF_UP)],
        run=run,
        powershell_argv=lambda: ["powershell.exe", "-NoProfile"],
        clock=clock,
    )


def test_env_linux_native_never_probes():
    run = FakeRun()
    env = make_env(run, wsl=False)
    env.schedule_refresh()
    asyncio.run(env.refresh())
    discovered = [LanAddress("192.168.0.9", "enp6s0", "ethernet", True)]
    assert env.platform() == "linux" and env.candidates(discovered) == discovered
    assert env.firewall_hint(PORT) in (
        None,
        {"tool": "ufw", "command": "sudo ufw allow 8765/tcp"},
        {"tool": "firewall-cmd", "command": "sudo firewall-cmd --add-port=8765/tcp"},
    )
    assert run.calls == []


def test_env_wsl_nat_probe_cache_and_failure():
    now = [0.0]
    run = FakeRun()
    env = make_env(run, clock=lambda: now[0])
    assert env.platform() == "wsl-nat" and env.firewall_hint(PORT) == {
        "tool": "windows",
        "command": "karaoke.bat --configurar-rede",
    }
    asyncio.run(env.refresh())
    assert [(c, t) for c, t in run.calls] == [("wslinfo", 2.0), ("powershell.exe", 12.0)]
    cands = env.candidates(DISCOVERED_NAT)
    assert [(a.address, a.bind_address) for a in cands] == [(WIN_WIFI, ETH0)]
    assert not env.stale()
    now[0] = 61.0
    assert env.stale()
    run.probe_out = subprocess.TimeoutExpired("powershell.exe", 6)
    env.probe_now()
    assert env.probe is None and env.probe_failed
    assert codes(env.diagnostics(env.candidates(DISCOVERED_NAT), None, PORT)) == ["windows_ip_unknown"]


def test_env_wslinfo_missing_uses_heuristic_and_advertise_validation():
    env = make_env(FakeRun(wslinfo=FileNotFoundError("wslinfo")), environ={winnet.ADVERTISE_ENV: "nada"})
    assert env.platform() == "wsl-nat"  # eth0 em 172.16/12
    assert env.advertise() is None
    env2 = make_env(FakeRun(), environ={winnet.ADVERTISE_ENV: " 192.168.1.50 "})
    assert env2.advertise() == "192.168.1.50"


def test_schedule_refresh_runs_in_background():
    run = FakeRun()
    env = make_env(run)

    async def go():
        env.schedule_refresh()
        env.schedule_refresh()  # já em andamento: não duplica
        await env._task
        await env.stop()

    asyncio.run(go())
    assert [c for c, _ in run.calls].count("powershell.exe") == 1 and env.probe is not None


def test_unknown_rule_state_does_not_flag_missing():
    """review-1 (medium): erro ao ler a regra → null → sem `*_rule_missing`."""
    p = parse_probe(probe_json(fw_rule=None, hyperv_rule=None))
    assert p is not None and p.fw_rule is None and p.hyperv_rule is None
    assert codes(build_diagnostics("wsl-mirrored", p, False, [], "10.0.0.1", PORT, None)) == []
    p2 = parse_probe(probe_json(fw_rule=False, hyperv_rule=False))
    assert codes(build_diagnostics("wsl-mirrored", p2, False, [], "10.0.0.1", PORT, None)) == [
        "windows_firewall_rule_missing",
        "windows_hyperv_rule_missing",
    ]
    assert "RuleState" in winnet.PROBE_SCRIPT and "-ErrorAction Stop" in winnet.PROBE_SCRIPT
