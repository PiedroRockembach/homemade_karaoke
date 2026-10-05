"""Transcrição com faster-whisper (large-v3-turbo fp16 na GPU / small int8 na CPU) sobre a faixa de voz,
vad_filter e word_timestamps. Também devolve o envelope de energia do vocal (dB a cada 10 ms), usado pelo
alinhamento "pela voz" (`lyrics.align`) para corrigir inícios de palavra.

Roda **dentro do subprocesso** de IA (`worker.py`). Devolve segmentos "crus" (segundos) para
`lyrics.builder.from_whisper`, que aplica as regras de linha/alucinação do contrato.

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

Report = Callable[[dict[str, Any]], None]


SAMPLE_RATE = 16000


def decode_audio(path: str) -> Any:
    """Decodifica com o ffmpeg do sistema para float32 mono 16 kHz (o decodificador PyAV do faster-whisper 1.2.1
    quebra com o PyAV instalado: `open() got an unexpected keyword argument 'metadata_errors'`)."""
    import numpy as np

    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg não encontrado")
    out = subprocess.run(
        [
            ff,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def run(params: dict[str, Any], report: Report) -> dict[str, Any]:
    """params: input (vocals.wav/opus), model, device (cuda|cpu), language (opcional)."""
    # torch antes do CTranslate2: carrega as libs nvidia-*-cu12 (o LD_LIBRARY_PATH é o mecanismo principal)
    with contextlib.suppress(Exception):
        import torch  # noqa: F401
    from faster_whisper import WhisperModel

    device = params.get("device", "cpu")
    compute_type = "float16" if device == "cuda" else "int8"
    report({"type": "log", "message": f"carregando whisper {params['model']} em {device} ({compute_type})"})
    model = WhisperModel(params["model"], device=device, compute_type=compute_type, local_files_only=True)
    audio = decode_audio(params["input"])
    segments, info = model.transcribe(
        audio,
        language=params.get("language") or None,
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    duration = float(info.duration or 0) or None
    out: list[dict[str, Any]] = []
    for seg in segments:
        out.append(
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "no_speech_prob": seg.no_speech_prob,
                "avg_logprob": seg.avg_logprob,
                "compression_ratio": seg.compression_ratio,
                "words": [
                    {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                    for w in (seg.words or [])
                ],
            }
        )
        if duration:
            report({"type": "progress", "value": min(seg.end / duration, 0.99)})
    report({"type": "progress", "value": 1.0})
    from karaoke.lyrics.align import envelope_from_samples

    return {
        "segments": out,
        "envelope": envelope_from_samples(audio, SAMPLE_RATE),
        "language": info.language,
        "language_probability": info.language_probability,
        "duration_s": duration,
        "device": device,
    }
