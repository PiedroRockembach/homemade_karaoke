"""Alinhamento "pela voz" (lyrics.align) com transcrições sintéticas — sem rede/GPU."""

from __future__ import annotations

from karaoke.lyrics import align, builder
from tests.fixtures.pipeline import assert_lyrics_invariants

CHORUS = "O que tem que ser assim será"
LRC = f"""[00:10.00]Toda vez que eu te vejo
[00:14.00]Eu te quero e te desejo
[00:18.00]{CHORUS}
[00:22.00]Vai acontecer sei esperar
[00:26.00]
[00:40.00]Não dá pra disfarçar
[00:44.00]{CHORUS}
[00:48.00]Vai acontecer sei esperar
"""
DUR = 60000


def lrc_lines() -> list[dict]:
    lines, _ = builder.from_lrc(LRC, DUR)
    return lines


def sung(lines: list[dict], offset_ms: int = 0, skip: set[int] = frozenset(), drop: set[tuple] = frozenset()):
    """Palavras "cantadas": cada linha começa em LRC + offset, 300 ms por palavra (um segmento por linha)."""
    segs = []
    for li, ln in enumerate(lines):
        if li in skip:
            continue
        t = (ln["start_ms"] + offset_ms) / 1000
        words = []
        for k, tok in enumerate(ln["text"].split()):
            if (li, k) not in drop:
                words.append({"word": " " + tok, "start": t, "end": t + 0.28, "probability": 0.9})
            t += 0.3
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": "", "words": words})
    return segs


def starts(lines: list[dict]) -> list[int]:
    return [ln["start_ms"] for ln in lines]


def test_norm_word():
    assert align.norm_word("Será,") == "sera"
    assert align.norm_word("'Cause") == "cause"
    assert align.norm_word("Não!") == "nao"
    assert align.norm_word("...") == ""


def test_constant_offset_is_recovered_with_word_timing():
    lines = lrc_lines()
    out, stats = align.align_lines(lines, sung(lines, offset_ms=1200), DUR)
    assert_lyrics_invariants(out, DUR)
    assert starts(out) == [s + 1200 for s in starts(lines)]
    assert all(ln["words"] for ln in out) and builder.timing_of(out) == "word"
    assert out[0]["words"][1]["start_ms"] == 11200 + 300
    assert stats.anchored_lines == len(lines) and stats.offset_ms == 1200 and stats.coverage == 1.0
    assert not any(ln.get("interpolated") for ln in out)


def test_missing_and_extra_words():
    lines = lrc_lines()
    segs = sung(lines, offset_ms=-500, drop={(0, 1), (3, 0)})  # Whisper perdeu "vez" e o 1º "Vai"
    # e ouviu palavras que não estão na letra (ad-lib) entre as linhas 1 e 2
    segs.insert(
        2,
        {
            "start": 16.0,
            "end": 16.6,
            "text": "",
            "words": [
                {"word": " yeah", "start": 16.0, "end": 16.2, "probability": 0.5},
                {"word": " uou", "start": 16.3, "end": 16.6, "probability": 0.5},
            ],
        },
    )
    out, stats = align.align_lines(lines, segs, DUR)
    assert_lyrics_invariants(out, DUR)
    assert [ln["text"] for ln in out] == [ln["text"] for ln in lines]  # texto da letra preservado
    assert starts(out)[1:3] == [13500, 17500]
    # "Vai" não casou: a linha continua ancorada pelas outras palavras e o "Vai" fica logo antes de "acontecer"
    assert abs(out[3]["start_ms"] - 21500) <= 400
    assert stats.anchored_lines == len(lines)


def test_repeated_chorus_matches_the_right_occurrence():
    lines = lrc_lines()
    out, _ = align.align_lines(lines, sung(lines, offset_ms=300), DUR)
    i1, i2 = [i for i, ln in enumerate(out) if ln["text"] == CHORUS]
    assert out[i1]["start_ms"] == 18300 and out[i2]["start_ms"] == 44300


