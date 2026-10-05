from karaoke.lyrics.lrc_parser import parse_lrc
from tests.fixtures.pipeline import ENHANCED_LRC, SAMPLE_LRC


def test_parse_simple_with_tags_and_markers():
    p = parse_lrc(SAMPLE_LRC)
    assert p.tags["ti"] == "Bohemian Rhapsody" and p.tags["ar"] == "Queen"
    assert [(ln.start_ms, ln.text) for ln in p.lines] == [
        (12340, "Is this the real life?"),
        (15800, "Is this just fantasy?"),
        (45100, "Mama, just killed a man"),
    ]
    assert p.markers == [19000]
    assert p.first_untimed_line is None


def test_fraction_precision():
    p = parse_lrc("[01:02.5]a\n[01:02.345]b\n[1:02]c")
    assert [ln.start_ms for ln in p.lines] == [62000, 62345, 62500]


def test_multiple_time_tags_on_one_line():
    p = parse_lrc("[00:10.00][01:10.00]Refrão")
    assert [ln.start_ms for ln in p.lines] == [10000, 70000]


def test_offset_tag_applied():
    p = parse_lrc("[offset:+500]\n[00:10.00]a\n[00:00.20]b")
    assert [ln.start_ms for ln in p.lines] == [0, 9500]
    assert p.offset_ms == 500


def test_enhanced_words():
    p = parse_lrc(ENHANCED_LRC)
    first = p.lines[0]
    assert first.text == "Put a gun against his head"
    assert [w.text for w in first.words] == ["Put", "a", "gun", "against", "his", "head"]
    assert first.words[-1].end_ms == 53000


def test_untimed_lines_reported():
    p = parse_lrc("só texto\n\n[ar:x]\noutra")
    assert p.lines == [] and p.first_untimed_line == 1
