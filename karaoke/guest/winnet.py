"""Modo convidado dentro do WSL: plataforma, sonda do Windows e diagnósticos (ADR-0006 §5–6).

Só biblioteca padrão. A parte pura (`resolve_platform`, `parse_probe`, `build_candidates`, `build_diagnostics`) é
testada com dados simulados; a leitura do sistema (`/proc/version`, `wslinfo`, `powershell.exe`) fica isolada em
`NetEnvironment`, com dependências injetáveis.

Fora do WSL (`platform == "linux"`) nada muda em relação à v0.3: os candidatos são os do `netinfo.discover()` e a
dica de firewall é a do ufw/firewall-cmd.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from karaoke.guest import netinfo
from karaoke.guest.netinfo import KIND_ORDER, LanAddress

log = logging.getLogger("karaoke")

PROBE_TIMEOUT_S = 12.0  # review-1: partida a frio do PS 5.1 + módulos CIM
PROBE_TTL_S = 60.0
WSLINFO_TIMEOUT_S = 2.0
WSL_NAT_NET = ipaddress.ip_network("172.16.0.0/12")
ADVERTISE_ENV = "KARAOKE_GUEST_ADVERTISE"
CONFIGURE_CMD = "karaoke.bat --configurar-rede"
POWERSHELL_FALLBACK = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
PLATFORMS = ("linux", "wsl-mirrored", "wsl-nat", "wsl-unknown")

# Uma chamada só; nada exige admin. Saída: JSON compacto (ADR-0006 §5.2). Enums viram texto por interpolação.
PROBE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ips = @()
foreach ($c in Get-NetIPConfiguration) {
  if (-not $c.IPv4Address) { continue }
  $a = Get-NetAdapter -InterfaceIndex $c.InterfaceIndex
  $kind = if ("$($a.PhysicalMediaType)" -like '*802.11*') { 'wifi' } else { 'ethernet' }
  $p = Get-NetConnectionProfile -InterfaceIndex $c.InterfaceIndex
  $cat = if ($p) { "$($p.NetworkCategory)" } else { $null }
  foreach ($ip in @($c.IPv4Address)) {
    $ips += [pscustomobject]@{ address = "$($ip.IPAddress)"; alias = "$($c.InterfaceAlias)"; kind = $kind;
      has_gateway = [bool]$c.IPv4DefaultGateway; category = $cat }
  }
}
$pp = @()
foreach ($l in @(netsh interface portproxy show v4tov4)) {
  if ($l -match '^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
    $pp += [pscustomobject]@{ listen = $matches[1]; port = [int]$matches[2]; connect = $matches[3] }
  }
}
function RuleState($cmd, $name) {
  # $true existe · $false "não encontrado" confirmado · $null erro de leitura (desconhecido)
  try { return [bool](& $cmd -Name $name -ErrorAction Stop) }
  catch {
    if ($_.CategoryInfo.Category -eq 'ObjectNotFound' -or "$($_.FullyQualifiedErrorId)" -like '*NotFound*') {
      return $false
    }
    return $null
  }
}
$fw = RuleState 'Get-NetFirewallRule' 'Karaoke-TCP-In'
$hv = $null
if (Get-Command Get-NetFirewallHyperVRule) { $hv = RuleState 'Get-NetFirewallHyperVRule' 'Karaoke-WSL-TCP-In' }
[pscustomobject]@{ ips = @($ips); portproxy = @($pp); fw_rule = $fw; hyperv_rule = $hv } |
  ConvertTo-Json -Depth 4 -Compress
"""


@dataclass(frozen=True)
class WinIP:
    address: str
    alias: str
    kind: str  # wifi | ethernet
    has_gateway: bool
    category: str | None  # Public | Private | DomainAuthenticated


@dataclass(frozen=True)
class PortProxy:
    listen: str
    port: int
    connect: str


@dataclass(frozen=True)
class WinProbe:
    ips: tuple[WinIP, ...]
    portproxy: tuple[PortProxy, ...]
    fw_rule: bool | None  # None: erro ao ler a regra (desconhecido)
    hyperv_rule: bool | None  # None: o cmdlet do Hyper-V não existe nesta build

    def ip(self, address: str | None) -> WinIP | None:
        return next((i for i in self.ips if i.address == address), None)


# ---- parte pura -----------------------------------------------------------------------------------------
def _ipv4(value: object) -> str | None:
    try:
        return str(ipaddress.IPv4Address(str(value).strip()))
    except ValueError:
        return None