def test_unsung_lines_are_interpolated_and_flagged():
    lines = lrc_lines()
    out, stats = align.align_lines(lines, sung(lines, offset_ms=800, skip={2}), DUR)
    assert_lyrics_invariants(out, DUR)
    assert out[2].get("interpolated") is True and not out[1].get("interpolated")
    # interpolada entre as vizinhas ancoradas (ambas com +800)
    assert out[2]["start_ms"] == 18800 and out[2]["words"]
    assert stats.interpolated_lines == 1 and stats.anchored_lines == len(lines) - 1


def test_instrumental_gap_and_drift():
    """Deriva linear (gravação levemente mais lenta): âncoras seguem a voz, não o LRC + constante."""
    lines = lrc_lines()
    segs = []
    for seg_lines in [lines]:
        for ln in seg_lines:
            t = ln["start_ms"] * 1.03 / 1000  # +3 %
            ws = []
            for tok in ln["text"].split():
                ws.append({"word": " " + tok, "start": t, "end": t + 0.25, "probability": 0.9})
                t += 0.3
            segs.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": "", "words": ws})
    out, stats = align.align_lines(lines, segs, DUR)
    for a, b in zip(out, lines, strict=True):
        assert abs(a["start_ms"] - round(b["start_ms"] * 1.03)) <= 1
    assert stats.drift_ms_per_min and stats.drift_ms_per_min > 1000
    # o intervalo instrumental (marcador vazio do LRC) continua sem linha
    assert out[3]["end_ms"] < out[4]["start_ms"] - 5000


def test_no_voice_falls_back_to_lrc_times():
    lines = lrc_lines()
    out, stats = align.align_lines(lines, [], DUR)
    assert starts(out) == starts(lines)
    assert all(ln.get("interpolated") for ln in out)
    assert stats.fallback_lines == len(lines) and stats.anchored_lines == 0


def test_lines_after_duration_are_dropped():
    lines = lrc_lines()
    out, _ = align.align_lines(lines, sung(lines, offset_ms=13000), 59000)
    assert all(ln["start_ms"] < 59000 for ln in out) and len(out) == len(lines) - 1
    assert_lyrics_invariants(out, 59000)


def test_detect_offset():
    lines = lrc_lines()
    r = align.detect_offset(lines, sung(lines, offset_ms=-700))
    assert r["suggested_offset_ms"] == -700 and r["confidence"] >= 0.9
    assert r["anchored_lines"] == len(lines)
    empty = align.detect_offset(lines, [])
    assert empty["suggested_offset_ms"] == 0 and empty["confidence"] == 0.0


def test_envelope_corrects_whisper_word_start():
    """1ª palavra do segmento "começando" 2 s antes (silêncio absorvido) é puxada para o ataque de voz."""
    hop = 10
    db = [-60] * 600
    for k in range(300, 380):  # voz de 3,0 s a 3,8 s
        db[k] = -3
    env = align.Envelope(hop, db)
    segs = [
        {
            "start": 1.0,
            "end": 3.8,
            "text": "",
            "words": [
                {"word": " Toda", "start": 1.0, "end": 3.4, "probability": 0.9},
                {"word": " vez", "start": 3.4, "end": 3.8, "probability": 0.9},
            ],
        }
    ]
    ww = align.whisper_words(segs, env)
    assert abs(ww[0].start_ms - 3000) <= 20
    d = align.envelope_from_samples([0.0] * 1600 + [0.5] * 1600, 16000)
    assert d["hop_ms"] == 10 and len(d["db"]) == 20 and d["db"][-1] == 0 and d["db"][0] < -90


def test_align_plain_text_without_times():
    """Letra oficial sem tempos (CR-001): texto oficial + tempos da voz; refrão repetido em ordem."""
    lines = lrc_lines()
    texts = [ln["text"] for ln in lines]
    out, stats = align.align_text(texts, sung(lines, offset_ms=2500, skip={5}), DUR)
    assert_lyrics_invariants(out, DUR)
    assert [ln["text"] for ln in out] == texts
    got = starts(out)
    for i, (a, b) in enumerate(zip(got, starts(lines), strict=True)):
        if i != 5:
            assert a == b + 2500
    assert out[5].get("interpolated") is True and got[4] < got[5] < got[6]
    assert stats.anchored_lines == len(lines) - 1
