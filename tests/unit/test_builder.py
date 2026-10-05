from karaoke.lyrics import builder
from tests.fixtures.pipeline import ENHANCED_LRC, SAMPLE_LRC, assert_lyrics_invariants, whisper_segments


def test_lrc_contract_example():
    lines, _ = builder.from_lrc(SAMPLE_LRC, 359000)
    assert_lyrics_invariants(lines, 359000)
    assert [ln["start_ms"] for ln in lines] == [12340, 15800, 45100]
    # exemplo do contrato §6: a linha 1 termina em min(19000, 15800 + 2400) = 18200
    assert lines[1]["end_ms"] == 18200
    assert lines[0]["end_ms"] == 15190  # 5 palavras -> 2850 ms
    assert builder.timing_of(lines) == "line"


def test_last_line_limited_by_duration():
    lines, _ = builder.from_lrc("[00:10.00]um dois tres quatro cinco seis", 11000)
    assert lines[0]["end_ms"] == 11000


def test_enhanced_lrc_is_word_timing():
    lines, _ = builder.from_lrc(ENHANCED_LRC, 300000)
    assert_lyrics_invariants(lines, 300000)
    assert builder.timing_of(lines) == "word"
    assert lines[0]["end_ms"] == 53000
    # "gun against" compartilham a tag 50.60 -> distribuídas até 52.00
    words = {w["text"]: w for w in lines[0]["words"]}
    assert words["gun"]["start_ms"] == 50600 and words["against"]["start_ms"] == 51300


def test_same_timestamp_lines_merged():
    lines, _ = builder.from_lrc("[00:10.00]Hello\n[00:10.00]Olá\n[00:12.00]Next")
    assert_lyrics_invariants(lines)
    assert lines[0]["text"] == "Hello Olá" and len(lines) == 2


def test_whisper_lines_split_and_hallucinations_dropped():
    lines = builder.from_whisper(whisper_segments(), 60000)
    assert_lyrics_invariants(lines, 60000)
    assert builder.timing_of(lines) == "word"
    texts = [ln["text"] for ln in lines]
    assert not any("Amara" in t for t in texts)
    assert all(len(t) <= builder.MAX_LINE_CHARS for t in texts)
    assert texts[0].startswith("Is this the real life?")
    # pausa de 1.5 s depois de "fantasy?" força quebra
    assert any(t.endswith("fantasy?") for t in texts)


def test_whisper_repetition_loop_dropped():
    seg = {"text": "la la la", "no_speech_prob": 0.0, "avg_logprob": -0.1, "compression_ratio": 1.0}
    segs = []
    for k in range(4):
        s = dict(
            seg,
            start=k * 2.0,
            end=k * 2.0 + 1,
            words=[
                {"word": " la", "start": k * 2.0 + i * 0.3, "end": k * 2.0 + i * 0.3 + 0.2} for i in range(3)
            ],
        )
        segs.append(s)
    lines = builder.from_whisper(segs)
    assert len(lines) == 2


def test_document_shape_and_empty():
    lines, _ = builder.from_lrc(SAMPLE_LRC, 359000)
    doc = builder.make_document(
        song_id="fJ9rUzIMcZQ",
        candidate_id="lrclib:1",
        source="lrclib",
        lines=lines,
        duration_ms=359000,
        meta={"provider_id": 7},
    )
    assert doc["version"] == 1 and doc["timing"] == "line" and doc["meta"]["provider_id"] == 7
    empty = builder.empty_document("fJ9rUzIMcZQ", 1000)
    assert empty["source"] is None and empty["timing"] == "none" and empty["lines"] == []


def test_fix_words_never_overlaps():
    words = [
        {"text": "a", "start_ms": 100, "end_ms": 500},
        {"text": "b", "start_ms": 300, "end_ms": 200},
        {"text": "c", "start_ms": 300, "end_ms": 900},
    ]
    out = builder._fix_words(words, 100, 800)
    assert out[0]["start_ms"] == 100 and out[-1]["end_ms"] == 800
    for a, b in zip(out, out[1:], strict=False):
        assert a["start_ms"] < a["end_ms"] <= b["start_ms"] < b["end_ms"]
