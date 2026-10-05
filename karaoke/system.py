"""Detecção de GPU/CUDA, modelos baixados, ferramentas (ffmpeg, yt-dlp, Deno) e disco.

Dono: backend-engineer (T-006) a partir do esqueleto do architect (T-004).
- `collect(config)`: rápido, sem importar torch no processo do servidor (GPU via subprocesso, resultado em cache).
- `cuda_lib_dirs()` / `cuda_env()`: diretórios `site-packages/nvidia/*/lib` para o `LD_LIBRARY_PATH` dos
  subprocessos de IA (CTranslate2 precisa de `libcublas.so.12`; ver ADR-0001 "Plano concreto para GPU").
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

from karaoke.config import PROJECT_DIR, Config


# ---- ambiente dos modelos / CUDA ------------------------------------------------------
def model_env(config: Config) -> dict[str, str]:
    """HF_HOME/TORCH_HOME apontando para data/models (aplicar em os.environ antes de importar torch/hf), sem
    telemetria do huggingface_hub (local-first) e sem avisos dele no terminal do usuário."""
    return {
        "HF_HOME": str(config.models_dir / "hf"),
        "TORCH_HOME": str(config.models_dir / "torch"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "HF_HUB_VERBOSITY": "error",
    }


def cuda_lib_dirs() -> list[str]:
    purelib = Path(sysconfig.get_paths()["purelib"])
    return sorted(str(p) for p in (purelib / "nvidia").glob("*/lib") if p.is_dir())


def cuda_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    dirs = cuda_lib_dirs()
    if dirs:
        env["LD_LIBRARY_PATH"] = os.pathsep.join([*dirs, *filter(None, [env.get("LD_LIBRARY_PATH")])])
    return env


def venv_bin_on_path(env: dict[str, str] | None = None) -> dict[str, str]:
    """Garante `.venv/bin` no PATH (yt-dlp encontra o `deno` instalado pelo extra `yt-dlp[deno]`)."""
    env = dict(os.environ if env is None else env)
    vbin = str(Path(sys.executable).parent)
    if vbin not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = os.pathsep.join([vbin, env.get("PATH", "")])
    return env


# ---- ferramentas ---------------------------------------------------------------------------
def _version_of(cmd: list[str], pattern: str) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(pattern, out)
    return m.group(1) if m else None


def find_deno() -> str | None:
    found = shutil.which("deno", path=venv_bin_on_path().get("PATH"))
    if found:
        return found
    try:
        import deno  # type: ignore[import-not-found]

        return str(deno.find_deno_bin())
    except Exception:
        return None


def tools() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ff_ver = _version_of([ffmpeg, "-version"], r"ffmpeg version (\S+)") if ffmpeg else None
    try:
        from yt_dlp.version import __version__ as ytv
    except Exception:
        ytv = None
    deno = find_deno()
    deno_ver = _version_of([deno, "--version"], r"deno (\S+)") if deno else None
    return {
        "ffmpeg": {"ok": bool(ff_ver), "version": ff_ver},
        "yt_dlp": {"ok": ytv is not None, "version": ytv},
        "js_runtime": {"ok": bool(deno_ver), "name": "deno", "version": deno_ver},
    }


# ---- modelos ---------------------------------------------------------------------------------
def models(config: Config, device: str) -> list[dict[str, Any]]:
    from karaoke.jobs import models as m

    whisper_active = config.whisper_model or ("large-v3-turbo" if device == "cuda" else "small")
    out = []
    for mid in m.SEPARATION_MODELS:
        out.append(
            {
                "id": mid,
                "kind": "separation",
                "downloaded": m.is_downloaded(config, mid),
                "size_mb": m.size_mb(mid),
                "active": mid == config.separation_model,
            }
        )
    for mid in m.TRANSCRIPTION_MODELS:
        out.append(
            {
                "id": mid,
                "kind": "transcription",
                "downloaded": m.is_downloaded(config, mid),
                "size_mb": m.size_mb(mid),
                "active": mid == whisper_active,
            }
        )
    return out


def library_stats(db: Any) -> dict[str, int]:
    """Músicas prontas e espaço ocupado (MB) a partir do banco."""
    if db is None:
        return {"songs": 0, "size_mb": 0}
    try:
        conn = db.connect()
        try:
            r = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM songs WHERE status = 'ready'"
            ).fetchone()
        finally:
            conn.close()
        return {"songs": int(r["n"]), "size_mb": round(int(r["b"]) / (1024 * 1024))}
    except Exception:  # noqa: BLE001 — banco ainda não migrado
        return {"songs": 0, "size_mb": 0}


# ---- GPU ------------------------------------------------------------------------------------------
_GPU_PROBE = r"""
import json, sys
r = {"available": False, "name": None, "vram_total_mb": None, "compute_capability": None,
     "torch": None, "cuda": None, "error": None}
