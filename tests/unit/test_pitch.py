"""Testes unitários para o módulo de extração de pitch e notas (US-21)."""

import json

import numpy as np
import pytest

from karaoke.lyrics import pitch
from karaoke.lyrics.pitch import (
    extract_pitch_from_audio,
    freq_to_midi,
    get_song_pitch,
    midi_to_note_name,
)


def test_midi_and_note_conversions():
    assert midi_to_note_name(60) == "C4"
    assert midi_to_note_name(69) == "A4"
    assert midi_to_note_name(72) == "C5"

    assert freq_to_midi(440.0) == 69
    assert freq_to_midi(261.63) == 60
    assert freq_to_midi(0.0) == 0


def test_extract_pitch_synthetic_sine_wave():
    """Gera um sinal senoidal puro em A4 (440 Hz) de 1 segundo a 16 kHz."""
    sr = 16000
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # Tom A4 (440 Hz)
    audio = 0.5 * np.sin(2 * np.pi * 440.0 * t)

    notes = extract_pitch_from_audio(audio, sr)
    assert len(notes) >= 1
    assert notes[0]["midi"] == 69
    assert notes[0]["note_name"] == "A4"
    assert {"start", "end", "midi", "note_name"} <= notes[0].keys()
    assert notes[0]["start"] == 0.0
    assert notes[0]["end"] >= 0.8


def test_extract_pitch_two_notes():
    """Gera uma sequência de duas notas: C4 (261.6 Hz) seguido de G4 (392 Hz)."""
    sr = 16000
    t1 = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    t2 = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    note1 = 0.4 * np.sin(2 * np.pi * 261.63 * t1)  # C4
    note2 = 0.4 * np.sin(2 * np.pi * 392.00 * t2)  # G4
    audio = np.concatenate([note1, note2])

    notes = extract_pitch_from_audio(audio, sr)
    assert len(notes) == 2
    assert notes[0]["midi"] == 60
    assert notes[0]["note_name"] == "C4"
    assert notes[1]["midi"] == 67
    assert notes[1]["note_name"] == "G4"


def test_extract_pitch_silence():
    """Silêncio não deve gerar notas."""
    sr = 16000
    audio = np.zeros(sr)
    notes = extract_pitch_from_audio(audio, sr)
    assert notes == []


def test_get_song_pitch_failure_is_cached(tmp_path, monkeypatch):
    """Falha na decodificação: devolve [] e grava marcador, sem recalcular na próxima chamada."""
    (tmp_path / "vocals.opus").write_bytes(b"not audio")
    calls = []

    def boom(path):
        calls.append(path)
        raise RuntimeError("ffmpeg falhou")

    monkeypatch.setattr(pitch, "decode_vocals", boom)
    assert get_song_pitch(tmp_path) == []
    assert get_song_pitch(tmp_path) == []
    assert len(calls) == 1
    assert json.loads((tmp_path / "pitch.json").read_text())["notes"] == []


def test_get_song_pitch_missing_vocals_writes_nothing(tmp_path):
    assert get_song_pitch(tmp_path) == []
    assert not (tmp_path / "pitch.json").exists()


def test_get_song_pitch_legacy_cache_is_recomputed(tmp_path, monkeypatch):
    """pitch.json v1 (lista pura, algoritmo antigo) é recalculado e regravado no formato v2."""
    (tmp_path / "pitch.json").write_text(json.dumps([{"start": 0, "end": 1, "midi": 81, "note": "A5"}]))
    (tmp_path / "vocals.opus").write_bytes(b"x")
    sr = 16000
    t = np.arange(sr) / sr
    monkeypatch.setattr(pitch, "decode_vocals", lambda p: (0.5 * np.sin(2 * np.pi * 440.0 * t), sr))
    notes = get_song_pitch(tmp_path)
    assert notes[0]["midi"] == 69
    saved = json.loads((tmp_path / "pitch.json").read_text())
    assert saved["version"] == pitch.PITCH_VERSION and saved["notes"] == notes


@pytest.mark.parametrize("freq", [80.0, 90.0, 100.0, 110.0, 987.77])
def test_extract_pitch_low_and_high_sines(freq):
    """Vozes graves (lóbulo central não pode virar o período) e B5 (topo da faixa)."""
    sr = 16000
    t = np.arange(sr) / sr
    notes = extract_pitch_from_audio(0.5 * np.sin(2 * np.pi * freq * t), sr)
    assert [n["midi"] for n in notes] == [freq_to_midi(freq)]


def test_extract_pitch_sawtooth_and_harmonic_voice():
    sr = 16000
    t = np.arange(sr) / sr
    saw = 0.4 * (2 * ((100.0 * t) % 1) - 1)
    assert [n["midi"] for n in extract_pitch_from_audio(saw, sr)] == [43]
    voice = sum((0.5 / k) * np.sin(2 * np.pi * 110.0 * k * t) for k in range(1, 8))
    assert [n["midi"] for n in extract_pitch_from_audio(voice, sr)] == [45]


def test_extract_pitch_memory_bounded():
    """4 min a 16 kHz: pico extra bem abaixo de 100 MB (medido ~23 MB)."""
    import tracemalloc

    sr = 16000
    x = (0.3 * np.sin(2 * np.pi * 220.0 * np.arange(240 * sr) / sr)).astype(np.float32)
    tracemalloc.start()
    try:
        extract_pitch_from_audio(x, sr)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100e6
