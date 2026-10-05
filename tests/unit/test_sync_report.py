"""Ferramenta de diagnóstico (karaoke.tools.lyrics_sync_report) com transcrição pronta — sem GPU/rede."""

from __future__ import annotations

import json

from karaoke.config import PROJECT_DIR
from karaoke.db import Database
from karaoke.tools import lyrics_sync_report as tool
from tests.fixtures.pipeline import SONG_ID, insert_ready_song, whisper_segments


def test_report_from_saved_transcripts(tmp_path, capsys):
    data = tmp_path / "copia"
    data.mkdir()
    db = Database(data / "karaoke.db")
    db.migrate()
    insert_ready_song(db)
    tr = tmp_path / "tr"
    tr.mkdir()
    (tr / f"{SONG_ID}.whisper.json").write_text(json.dumps({"segments": whisper_segments()}))
    out = tmp_path / "out"
    assert (
        tool.main(["--data", str(data), "--transcripts", str(tr), "--out", str(out), "--no-transcribe"]) == 0
    )
    text = capsys.readouterr().out
    assert "LRC − voz (Whisper)" in text and "ALIGNED − voz (Whisper)" in text
    rep = json.loads((out / "report.json").read_text())
    assert rep[0]["song_id"] == SONG_ID and rep[0]["lrc_vs_voice"]["n"] == 2
    assert rep[0]["lrc_vs_voice"]["median"] > 0  # LRC (12,34 s / 15,8 s) depois da voz (10,0 s / 11,75 s)
    assert rep[0]["aligned_vs_whisper"]["mean_abs"] == 0


def test_refuses_real_data_dir():
    assert tool.main(["--data", str(PROJECT_DIR / "data"), "--no-transcribe"]) == 2