try:
    import torch
    r["torch"] = torch.__version__; r["cuda"] = torch.version.cuda
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        r["name"] = p.name; r["vram_total_mb"] = p.total_memory // (1024 * 1024)
        r["compute_capability"] = "%d.%d" % torch.cuda.get_device_capability(0)
        x = torch.ones((256, 256), device="cuda"); float((x @ x).sum().item())
        r["available"] = True
except Exception as e:
    r["error"] = f"{type(e).__name__}: {e}"
try:
    import ctranslate2
    r["ct2_cuda_devices"] = ctranslate2.get_cuda_device_count()
except Exception as e:
    r["ct2_cuda_devices"] = 0; r["ct2_error"] = f"{type(e).__name__}: {e}"
print(json.dumps(r))
"""


@functools.lru_cache(maxsize=1)
def probe_gpu() -> dict[str, Any]:
    """Testa torch + CTranslate2 na CUDA num subprocesso (não carrega torch no servidor). Cacheado."""
    try:
        out = subprocess.run(
            [sys.executable, "-c", _GPU_PROBE],
            capture_output=True,
            text=True,
            timeout=120,
            env=cuda_env(),
            check=False,
        )
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}", "ct2_cuda_devices": 0}


def resolve_device(config: Config, gpu: dict[str, Any]) -> tuple[str, str | None]:
    """Device efetivo e aviso pt-BR (None se ok)."""
    usable = bool(gpu.get("available")) and gpu.get("ct2_cuda_devices", 0) > 0
    if config.device == "cpu":
        return "cpu", None
    if usable:
        return "cuda", None
    if gpu.get("available"):
        return "cuda", "CTranslate2 sem CUDA — a transcrição usará CPU (mais lenta)."
    return "cpu", "CUDA indisponível — usando CPU, o processamento será mais lento."


def collect(config: Config, db: Any = None) -> dict[str, Any]:
    """Corpo de `GET /api/system` (contrato §3)."""
    from karaoke.jobs.models import current_download

    gpu = probe_gpu()
    device, warning = resolve_device(config, gpu)
    try:
        data_dir = str(config.data_dir.relative_to(PROJECT_DIR))
    except ValueError:
        data_dir = str(config.data_dir)
    disk = shutil.disk_usage(config.data_dir if config.data_dir.exists() else PROJECT_DIR)
    return {
        "device": device,
        "gpu": {
            "available": bool(gpu.get("available")),
            "name": gpu.get("name"),
            "vram_total_mb": gpu.get("vram_total_mb"),
            "compute_capability": gpu.get("compute_capability"),
            "torch": gpu.get("torch"),
            "cuda": gpu.get("cuda"),
            "warning": warning,
        },
        "models": models(config, device),
        "model_download": current_download(),
        "tools": tools(),
        "data_dir": data_dir,
        "disk_free_mb": disk.free // (1024 * 1024),
        "library": library_stats(db),
    }
