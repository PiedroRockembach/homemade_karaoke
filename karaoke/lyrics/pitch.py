"""Extração e análise de melodia vocal (pitch) para o jogo de pontuação do Palco (US-18, US-21).

Dono: backend-engineer (T-003, ciclo 20261017-1115-karaoke).

Usa YIN (diferença quadrática + CMND, ADR-0004) sobre a faixa vocal isolada, com interpolação
parabólica e limiar de energia RMS, para estimar a frequência fundamental (f0) e quantizar em notas
MIDI (C2 a B5). Processa em blocos de quadros em float32 (memória limitada mesmo em faixas longas).
Os resultados são salvos em data/songs/<id>/pitch.json no formato versionado
`{"version": PITCH_VERSION, "notes": [...]}`; caches de versões anteriores são recalculados.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("karaoke.pitch")

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
TARGET_SR = 16000
FRAME_LEN = 1024
HOP_SIZE = 320  # 20 ms a 16 kHz
MIN_FREQ = 65.0  # ~C2
MAX_FREQ = 1000.0  # cobre B5 (987,8 Hz), igual a mic-pitch.js
RMS_THRESHOLD = 0.012
YIN_THRESHOLD = 0.15  # CMND abaixo disso = quadro vozeado
BLOCK_FRAMES = 512  # quadros por bloco de FFT (~10 s de áudio)
PITCH_VERSION = 2  # v1 (lista pura) usava autocorrelação que errava vozes graves
MIN_NOTE_DURATION_S = 0.08  # notas menores que 80ms são descartadas


def midi_to_note_name(midi: int) -> str:
    """Converte número MIDI (ex: 60) para nome da nota (ex: C4)."""
    octave = (midi // 12) - 1
    return f"{NOTE_NAMES[midi % 12]}{octave}"


def freq_to_midi(freq: float) -> int:
    """Converte frequência em Hz para nota MIDI inteira."""
    if freq <= 0:
        return 0
    return int(round(69 + 12 * np.log2(freq / 440.0)))


def _note_fields(midi: int) -> dict[str, Any]:
    """`note_name` é o campo do contrato (AC-21.2); `note` fica como alias legado."""
    name = midi_to_note_name(midi)
    return {"midi": midi, "note_name": name, "note": name}


def _frame_midis(x: np.ndarray, sr: int) -> np.ndarray:
    """Nota MIDI por quadro (0 = não vozeado), via YIN em blocos de BLOCK_FRAMES quadros."""
    min_lag = max(2, int(sr / MAX_FREQ))
    max_lag = min(FRAME_LEN // 2, int(np.ceil(sr / MIN_FREQ)))
    win = FRAME_LEN - max_lag  # janela de integração do YIN
    n_fft = 1 << int(np.ceil(np.log2(FRAME_LEN + win)))
    n_frames = (len(x) - FRAME_LEN) // HOP_SIZE + 1
    out = np.zeros(n_frames, dtype=np.int16)
    lags = np.arange(1, max_lag + 1, dtype=np.float32)
    stride = x.strides[0]

    for b0 in range(0, n_frames, BLOCK_FRAMES):
        nb = min(BLOCK_FRAMES, n_frames - b0)
        frames = np.lib.stride_tricks.as_strided(
            x[b0 * HOP_SIZE :], shape=(nb, FRAME_LEN), strides=(stride * HOP_SIZE, stride), writeable=False
        )
        sq = frames * frames
        rms = np.sqrt(sq.mean(axis=1))
        # r[τ] = Σ_{j<win} x[j]·x[j+τ] (correlação cruzada via FFT, sem wrap pois n_fft ≥ FRAME_LEN + win)
        spec = np.fft.rfft(frames, n=n_fft, axis=1)
        spec *= np.conj(np.fft.rfft(frames[:, :win], n=n_fft, axis=1))
        r = np.fft.irfft(spec, n=n_fft, axis=1)[:, : max_lag + 1].astype(np.float32)
        del spec
        cs = np.zeros((nb, FRAME_LEN + 1), dtype=np.float32)
        np.cumsum(sq, axis=1, out=cs[:, 1:])
        del sq
        energy = cs[:, win : win + max_lag + 1] - cs[:, : max_lag + 1]  # Σ x² em [τ, τ+win)
        del cs
        d = np.maximum(energy[:, :1] + energy - 2.0 * r, 0.0)
        del energy, r
        # CMND: d'(τ) = d(τ)·τ / Σ_{k=1..τ} d(k); d'(0) = 1
        cmnd = np.ones_like(d)
        cum = np.cumsum(d[:, 1:], axis=1)
        cmnd[:, 1:] = d[:, 1:] * lags / np.maximum(cum, 1e-12)
        del d, cum

        below = cmnd[:, min_lag:] < YIN_THRESHOLD
        voiced = below.any(axis=1) & (rms > RMS_THRESHOLD)
        tau = np.argmax(below, axis=1) + min_lag
        rows = np.arange(nb)
        # desce até o mínimo local após o primeiro cruzamento do limiar
        for _ in range(max_lag):
            nxt = np.minimum(tau + 1, max_lag)
            move = voiced & (tau < max_lag) & (cmnd[rows, nxt] < cmnd[rows, tau])
            if not move.any():
                break
            tau[move] += 1
        # interpolação parabólica
        t0 = np.clip(tau, 1, max_lag - 1)
        a, b, c = cmnd[rows, t0 - 1], cmnd[rows, t0], cmnd[rows, t0 + 1]
        denom = a - 2 * b + c
        safe = np.abs(denom) > 1e-9
        shift = np.where(safe, 0.5 * (a - c) / np.where(safe, denom, 1.0), 0.0)
        refined = t0 + np.clip(shift, -1.0, 1.0)
        f0 = np.where(voiced, sr / np.maximum(refined, 1.0), 0.0)
        midi = np.zeros(nb, dtype=np.int16)
        ok = f0 > 0
        midi[ok] = np.rint(69 + 12 * np.log2(f0[ok] / 440.0)).astype(np.int16)
        midi[(midi < 36) | (midi > 84)] = 0  # faixa vocal musical C2..C6
        out[b0 : b0 + nb] = midi
    return out


def extract_pitch_from_audio(audio_data: np.ndarray, sr: int) -> list[dict[str, Any]]:
    """Estima as notas musicais contínuas a partir de uma série temporal de áudio monofônica."""
    audio = np.asarray(audio_data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1, dtype=np.float32)

    # Resample simples por decimação para ~16 kHz se necessário
    if sr > TARGET_SR:
        step = max(1, round(sr / TARGET_SR))
        audio = audio[::step]
        sr = sr // step
    audio = np.ascontiguousarray(audio)

    if len(audio) < FRAME_LEN:
        return []
    midi_frames = _frame_midis(audio, sr)
    n_frames = len(midi_frames)

    # Filtro simples (tamanho 3) para suavizar saltos isolados de um quadro
    smoothed = midi_frames.copy()
    same = (midi_frames[:-2] == midi_frames[2:]) & (midi_frames[:-2] > 0)
    smoothed[1:-1][same] = midi_frames[:-2][same]

    # Agrupamento de quadros consecutivos em notas contínuas
    notes: list[dict[str, Any]] = []
    current_midi = 0
    start_frame = 0
    for i in range(n_frames + 1):
        m = int(smoothed[i]) if i < n_frames else 0
        if m != current_midi:
            if current_midi > 0:
                duration_s = (i - start_frame) * HOP_SIZE / sr
                if duration_s >= MIN_NOTE_DURATION_S:
                    notes.append(
                        {
                            "start": round(start_frame * HOP_SIZE / sr, 2),
                            "end": round(i * HOP_SIZE / sr, 2),
                            **_note_fields(current_midi),
                        }
                    )
            current_midi = m
            start_frame = i
    return notes


def attach_words_to_notes(notes: list[dict[str, Any]], transcript_file: Path | None) -> list[dict[str, Any]]:
    """Associa as palavras sincronizadas do transcript.json às notas correspondentes."""
    if not transcript_file or not transcript_file.exists():
        return notes

    try:
        data = json.loads(transcript_file.read_text(encoding="utf-8"))
        words_list: list[dict[str, Any]] = []
        for seg in data.get("segments", []):
            for w in seg.get("words", []):
                words_list.append(
                    {
                        "start": float(w.get("start", 0)),
                        "end": float(w.get("end", 0)),
                        "text": str(w.get("word", "")).strip(),
                    }
                )

        if not words_list:
            return notes

        # Para cada nota, encontra a palavra que mais sobrepõe seu intervalo
        for n in notes:
            n_start = n["start"]
            n_end = n["end"]
            best_word = ""
            best_overlap = 0.0

            for w in words_list:
                w_start = w["start"]
                w_end = w["end"]
                overlap = max(0.0, min(n_end, w_end) - max(n_start, w_start))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_word = w["text"]

            if best_word:
                n["text"] = best_word

    except Exception as e:
        log.warning("Falha ao associar palavras às notas do transcript: %s", e)

    return notes


def decode_vocals(path: Path) -> tuple[np.ndarray, int]:
    """Decodifica o áudio para float32 mono 16 kHz com o ffmpeg do sistema (libsndfile nem sempre lê Opus).

    Sem ffmpeg, tenta `soundfile` como alternativa. Levanta exceção se nenhum dos dois conseguir.
    """
    ff = shutil.which("ffmpeg")
    if ff:
        out = subprocess.run(
            [ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path)]
            + ["-ac", "1", "-ar", str(TARGET_SR), "-f", "f32le", "-"],
            capture_output=True,
            check=True,
            timeout=600,
        ).stdout
        return np.frombuffer(out, dtype=np.float32), TARGET_SR
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32")
    return data, int(sr)


def _write_pitch_atomic(pitch_file: Path, notes: list[dict[str, Any]], failed: bool = False) -> None:
    payload: dict[str, Any] = {"version": PITCH_VERSION, "notes": notes}
    if failed:
        payload["failed"] = True
    tmp = pitch_file.with_name(pitch_file.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, pitch_file)


def _load_cache(pitch_file: Path) -> list[dict[str, Any]] | None:
    """Notas do pitch.json se ele existir, for válido e da versão atual; senão None (recalcular)."""
    if not pitch_file.is_file():
        return None
    try:
        data = json.loads(pitch_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Erro ao ler %s: %s; recalculando", pitch_file, e)
        return None
    if not isinstance(data, dict) or data.get("version") != PITCH_VERSION:
        log.info("pitch.json em formato antigo em %s; recalculando", pitch_file)
        return None
    notes = data.get("notes")
    if not isinstance(notes, list) or not all(isinstance(n, dict) and "midi" in n for n in notes):
        log.warning("pitch.json inválido em %s; recalculando", pitch_file)
        return None
    return notes


def extract_and_save_song_pitch(song_dir: Path, source: Path | None = None) -> list[dict[str, Any]]:
    """Processa a voz da música e salva pitch.json (escrita atômica).

    `source` permite ao job usar `stems/vocals.wav` direto (evita decodificar o Opus recém-gerado);
    o padrão é `song_dir/vocals.opus`. Se a extração falhar (ffmpeg ausente, arquivo corrompido),
    grava `notes: []` com `failed: true` como marcador para não reprocessar a cada requisição; o job de
    preparo sobrescreve ao gerar a música de novo. Sem arquivo de voz não grava nada e devolve `[]`.
    """
    vocals_file = source or song_dir / "vocals.opus"
    pitch_file = song_dir / "pitch.json"
    transcript_file = song_dir / "transcript.json"

    if not vocals_file.is_file():
        return []

    failed = False
    try:
        data, sr = decode_vocals(vocals_file)
        notes = extract_pitch_from_audio(data, sr)
        del data
        notes = attach_words_to_notes(notes, transcript_file if transcript_file.exists() else None)
    except Exception as e:
        log.error("Erro ao extrair pitch de %s: %s (cacheando lista vazia)", vocals_file, e)
        notes, failed = [], True
    try:
        _write_pitch_atomic(pitch_file, notes, failed)
        log.info("Pitch salvo: %s (%d notas)", pitch_file, len(notes))
    except OSError as e:
        log.error("Não foi possível gravar %s: %s", pitch_file, e)
    return notes


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _song_lock(song_dir: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(song_dir), threading.Lock())


def get_song_pitch(song_dir: Path) -> list[dict[str, Any]]:
    """Notas de pitch da música (cache pitch.json ou extração sob demanda). Nunca levanta.

    Pedidos simultâneos da mesma música esperam uma única extração (trava por diretório).
    """
    pitch_file = song_dir / "pitch.json"
    cached = _load_cache(pitch_file)
    if cached is not None:
        return cached
    try:
        with _song_lock(song_dir):
            cached = _load_cache(pitch_file)
            if cached is not None:
                return cached
            return extract_and_save_song_pitch(song_dir)
    except Exception as e:  # defesa extra: a rota nunca deve responder 500 por causa do pitch
        log.error("Falha inesperada no pitch de %s: %s", song_dir, e)
        return []
