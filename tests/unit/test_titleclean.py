import pytest

from karaoke.lyrics.titleclean import clean_channel, clean_title, normalize, strip_noise


@pytest.mark.parametrize(
    ("title", "channel", "artist", "track"),
    [
        (
            "Queen – Bohemian Rhapsody (Official Video Remastered)",
            "Queen Official",
            "Queen",
            "Bohemian Rhapsody",
        ),
        ("Bohemian Rhapsody", "Queen - Topic", "Queen", "Bohemian Rhapsody"),
        (
            "Rick Astley - Never Gonna Give You Up (Official Music Video)",
            "Rick Astley",
            "Rick Astley",
            "Never Gonna Give You Up",
        ),
        (
            "Blue Öyster Cult - (Don't Fear) The Reaper (Official Audio)",
            "x",
            "Blue Öyster Cult",
            "(Don't Fear) The Reaper",
        ),
        ("Anitta - Envolver | Official Music Video", "Anitta", "Anitta", "Envolver"),
        ("Ed Sheeran - Shape of You ft. Someone [Official Lyric Video]", "x", "Ed Sheeran", "Shape of You"),
        ("Jay-Z - Empire State of Mind", "JayZVEVO", "Jay-Z", "Empire State of Mind"),
        ("Evidências (Ao Vivo)", "ChitãozinhoXororóVEVO", "ChitãozinhoXororó", "Evidências"),
        # QA L-02: separador dentro de parênteses não divide artista/faixa
        (
            "A pessoa certa (Música Autoral - Voz e Violão)",
            "João Manô",
            "João Manô",
            "A pessoa certa (Música Autoral - Voz e Violão)",
        ),
        ("Djavan - Oceano (Ao Vivo - Acústico)", "x", "Djavan", "Oceano"),
    ],
)
def test_clean_title(title, channel, artist, track):
    c = clean_title(title, channel)
    assert (c.artist, c.track) == (artist, track)


def test_strip_noise_keeps_meaningful_brackets():
    assert strip_noise("Song (Part 2) [HD]") == "Song (Part 2)"


def test_clean_channel():
    assert clean_channel("Queen - Topic") == "Queen"
    assert clean_channel("QueenVEVO") == "Queen"
    assert clean_channel(None) is None


def test_normalize():
    assert normalize("Évidências!  Ao-Vivo") == "evidencias ao vivo"
