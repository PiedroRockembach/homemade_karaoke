"""Descoberta dos IPv4 de LAN (ADR-0003 §2). Só biblioteca padrão; Linux (SO-alvo). No WSL, o `winnet` ajusta.

`discover()` devolve os candidatos para o QR (`GuestState.addresses`). A classificação (`classify`) é pura e testada
com dados simulados; a leitura do sistema (`read_interfaces`) usa `ioctl` e `/sys`.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import shutil
import socket
import struct
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

SIOCGIFADDR = 0x8915
SIOCGIFFLAGS = 0x8913
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
IFF_POINTOPOINT = 0x10

VIRTUAL_PREFIXES = ("docker", "br-", "veth", "virbr", "lxc", "podman", "cni", "flannel", "vmnet", "vboxnet")
VPN_PREFIXES = ("tun", "tap", "wg", "tailscale", "zt", "ppp", "nordlynx")
CGNAT = ipaddress.ip_network("100.64.0.0/10")
LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
KIND_ORDER = {"ethernet": 0, "wifi": 1, "other": 2, "vpn": 3}
PROBE_TARGET = ("192.0.2.1", 9)  # TEST-NET-1: `connect` de UDP não envia pacote nenhum


@dataclass(frozen=True)
class Interface:
    """Dados crus de uma interface (o que `read_interfaces` lê do sistema)."""

    name: str
    address: str
    flags: int
    virtual: bool = False  # existe /sys/devices/virtual/net/<if>
    wireless: bool = False  # existe /sys/class/net/<if>/wireless


@dataclass(frozen=True)
class LanAddress:
    address: str
    interface: str
    kind: str  # ethernet | wifi | vpn | other
    recommended: bool = False
    bind_address: str | None = None  # ADR-0006: IP local do listener quando difere do anunciado (só wsl-nat)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def classify(iface: Interface) -> str | None:
    """`ethernet` | `wifi` | `vpn` | `other`, ou `None` (descartada: loopback, link-local, virtual, desligada)."""
    try:
        ip = ipaddress.IPv4Address(iface.address)
    except ValueError:
        return None
    if not iface.flags & IFF_UP or iface.flags & IFF_LOOPBACK or ip.is_loopback or ip in LINK_LOCAL:
        return None
    name = iface.name.lower()
    if iface.virtual or name.startswith(VIRTUAL_PREFIXES):
        return None
    if iface.flags & IFF_POINTOPOINT or name.startswith(VPN_PREFIXES) or ip in CGNAT:
        return "vpn"
    if iface.wireless:
        return "wifi"
    if ip.is_private:
        return "ethernet"
    return "other"


def build_addresses(ifaces: Iterable[Interface], route_source: str | None) -> list[LanAddress]:
    """Classifica, ordena (ethernet > wifi > other > vpn) e marca o recomendado (ADR-0003 §2.3)."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for iface in ifaces:
        kind = classify(iface)
        if kind is None or iface.address in seen:
            continue
        seen.add(iface.address)
        found.append((iface.address, iface.name, kind))
    found.sort(key=lambda t: (KIND_ORDER[t[2]], t[1], t[0]))
    recommended = None
    for address, _, kind in found:
        if address == route_source and kind in ("ethernet", "wifi"):
            recommended = address
            break
    if recommended is None:
        recommended = next((a for a, _, k in found if k != "vpn"), None)
    return [LanAddress(a, n, k, a == recommended) for a, n, k in found]


# ---- leitura do sistema (Linux) -------------------------------------------------------------------------
def _ioctl_ifreq(sock: socket.socket, request: int, name: str) -> bytes:
    import fcntl

    return fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name.encode()[:15]))


def read_interfaces() -> list[Interface]:
    out: list[Interface] = []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return out
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in names:
            try:
                addr = socket.inet_ntoa(_ioctl_ifreq(sock, SIOCGIFADDR, name)[20:24])
                flags = struct.unpack("H", _ioctl_ifreq(sock, SIOCGIFFLAGS, name)[16:18])[0]
            except OSError:  # interface sem IPv4
                continue
            out.append(
                Interface(
                    name=name,
                    address=addr,
                    flags=flags,
                    virtual=os.path.exists(f"/sys/devices/virtual/net/{name}"),
                    wireless=os.path.exists(f"/sys/class/net/{name}/wireless"),
                )
            )
    return out


def default_route_source() -> str | None:
    """Endereço de origem da rota padrão (sem enviar pacotes). `None` se offline/sem rota."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(PROBE_TARGET)
        return s.getsockname()[0]
    return None


def discover() -> list[LanAddress]:
    return build_addresses(read_interfaces(), default_route_source())


def firewall_hint(port: int, which: Callable[[str], str | None] = shutil.which) -> dict[str, str] | None:
    """Comando pronto para liberar a porta (não detecta se o firewall está ativo: exigiria root)."""
    if which("ufw"):
        return {"tool": "ufw", "command": f"sudo ufw allow {port}/tcp"}
    if which("firewall-cmd"):
        return {"tool": "firewall-cmd", "command": f"sudo firewall-cmd --add-port={port}/tcp"}
    return None
