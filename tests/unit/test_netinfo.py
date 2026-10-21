"""Descoberta/classificação de IPs de LAN com dados simulados (ADR-0003 §2)."""

from __future__ import annotations

from karaoke.guest import netinfo
from karaoke.guest.netinfo import IFF_LOOPBACK, IFF_POINTOPOINT, IFF_UP, Interface, build_addresses, classify

UP = IFF_UP


def test_classify_kinds():
    assert classify(Interface("lo", "127.0.0.1", UP | IFF_LOOPBACK)) is None
    assert classify(Interface("enp6s0", "169.254.3.4", UP)) is None  # link-local
    assert classify(Interface("enp6s0", "192.168.1.16", 0)) is None  # desligada
    assert classify(Interface("docker0", "172.17.0.1", UP)) is None
    assert classify(Interface("br-1a2b", "172.18.0.1", UP)) is None
    assert classify(Interface("myvirt", "10.0.3.1", UP, virtual=True)) is None
    assert classify(Interface("enp6s0", "192.168.1.16", UP)) == "ethernet"
    assert classify(Interface("wlan0", "192.168.1.4", UP, wireless=True)) == "wifi"
    assert classify(Interface("wg0", "10.8.0.2", UP)) == "vpn"
    assert classify(Interface("ppp0", "10.64.0.2", UP | IFF_POINTOPOINT)) == "vpn"
    assert classify(Interface("eth1", "100.100.1.2", UP)) == "vpn"  # CGNAT (tailscale)
    assert classify(Interface("eth2", "8.8.4.4", UP)) == "other"  # IP público
    assert classify(Interface("eth3", "not-an-ip", UP)) is None


MACHINE = [
    Interface("lo", "127.0.0.1", UP | IFF_LOOPBACK),
    Interface("wlan0", "192.168.1.4", UP, wireless=True),
    Interface("enp6s0", "192.168.1.16", UP),
    Interface("docker0", "172.17.0.1", UP, virtual=True),
    Interface("tailscale0", "100.101.1.1", UP | IFF_POINTOPOINT),
]


def test_build_addresses_recommends_default_route_source():
    out = build_addresses(MACHINE, "192.168.1.4")
    assert [(a.address, a.kind) for a in out] == [
        ("192.168.1.16", "ethernet"),
        ("192.168.1.4", "wifi"),
        ("100.101.1.1", "vpn"),
    ]
    assert [a.address for a in out if a.recommended] == ["192.168.1.4"]
    assert out[0].to_dict() == {
        "address": "192.168.1.16",
        "interface": "enp6s0",
        "kind": "ethernet",
        "recommended": False,
        "bind_address": None,
    }


def test_build_addresses_fallback_order_and_vpn_never_recommended():
    # rota padrão pela VPN (ou offline): vale a ordem ethernet > wifi > other
    assert [a.address for a in build_addresses(MACHINE, "100.101.1.1") if a.recommended] == ["192.168.1.16"]
    assert [a.address for a in build_addresses(MACHINE, None) if a.recommended] == ["192.168.1.16"]
    only_vpn = build_addresses([Interface("wg0", "10.8.0.2", UP)], "10.8.0.2")
    assert [(a.kind, a.recommended) for a in only_vpn] == [("vpn", False)]
    assert build_addresses([MACHINE[0], MACHINE[3]], None) == []


def test_discover_runs_on_this_machine():
    # leitura real (só stdlib, sem rede): nunca devolve loopback/virtual
    for a in netinfo.discover():
        assert a.kind in ("ethernet", "wifi", "vpn", "other")
        assert not a.address.startswith("127.")


def test_firewall_hint():
    assert netinfo.firewall_hint(8765, lambda n: "/usr/sbin/ufw" if n == "ufw" else None) == {
        "tool": "ufw",
        "command": "sudo ufw allow 8765/tcp",
    }
    assert netinfo.firewall_hint(8765, lambda n: "/x" if n == "firewall-cmd" else None) == {
        "tool": "firewall-cmd",
        "command": "sudo firewall-cmd --add-port=8765/tcp",
    }
    assert netinfo.firewall_hint(8765, lambda n: None) is None