def _as_list(value: object) -> list[Any]:
    """O `ConvertTo-Json` do PowerShell 5.1 às vezes desembrulha listas de 1 item (ou `null` se vazia)."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_probe(text: str) -> WinProbe | None:
    """JSON da sonda → `WinProbe`. `None` se inválido. Itens malformados são ignorados."""
    try:
        data = json.loads(text.strip().lstrip("﻿"))
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    ips: list[WinIP] = []
    for item in _as_list(data.get("ips")):
        if not isinstance(item, dict) or not (addr := _ipv4(item.get("address"))):
            continue
        ip = ipaddress.IPv4Address(addr)
        if ip.is_loopback or ip.is_link_local:
            continue
        category = item.get("category")
        ips.append(
            WinIP(
                address=addr,
                alias=str(item.get("alias") or "?")[:64],
                kind="wifi" if item.get("kind") == "wifi" else "ethernet",
                has_gateway=bool(item.get("has_gateway")),
                category=category if category in ("Public", "Private", "DomainAuthenticated") else None,
            )
        )
    proxies: list[PortProxy] = []
    for item in _as_list(data.get("portproxy")):
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        proxies.append(PortProxy(str(item.get("listen") or ""), port, str(item.get("connect") or "")))
    fw, hv = data.get("fw_rule"), data.get("hyperv_rule")
    return WinProbe(
        ips=tuple(ips),
        portproxy=tuple(proxies),
        fw_rule=None if fw is None else bool(fw),
        hyperv_rule=None if hv is None else bool(hv),
    )


def is_wsl(proc_version: str, environ: Mapping[str, str]) -> bool:
    return "microsoft" in proc_version.lower() or "WSL_INTEROP" in environ or "WSL_DISTRO_NAME" in environ


def resolve_platform(
    wsl: bool,
    wslinfo_mode: str | None,
    local_ips: Iterable[str],
    eth0: str | None,
    probe: WinProbe | None,
) -> str:
    """ADR-0006 §5.1: `wslinfo` manda; senão heurística (IP do Windows numa interface local → mirrored)."""
    if not wsl:
        return "linux"
    mode = (wslinfo_mode or "").strip().lower()
    if mode == "mirrored":
        return "wsl-mirrored"
    if mode == "nat":
        return "wsl-nat"
    local = set(local_ips)
    if probe is not None and any(i.address in local for i in probe.ips):
        return "wsl-mirrored"
    with contextlib.suppress(ValueError, TypeError):
        if eth0 and ipaddress.IPv4Address(eth0) in WSL_NAT_NET:
            return "wsl-nat"
    return "wsl-unknown"


def _remark(addrs: list[LanAddress], previous: str | None) -> list[LanAddress]:
    """Reordena (ethernet > wifi > other > vpn) e garante um recomendado (o anterior, se sobrou)."""
    addrs = sorted(addrs, key=lambda a: (KIND_ORDER.get(a.kind, 9), a.interface, a.address))
    keep = {a.address for a in addrs}
    rec = previous if previous in keep else next((a.address for a in addrs if a.kind != "vpn"), None)
    return [replace(a, recommended=a.address == rec) for a in addrs]


def build_candidates(
    platform: str,
    discovered: list[LanAddress],
    probe: WinProbe | None,
    eth0: str | None,
    advertise: str | None = None,
) -> list[LanAddress]:
    """Candidatos do `GuestState.addresses` (ADR-0006 §5.3)."""
    nat_bind = eth0 if platform == "wsl-nat" else None
    if advertise:
        win = probe.ip(advertise) if probe else None
        local = next((a for a in discovered if a.address == advertise), None)
        kind = win.kind if win else (local.kind if local else "other")
        name = f"{win.alias} (Windows)" if win else (local.interface if local else ADVERTISE_ENV)
        bind = nat_bind if nat_bind != advertise else None
        return [LanAddress(advertise, name, kind, True, bind)]
    if platform == "wsl-nat":
        if probe is None or not eth0:
            return discovered
        wins = [i for i in probe.ips if i.has_gateway]
        return _remark(
            [LanAddress(i.address, f"{i.alias} (Windows)", i.kind, False, eth0) for i in wins], None
        )
    if platform == "wsl-mirrored" and probe is not None:
        previous = next((a.address for a in discovered if a.recommended), None)
        out: list[LanAddress] = []
        for a in discovered:
            win = probe.ip(a.address)
            if win is None:  # não espelhada (ex.: VPN do próprio WSL): fica como está
                out.append(a)
                continue
            if not win.has_gateway:  # vEthernet de Hyper-V/Docker
                continue
            out.append(replace(a, kind=win.kind) if a.kind in ("ethernet", "wifi", "other") else a)
        return _remark(out, previous)
    return discovered


def _diag(code: str, message: str, command: str | None, severity: str = "warning") -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "command": command}


def build_diagnostics(
    platform: str,
    probe: WinProbe | None,
    probe_failed: bool,
    candidates: list[LanAddress],
    address: str | None,
    port: int,
    eth0: str | None,
) -> list[dict[str, Any]]:
    """`GuestState.diagnostics[]` (ADR-0006 §6). `address`: IP em uso; `None` → usa o recomendado."""
    out: list[dict[str, Any]] = []
    wsl = platform != "linux"
    if wsl and probe is not None:
        if platform == "wsl-nat":
            on_port = [p for p in probe.portproxy if p.port == port]
            if not on_port:
                out.append(
                    _diag(
                        "wsl_nat_unconfigured",
                        "O Windows ainda não encaminha a porta para o WSL.",
                        CONFIGURE_CMD,
                    )
                )
            elif eth0 and not any(p.connect == eth0 for p in on_port):
                out.append(
                    _diag(
                        "wsl_portproxy_stale",
                        "O encaminhamento aponta para um IP antigo do WSL. Feche e abra pelo karaoke.bat.",
                        "karaoke.bat",
                    )
                )
        if probe.fw_rule is False:  # só com ausência confirmada (None = desconhecido)
            out.append(
                _diag(
                    "windows_firewall_rule_missing",
                    "A porta não está liberada no Firewall do Windows.",
                    CONFIGURE_CMD,
                )
            )
        if platform == "wsl-mirrored" and probe.hyperv_rule is False:
            out.append(
                _diag(
                    "windows_hyperv_rule_missing",
                    "Falta liberar a porta no firewall do Hyper-V (WSL).",
                    CONFIGURE_CMD,
                )
            )
        target = address or next((a.address for a in candidates if a.recommended), None)
        win = probe.ip(target)
        if win is not None and win.category == "Public":
            out.append(
                _diag(
                    "windows_network_public",
                    f'A rede "{win.alias}" está como Pública no Windows; marque-a como Privada.',
                    CONFIGURE_CMD,
                )
            )
    if wsl and probe is None and probe_failed:
        out.append(
            _diag(
                "windows_ip_unknown",
                "Não consegui descobrir o IP do Windows. Veja o README (Windows) ou defina KARAOKE_GUEST_ADVERTISE.",
                None,
                "info",
            )
        )
    if any(a.kind == "vpn" for a in candidates):
        out.append(
            _diag(
                "vpn_present", "Uma VPN está ativa; se os celulares não entrarem, desligue-a.", None, "info"
            )
        )
    return out


# ---- leitura do sistema -----------------------------------------------------------------------------------------
def _read_proc_version() -> str:
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _run(cmd: list[str], timeout: float) -> str:
    """Executa e devolve o stdout; levanta `OSError`/`subprocess.SubprocessError` em falha."""
    cwd = "/mnt/c" if os.path.isdir("/mnt/c") else None  # evita o aviso de "caminho UNC" do Windows
    res = subprocess.run(  # noqa: S603 — argv fixo, sem shell
        cmd, capture_output=True, timeout=timeout, check=False, cwd=cwd, stdin=subprocess.DEVNULL
    )
    if res.returncode != 0:
        raise subprocess.SubprocessError(
            f"código {res.returncode}: {res.stderr.decode('utf-8', 'replace')[:200]}"
        )
    return res.stdout.decode("utf-8", "replace")


def _powershell_argv() -> list[str] | None:
    exe = shutil.which("powershell.exe") or (
        POWERSHELL_FALLBACK if os.path.exists(POWERSHELL_FALLBACK) else None
    )
    if exe is None:
        return None
    encoded = base64.b64encode(PROBE_SCRIPT.encode("utf-16-le")).decode("ascii")
    return [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


class NetEnvironment:
    """Plataforma + sonda do Windows em cache (≤ 1×/60 s, fora do loop). `state()` só lê o cache."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        proc_version: Callable[[], str] = _read_proc_version,
        read_interfaces: Callable[[], list[netinfo.Interface]] = netinfo.read_interfaces,
        run: Callable[[list[str], float], str] = _run,
        powershell_argv: Callable[[], list[str] | None] = _powershell_argv,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.environ = os.environ if environ is None else environ
        self._proc_version = proc_version
        self._read_interfaces = read_interfaces
        self._run = run
        self._powershell_argv = powershell_argv
        self._clock = clock
        self._wsl: bool | None = None
        self._wslinfo_mode: str | None = None
        self.probe: WinProbe | None = None
        self.probe_failed = False
        self._probed_at: float | None = None
        self._task: asyncio.Task[None] | None = None

    # -- plataforma
    @property
    def wsl(self) -> bool:
        if self._wsl is None:
            self._wsl = is_wsl(self._proc_version(), self.environ)
            if self._wsl:
                try:
                    self._wslinfo_mode = self._run(
                        ["wslinfo", "--networking-mode"], WSLINFO_TIMEOUT_S
                    ).strip()
                except (OSError, subprocess.SubprocessError) as exc:
                    log.info("wslinfo indisponível (%s); usando heurística de rede", exc)
                log.info("WSL detectado (modo de rede: %s)", self._wslinfo_mode or "?")
        return self._wsl

    def _local(self) -> tuple[list[str], str | None]:
        try:
            ifaces = self._read_interfaces()
        except Exception:  # noqa: BLE001
            log.exception("falha ao ler as interfaces de rede")
            return [], None
        eth0 = next((i.address for i in ifaces if i.name == "eth0"), None)
        return [i.address for i in ifaces], eth0

    def platform(self) -> str:
        if not self.wsl:
            return "linux"
        local, eth0 = self._local()
        return resolve_platform(True, self._wslinfo_mode, local, eth0, self.probe)

    def eth0(self) -> str | None:
        return self._local()[1] if self.wsl else None

    def advertise(self) -> str | None:
        raw = self.environ.get(ADVERTISE_ENV, "").strip()
        if not raw:
            return None
        ip = _ipv4(raw)
        if ip is None:
            log.warning("%s=%r não é um IPv4 válido; ignorado", ADVERTISE_ENV, raw)
        return ip

    # -- sonda
    def probe_now(self) -> None:
        """Bloqueante (até `PROBE_TIMEOUT_S`): chame via `asyncio.to_thread`."""
        argv = self._powershell_argv()
        result: WinProbe | None = None
        if argv is None:
            log.warning("powershell.exe não encontrado (interop do WSL desligado?)")
        else:
            try:
                result = parse_probe(self._run(argv, PROBE_TIMEOUT_S))
                if result is None:
                    log.warning("sonda do Windows: JSON inválido")
            except subprocess.TimeoutExpired:
                log.warning("sonda do Windows: timeout de %.0f s", PROBE_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("sonda do Windows falhou: %s", exc)
        self.probe, self.probe_failed = result, result is None
        self._probed_at = self._clock()

    async def refresh(self) -> None:
        if not self.wsl:
            return
        await asyncio.to_thread(self.probe_now)

    def stale(self) -> bool:
        return self._probed_at is None or self._clock() - self._probed_at >= PROBE_TTL_S

    def schedule_refresh(self) -> None:
        """Dispara a sonda em background se o cache venceu (sem bloquear quem chama)."""
        if not self.wsl or not self.stale() or (self._task is not None and not self._task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._refresh_logged(), name="winnet-probe")

    async def _refresh_logged(self) -> None:
        try:
            await self.refresh()
        except Exception:  # noqa: BLE001
            log.exception("erro na sonda do Windows")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None

    # -- fachada usada pelo GuestService
    def candidates(self, discovered: list[LanAddress]) -> list[LanAddress]:
        platform = self.platform()
        return build_candidates(platform, discovered, self.probe, self.eth0(), self.advertise())

    def diagnostics(
        self, candidates: list[LanAddress], address: str | None, port: int
    ) -> list[dict[str, Any]]:
        return build_diagnostics(
            self.platform(), self.probe, self.probe_failed, candidates, address, port, self.eth0()
        )

    def firewall_hint(self, port: int) -> dict[str, str] | None:
        if self.wsl:
            return {"tool": "windows", "command": CONFIGURE_CMD}
        return netinfo.firewall_hint(port)
