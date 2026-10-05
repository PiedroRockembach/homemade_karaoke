"""Separação de voz com demucs (htdemucs_ft | htdemucs): vocals.wav e instrumental.wav, device cuda/cpu, progresso.

Roda **dentro do subprocesso** de IA (`worker.py`). Estratégia (desvio consciente, ver HANDOFF T-006):
o `htdemucs_ft` é um "bag" de 4 modelos, cada um especialista em uma fonte (pesos identidade). Como o karaokê só
precisa de voz × resto, rodamos **só o especialista em voz** e fazemos `instrumental = mix − vocals`. O resultado
de voz é idêntico ao do bag completo, com 1/4 do tempo de GPU e do download. `full_bag=True` volta ao
comportamento clássico (`instrumental = drums + bass + other`).

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from karaoke.jobs.models import DEMUCS_REPOS, VOCALS_SOURCE_INDEX, vocal_sigs_from_bag

Report = Callable[[dict[str, Any]], None]


def load_model(model_id: str, full_bag: bool = False) -> Any:
    """Carrega do cache HF (offline). Retorna um BagOfModels com só os modelos necessários."""
    import yaml
    from demucs.apply import BagOfModels
    from demucs.hf import load_safetensors_model
    from huggingface_hub import hf_hub_download

    repo = DEMUCS_REPOS[model_id]
    bag_path = hf_hub_download(repo, f"{model_id}.yaml", local_files_only=True)
    with open(bag_path) as fh:
        bag = yaml.safe_load(fh)
    sigs = list(bag["models"]) if full_bag else vocal_sigs_from_bag(bag)
    models = [
        load_safetensors_model(hf_hub_download(repo, f"{s}.safetensors", local_files_only=True)) for s in sigs
    ]
    weights = None  # só o especialista em voz: peso 1 (evita divisão por zero nas outras fontes)
    if full_bag and bag.get("weights"):
        by_sig = dict(zip(bag["models"], bag["weights"], strict=False))
        weights = [by_sig[s] for s in sigs]
    model = BagOfModels(models, weights, bag.get("segment"))
    model.eval()
    return model


def _count_chunks(model: Any, length: int, overlap: float, shifts: int) -> int:
    seg = float(model.segment) if getattr(model, "segment", None) else 7.8
    seg_len = int(model.samplerate * seg)
    stride = max(int((1 - overlap) * seg_len), 1)
    per_pass = math.ceil(length / stride)
    return max(per_pass * max(shifts, 1) * len(getattr(model, "models", [model])), 1)


def run(params: dict[str, Any], report: Report) -> dict[str, Any]:
    """params: input, out_dir, model, device, full_bag (opcional), shifts (opcional)."""
    import soundfile as sf
    import torch
    from demucs.apply import apply_model

    device = params.get("device", "cpu")
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    out_dir = Path(params["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    full_bag = bool(params.get("full_bag", False))
    shifts = int(params.get("shifts", 1))
    overlap = 0.25

    report({"type": "log", "message": f"carregando {params['model']} em {device}"})
    model = load_model(params["model"], full_bag=full_bag)

    data, sr = sf.read(params["input"], dtype="float32", always_2d=True)  # (n, ch)
    if sr != model.samplerate:
        raise RuntimeError(f"taxa de amostragem {sr} != {model.samplerate} (o ffmpeg deveria ter convertido)")
    wav = torch.from_numpy(data.T.copy())  # (ch, n)
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    norm = (wav - mean) / std

    total = _count_chunks(model, norm.shape[-1], overlap, shifts)
    done = 0

    def cb(d: dict[str, Any]) -> None:
        nonlocal done
        if d.get("state") == "end":
            done += 1
            report({"type": "progress", "value": min(done / total, 0.99)})

    with torch.inference_mode():
        sources = apply_model(
            model,
            norm[None],
            device=device,
            shifts=shifts,
            split=True,
            overlap=overlap,
            progress=False,
            callback=cb,
        )[0]
    sources = sources * std + mean
    names = list(model.sources)
    vocals = sources[names.index("vocals") if "vocals" in names else VOCALS_SOURCE_INDEX]
    instrumental = sum(sources[i] for i, n in enumerate(names) if n != "vocals") if full_bag else wav - vocals
    sf.write(out_dir / "vocals.wav", vocals.cpu().numpy().T, sr, subtype="FLOAT")
    sf.write(out_dir / "instrumental.wav", instrumental.cpu().numpy().T, sr, subtype="FLOAT")
    report({"type": "progress", "value": 1.0})
    return {"device": device, "samplerate": sr, "samples": int(wav.shape[-1])}
