"""Modelos de IA: quais arquivos cada um precisa, se já estão em data/models e download com progresso em bytes.

- Separação: demucs 4.1 carrega do HuggingFace (`adefossez/HTDemucs-ft`, `adefossez/HTDemucs`). Como só precisamos
  da voz, para `htdemucs_ft` baixamos só o yaml + o modelo especialista em voz (84 MB, não 320 MB) — ver
  `separate.py`.
- Transcrição: repositórios CTranslate2 do faster-whisper (mesmos arquivos que `faster_whisper.download_model`).

O download roda no processo do servidor (thread do JobRunner ou da rota `POST /api/system/models/{id}/download`),
um por vez (`_download_lock`); o progresso é exposto por `current_download()` e publicado como `system.updated`.

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import contextlib
import fnmatch
import io
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from karaoke.config import Config

log = logging.getLogger("karaoke.models")

VOCALS_SOURCE_INDEX = 3  # htdemucs: sources = [drums, bass, other, vocals]

DEMUCS_REPOS = {"htdemucs_ft": "adefossez/HTDemucs-ft", "htdemucs": "adefossez/HTDemucs"}
# fallback se o yaml ainda não foi baixado (só para checar "downloaded" sem rede)
DEMUCS_VOCAL_SIGS = {"htdemucs_ft": ["04573f0d"], "htdemucs": ["955717e8"]}
DEMUCS_SIZE_MB = {"htdemucs_ft": 84, "htdemucs": 84}

WHISPER_REPOS = {
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1620),
    "medium": ("Systran/faster-whisper-medium", 1530),
    "small": ("Systran/faster-whisper-small", 484),
    "tiny": ("Systran/faster-whisper-tiny", 75),
}
WHISPER_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]
SEPARATION_MODELS = tuple(DEMUCS_REPOS)
TRANSCRIPTION_MODELS = ("large-v3-turbo", "medium", "small")
ALL_MODELS = SEPARATION_MODELS + TRANSCRIPTION_MODELS


class ModelDownloadError(Exception):
    pass


@dataclass
class ModelSpec:
    id: str
    kind: str  # separation | transcription
    repo: str


def spec(model_id: str) -> ModelSpec | None:
    if model_id in DEMUCS_REPOS:
        return ModelSpec(model_id, "separation", DEMUCS_REPOS[model_id])
    if model_id in WHISPER_REPOS:
        return ModelSpec(model_id, "transcription", WHISPER_REPOS[model_id][0])
    return None


def hf_cache_dir(config: Config) -> Path:
    return config.models_dir / "hf" / "hub"


def _cached(config: Config, repo: str, filename: str) -> str | None:
    from huggingface_hub import try_to_load_from_cache

    p = try_to_load_from_cache(repo, filename, cache_dir=str(hf_cache_dir(config)))
    return p if isinstance(p, str) else None


def demucs_vocal_sigs(config: Config, model_id: str) -> list[str]:
    """Assinaturas dos modelos do bag que contribuem para a voz (lê o yaml se estiver em cache)."""
    repo = DEMUCS_REPOS[model_id]
    y = _cached(config, repo, f"{model_id}.yaml")
    if not y:
        return DEMUCS_VOCAL_SIGS[model_id]
    return vocal_sigs_from_bag(yaml.safe_load(Path(y).read_text()))


def vocal_sigs_from_bag(bag: dict[str, Any]) -> list[str]:
    models = list(bag.get("models") or [])
    weights = bag.get("weights")
    if not weights:
        return models
    return [
        m
        for m, w in zip(models, weights, strict=False)
        if len(w) > VOCALS_SOURCE_INDEX and w[VOCALS_SOURCE_INDEX]
    ]


def required_files(config: Config, model_id: str) -> list[str]:
    s = spec(model_id)
    if s is None:
        return []
    if s.kind == "separation":
        return [f"{model_id}.yaml"] + [f"{sig}.safetensors" for sig in demucs_vocal_sigs(config, model_id)]
    return ["config.json", "model.bin", "tokenizer.json"]


def is_downloaded(config: Config, model_id: str) -> bool:
    s = spec(model_id)
    if s is None:
        return False
    try:
        if s.kind == "transcription":
            snap = hf_cache_dir(config) / f"models--{s.repo.replace('/', '--')}" / "snapshots"
            return any(snap.glob("*/model.bin")) and any(snap.glob("*/config.json"))
        return all(_cached(config, s.repo, f) for f in required_files(config, model_id))
    except Exception:  # noqa: BLE001
        return False


def size_mb(model_id: str) -> int:
    if model_id in DEMUCS_SIZE_MB:
        return DEMUCS_SIZE_MB[model_id]
    return WHISPER_REPOS.get(model_id, ("", 0))[1]


# ---- estado global do download -----------------------------------------------------
_download_lock = threading.Lock()
_state_lock = threading.Lock()
_current: dict[str, Any] | None = None
_listeners: list[Callable[[dict[str, Any] | None], None]] = []


def add_listener(fn: Callable[[dict[str, Any] | None], None]) -> None:
    """`fn(estado|None)` a cada mudança do download (ex.: espelhar em `app.state.model_download`)."""
    _listeners.append(fn)


def remove_listener(fn: Callable[[dict[str, Any] | None], None]) -> None:
    with contextlib.suppress(ValueError):
        _listeners.remove(fn)


def current_download() -> dict[str, Any] | None:
    with _state_lock:
        return dict(_current) if _current else None


def _set_current(value: dict[str, Any] | None) -> None:
    global _current
    with _state_lock:
        _current = value
    for fn in list(_listeners):
        with contextlib.suppress(Exception):
            fn(dict(value) if value else None)


def _tqdm_factory(on_bytes: Callable[[int], None]) -> type:
    from tqdm.auto import tqdm

    class _Tracker(tqdm):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("name", None)
            kwargs["disable"] = False
            kwargs["file"] = io.StringIO()
            super().__init__(*args, **kwargs)

        def update(self, n: float | None = 1) -> bool | None:
            if n:
                on_bytes(int(n))
            return super().update(n)

    return _Tracker


def _repo_files(repo: str) -> dict[str, int]:
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True)
    return {s.rfilename: int(s.size or 0) for s in info.siblings or []}


def download(
    config: Config,
    model_id: str,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Baixa o modelo para data/models/hf (bloqueante). Levanta ModelDownloadError com mensagem pt-BR."""
    from huggingface_hub import hf_hub_download

    s = spec(model_id)
    if s is None:
        raise ModelDownloadError(f"Modelo desconhecido: {model_id}")
    with _download_lock:
        if is_downloaded(config, model_id):
            return
        _set_current({"model_id": model_id, "progress": 0.0})
        try:
            try:
                listing = _repo_files(s.repo)
            except Exception as exc:  # noqa: BLE001
                raise ModelDownloadError("Não foi possível baixar o modelo de IA (sem internet?).") from exc
            if s.kind == "separation":
                bag_file = f"{model_id}.yaml"
                y = hf_hub_download(s.repo, bag_file, cache_dir=str(hf_cache_dir(config)))
                sigs = vocal_sigs_from_bag(yaml.safe_load(Path(y).read_text()))
                files = [f"{sig}.safetensors" for sig in sigs]
            else:
                files = [f for f in listing if any(fnmatch.fnmatch(f, p) for p in WHISPER_PATTERNS)]
            total = sum(listing.get(f, 0) for f in files) or 1
            done = 0

            def on_bytes(n: int) -> None:
                nonlocal done
                if cancel is not None and cancel.is_set():
                    raise ModelDownloadError("Download cancelado.")
                done += n
                frac = min(done / total, 0.999)
                _set_current({"model_id": model_id, "progress": round(frac, 3)})
                if on_progress:
                    on_progress(frac)

            tq = _tqdm_factory(on_bytes)
            for f in files:
                if _cached(config, s.repo, f):
                    done += listing.get(f, 0)
                    continue
                hf_hub_download(s.repo, f, cache_dir=str(hf_cache_dir(config)), tqdm_class=tq)
            if on_progress:
                on_progress(1.0)
        except ModelDownloadError:
            raise
        except OSError as exc:
            raise ModelDownloadError(f"Falha ao gravar o modelo: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("download do modelo %s falhou", model_id)
            raise ModelDownloadError("Não foi possível baixar o modelo de IA (sem internet?).") from exc
        finally:
            _set_current(None)
