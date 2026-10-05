"""Configuração do karaokê (variáveis de ambiente + defaults). Ver docs/ARCHITECTURE.md §8 e ADR-0002 §1.

Único módulo que lê variáveis de ambiente de rede/dados. Os demais recebem um `Config`.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_FALLBACK_TRIES = 10
DEVICES = ("auto", "cuda", "cpu")
SEPARATION_MODELS = ("htdemucs_ft", "htdemucs")


class ConfigError(ValueError):
    """Configuração inválida; a mensagem é exibida ao usuário (pt-BR)."""


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Config:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    data_dir: Path = field(default_factory=lambda: PROJECT_DIR / "data")
    device: str = "auto"
    separation_model: str = "htdemucs_ft"
    whisper_model: str | None = None  # None = large-v3-turbo na GPU / small na CPU
    open_browser: bool = True
    guest_mode: bool = False  # fixo na v1 (ADR-0002)

    # ---- caminhos derivados -------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "karaoke.db"

    @property
    def songs_dir(self) -> Path:
        return self.data_dir / "songs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    # ---- rede ---------------------------------------------------------------
    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Valores aceitos no header Host (porta efetiva, calculada depois do fallback)."""
        return frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(f"http://{h}" for h in self.allowed_hosts)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def with_port(self, port: int) -> Config:
        return replace(self, port=port)

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.songs_dir,
            self.tmp_dir,
            self.cache_dir / "thumbs",
            self.models_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_config(env: dict[str, str] | None = None) -> Config:
    """Lê `KARAOKE_*` do ambiente e valida. Levanta ConfigError com mensagem pt-BR."""
    env = dict(os.environ if env is None else env)
    host = env.get("KARAOKE_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    if not is_loopback_host(host):
        raise ConfigError(
            f"Acesso pela rede ainda não está disponível nesta versão (KARAOKE_HOST={host}). Use 127.0.0.1."
        )
    try:
        port = int(env.get("KARAOKE_PORT", DEFAULT_PORT))
    except ValueError as exc:
        raise ConfigError("KARAOKE_PORT deve ser um número inteiro.") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("KARAOKE_PORT deve estar entre 1 e 65535.")
    data_dir = Path(env.get("KARAOKE_DATA_DIR") or PROJECT_DIR / "data")
    if not data_dir.is_absolute():
        data_dir = (PROJECT_DIR / data_dir).resolve()
    device = env.get("KARAOKE_DEVICE", "auto").lower()
    if device not in DEVICES:
        raise ConfigError(f"KARAOKE_DEVICE deve ser um de {', '.join(DEVICES)}.")
    sep = env.get("KARAOKE_SEPARATION_MODEL", "htdemucs_ft")
    if sep not in SEPARATION_MODELS:
        raise ConfigError(f"KARAOKE_SEPARATION_MODEL deve ser um de {', '.join(SEPARATION_MODELS)}.")
    return Config(
        host=host,
        port=port,
        data_dir=data_dir,
        device=device,
        separation_model=sep,
        whisper_model=env.get("KARAOKE_WHISPER_MODEL") or None,
        open_browser=env.get("KARAOKE_NO_BROWSER", "") not in ("1", "true", "yes"),
    )
