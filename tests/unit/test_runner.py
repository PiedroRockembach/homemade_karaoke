"""JobRunner sem rede/GPU: YouTube, ffmpeg e worker de IA substituídos por fakes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from karaoke import youtube as yt
from karaoke.config import Config
from karaoke.db import Database, utcnow
from karaoke.jobs import models
from karaoke.jobs.runner import JobCanceled, JobRunner
from tests.fixtures.pipeline import (
    SONG_ID,
    assert_lyrics_invariants,
    insert_ready_song,
    lrclib_factory,
    lrclib_record,
    lrclib_transport,
    whisper_segments,
)


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, type_, data):
        self.events.append((type_, data))

    def types(self):
        return [t for t, _ in self.events]


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = Config(port=8765, data_dir=tmp_path / "data", device="cpu", open_browser=False)
    config.ensure_dirs()
    db = Database(config.db_path)
    db.migrate()
    bus = FakeBus()
    state = {
        "lrclib": lrclib_transport(search=[lrclib_record(77, 359.5)]),
        "download_error": None,
        "worker": [],
    }
    runner = JobRunner(
        config, db, bus, lrclib_factory=lambda: lrclib_factory(state["lrclib"])(), autostart=False
    )
    runner._device = ("cpu", "cpu")
    monkeypatch.setattr(models, "is_downloaded", lambda cfg, mid: True)

    def fake_download(song_id, dest, progress=None, cancel=None):
        if state["download_error"]:
            raise state["download_error"]
        dest.mkdir(parents=True, exist_ok=True)
        progress and progress(0.5)
        f = dest / "source.webm"
        f.write_bytes(b"audio")
        return f

    monkeypatch.setattr(yt, "download_audio", fake_download)
    monkeypatch.setattr(yt, "fetch_thumbnail", lambda sid, dest, large=True: dest.write_bytes(b"jpg") or True)

    def fake_ffmpeg(ctx, args, code="ffmpeg_failed"):
        Path(args[-1]).write_bytes(b"wav")

    def fake_parallel(ctx, jobs):
        for a in jobs:
            Path(a[-1]).write_bytes(b"opus" * 10)

    def fake_worker(ctx, task, params, on_progress, workdir, result_file=None):
        state["worker"].append((task, params))
        hook = state.get("worker_hook")
        if hook:
            hook(ctx)
        on_progress(0.5)
        ctx.check()
        if task == "separate":
            out = Path(params["out_dir"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "vocals.wav").write_bytes(b"v")
            (out / "instrumental.wav").write_bytes(b"i")
            return {"device": "cpu", "samplerate": 44100, "samples": 44100 * 359}
        return {"segments": whisper_segments(), "language": "en"}

    monkeypatch.setattr(runner, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(runner, "_ffmpeg_parallel", fake_parallel)
    monkeypatch.setattr(runner, "_run_worker", fake_worker)
    return runner, config, db, bus, state


def add_job(runner, db, song_id=SONG_ID, actor="host", options=None):
    now = utcnow()
    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO songs(id, title, artist, original_title, channel, duration_ms, status, added_by, "
            "created_at, updated_at) VALUES (?, 'Bohemian Rhapsody', 'Queen', 'Queen – Bohemian Rhapsody', 'Queen', "
            "359000, 'queued', ?, ?, ?)",
            (song_id, actor, now, now),
        )
        return runner.insert_job(conn, "process", song_id, actor, options)


def row(db, sql, *args):
    conn = db.connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def test_process_with_lrclib(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db, options={"lyrics_mode": "online"})
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "done" and job["progress"] == 1.0 and job["lyrics_source"] == "lrclib"
    assert job["attempts"] == 1 and job["queue_position"] is None and job["device"] == "cpu"
    song = row(db, "SELECT * FROM songs WHERE id=?", SONG_ID)
    assert song["status"] == "ready" and song["media_version"] == 1 and song["duration_ms"] == 359000
    assert song["lyrics_candidate_id"].startswith("lrclib:")
    files = sorted(p.name for p in (config.songs_dir / SONG_ID).iterdir())
    assert files == ["instrumental.opus", "thumb.jpg", "vocals.opus"]
    assert not (config.tmp_dir / jid).exists()
    stages = [d["stage"] for t, d in bus.events if t == "job.updated"]
    for s in ("downloading", "separating", "lyrics", "finalizing", "done"):
        assert s in stages
    assert "model_download" not in stages
    assert ("library.updated", "added") in [
        (t, d.get("action")) for t, d in bus.events if t == "library.updated"
    ]
    progresses = [d["progress"] for t, d in bus.events if t == "job.updated"]
    assert progresses == sorted(progresses)
    doc = json.loads(
        row(db, "SELECT lyrics_json FROM lyrics_candidates WHERE id=?", song["lyrics_candidate_id"])[0]
    )
    assert_lyrics_invariants(doc["lines"], 359000)
    assert [t for t, _ in state["worker"]] == ["separate"]


def test_lrclib_unavailable_falls_back_to_whisper(env):
    runner, config, db, bus, state = env
    state["lrclib"] = lrclib_transport(fail=True)
    jid = add_job(runner, db)
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "done" and job["lyrics_source"] == "whisper"
    assert [t for t, _ in state["worker"]] == ["separate", "transcribe"]
    assert state["worker"][1][1]["input"].endswith("vocals.wav")
    song = row(db, "SELECT lyrics_candidate_id FROM songs WHERE id=?", SONG_ID)
    cand = row(db, "SELECT * FROM lyrics_candidates WHERE id=?", song[0])
    assert cand["source"] == "whisper" and cand["timing"] == "word"
    assert_lyrics_invariants(json.loads(cand["lyrics_json"])["lines"], 359000)


def test_lrclib_far_duration_uses_whisper_but_keeps_candidates(env):
    runner, config, db, bus, state = env
    state["lrclib"] = lrclib_transport(search=[lrclib_record(5, 300)])
    jid = add_job(runner, db)
    runner.run_pending()
    # CR-001: mesma faixa com outra duração ainda é texto oficial; a voz confirma (cobertura) e dá os tempos
    assert runner.job_dict(jid)["lyrics_source"] == "aligned"
    assert sorted(c["source"] for c in cands_of(db)) == ["aligned", "lrclib"]


def test_download_error_marks_error_and_leaves_no_files(env):
    runner, config, db, bus, state = env
    state["download_error"] = yt.YoutubeError("video_unavailable", "Indisponível.")
    jid = add_job(runner, db)
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "error" and job["error"] == {
        "code": "video_unavailable",
        "message": "Indisponível.",
    }
    assert row(db, "SELECT status, error_code FROM songs WHERE id=?", SONG_ID)[:] == (
        "error",
        "video_unavailable",
    )
    assert not (config.songs_dir / SONG_ID).exists()
    assert not (config.tmp_dir / jid).exists()


def test_cancel_running_job(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)
    state["worker_hook"] = lambda ctx: runner.cancel(ctx.job_id)
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "canceled" and job["error"] is None
    assert row(db, "SELECT status, error_code FROM songs WHERE id=?", SONG_ID)[:] == ("error", "canceled")
    assert not (config.songs_dir / SONG_ID).exists() and not (config.tmp_dir / jid).exists()


def test_song_deleted_during_processing_is_tolerated(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)

    def delete_song(ctx):
        runner.cancel(ctx.job_id)  # o DELETE de música chama cancel() e a cascata apaga o job
        with db.tx() as conn:
            conn.execute("DELETE FROM songs WHERE id=?", (SONG_ID,))

    state["worker_hook"] = delete_song
    runner.run_pending()
    assert runner.job_dict(jid) is None
    assert not (config.songs_dir / SONG_ID).exists()


def test_cancel_or_remove_and_retry(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)
    assert runner.cancel_or_remove(jid) is True
    assert runner.job_dict(jid)["stage"] == "canceled"
    job = runner.retry(jid)
    assert job["stage"] == "queued" and job["queue_position"] == 1 and job["attempts"] == 0
    assert row(db, "SELECT status FROM songs WHERE id=?", SONG_ID)[0] == "queued"
    with pytest.raises(ValueError):
        runner.retry(jid)  # queued não é retentável
    runner.run_pending()
    assert runner.job_dict(jid)["stage"] == "done"
    assert runner.cancel_or_remove(jid) is True  # terminal -> removido
    assert runner.job_dict(jid) is None and ("job.removed", {"id": jid}) in bus.events
    assert runner.cancel_or_remove("j_999") is False
    assert runner.retry("j_999") is None


def test_queue_positions_and_listing(env):
    runner, config, db, bus, state = env
    a = add_job(runner, db, SONG_ID)
    b = add_job(runner, db, "dQw4w9WgXcQ")
    jobs = runner.list_jobs("active")
    assert [(j["id"], j["queue_position"]) for j in jobs] == [(a, 1), (b, 2)]
    assert runner.active_jobs() == jobs


def test_recover_after_restart(env):
    runner, config, db, bus, state = env
    a = add_job(runner, db, SONG_ID)
    b = add_job(runner, db, "dQw4w9WgXcQ")
    with db.tx() as conn:
        conn.execute("UPDATE jobs SET stage='separating', attempts=1 WHERE id=?", (a,))
        conn.execute("UPDATE jobs SET stage='downloading', attempts=3 WHERE id=?", (b,))
    (config.tmp_dir / a).mkdir()
    (config.tmp_dir / a / "source.wav").write_bytes(b"wav")
    (config.tmp_dir / "j_old").mkdir()
    runner.recover()
    assert runner.job_dict(a)["stage"] == "queued"
    jb = runner.job_dict(b)
    assert jb["stage"] == "error" and jb["error"]["code"] == "interrupted"
    assert not (config.tmp_dir / "j_old").exists()  # tmp órfão removido
    calls = []
    yt.download_audio, orig = (lambda *a, **k: calls.append(1)), yt.download_audio
    try:
        runner.run_pending()
    finally:
        yt.download_audio = orig
    assert calls == []  # source.wav existente: download pulado
    assert runner.job_dict(a)["stage"] == "done" and runner.job_dict(a)["attempts"] == 2


def test_stop_during_job_keeps_it_for_resume(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)

    def stop(ctx):
        runner._stop.set()
        raise JobCanceled()

    state["worker_hook"] = stop
    row_ = row(db, "SELECT * FROM jobs WHERE id=?", jid)
    runner._run(row_)
    assert runner.job_dict(jid)["stage"] == "separating"
    assert (config.tmp_dir / jid / "source.wav").exists()


def test_transcribe_job_selects_whisper(env):
    runner, config, db, bus, state = env
    insert_ready_song(db)
    (config.songs_dir / SONG_ID).mkdir(parents=True)
    (config.songs_dir / SONG_ID / "vocals.opus").write_bytes(b"x")
    with db.tx() as conn:
        jid = runner.insert_job(conn, "transcribe", SONG_ID, "host", {"language": "pt"})
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "done" and job["lyrics_source"] == "whisper"
    assert state["worker"][0][1]["language"] == "pt"
    sel = row(db, "SELECT lyrics_candidate_id, status FROM songs WHERE id=?", SONG_ID)
    assert sel[0].startswith("whisper:") and sel[1] == "ready"
    lu = [d for t, d in bus.events if t == "lyrics.updated"]
    assert lu == [{"song_id": SONG_ID, "candidate_id": sel[0], "source": "whisper"}]


def test_model_download_stage_when_missing(env, monkeypatch):
    runner, config, db, bus, state = env
    downloaded = set()
    monkeypatch.setattr(models, "is_downloaded", lambda cfg, mid: mid in downloaded)

    def fake_dl(cfg, mid, on_progress=None, cancel=None):
        on_progress and on_progress(0.5)
        downloaded.add(mid)

    monkeypatch.setattr(models, "download", fake_dl)
    jid = add_job(runner, db)
    runner.run_pending()
    stages = [d["stage"] for t, d in bus.events if t == "job.updated" and d["id"] == jid]
    assert stages.index("model_download") < stages.index("downloading")
    assert "system.updated" in bus.types()


def test_model_download_failure(env, monkeypatch):
    runner, config, db, bus, state = env
    monkeypatch.setattr(models, "is_downloaded", lambda cfg, mid: False)

    def fail(cfg, mid, on_progress=None, cancel=None):
        raise models.ModelDownloadError("sem internet")

    monkeypatch.setattr(models, "download", fail)
    jid = add_job(runner, db)
    runner.run_pending()
    assert runner.job_dict(jid)["error"]["code"] == "model_download_failed"


def test_single_runner_per_data_dir(env):
    runner, config, db, bus, state = env
    other = JobRunner(config, db, bus, autostart=True)
    assert runner._acquire_lock() is True
    assert other._acquire_lock() is False
    other.start()  # segunda instância não processa jobs
    assert other._thread is None
    runner.stop()  # libera o lock
    assert other._acquire_lock() is True
    other.stop()


# ---- review M1 + lows de corrida/vazamento no cancelamento -----------------------------------------------------
def test_cancel_returns_immediately_and_kills_later(env, monkeypatch):
    import subprocess
    import sys
    import time

    from karaoke.jobs import runner as runner_mod
    from karaoke.jobs.runner import _Ctx

    runner, config, db, bus, state = env
    monkeypatch.setattr(runner_mod, "KILL_GRACE_S", 0.3)
    jid = add_job(runner, db)
    code = (
        "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "sys.stdout.write('ok'); sys.stdout.flush(); time.sleep(30)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    try:
        assert proc.stdout.read(2) == b"ok"
        ctx = _Ctx(jid, "process", SONG_ID)
        ctx.proc = proc
        runner._current = ctx
        t0 = time.monotonic()
        runner.cancel(jid)
        assert time.monotonic() - t0 < 0.2
        assert ctx.cancel.is_set() and jid in runner._canceled_ids
        proc.wait(timeout=5)  # SIGKILL depois do prazo, pela thread de apoio
    finally:
        runner._current = None
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cancel_of_job_not_running_is_not_tracked(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)
    runner.cancel(jid)  # job só `queued` (ou já apagado pela cascata): nada a sinalizar
    assert runner._canceled_ids == set()


def test_run_skips_job_canceled_after_being_picked(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)
    picked = runner._next_queued()
    assert runner.cancel_or_remove(jid) is True  # DELETE entre a leitura e o início
    runner._run(picked)
    job = runner.job_dict(jid)
    assert job["stage"] == "canceled" and job["attempts"] == 0
    assert state["worker"] == []


def test_run_skips_job_deleted_after_being_picked(env):
    runner, config, db, bus, state = env
    add_job(runner, db)
    picked = runner._next_queued()
    with db.tx() as conn:
        conn.execute("DELETE FROM songs WHERE id=?", (SONG_ID,))  # cascata apaga o job
    runner._run(picked)
    assert state["worker"] == [] and runner._canceled_ids == set()


# ---- v0.2: modos de letra, sincronizar pela voz, detectar offset (T-001 ciclo 20261004-1742) ----------------
def cands_of(db, song_id=SONG_ID):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT * FROM lyrics_candidates WHERE song_id=? ORDER BY rowid", (song_id,)
        ).fetchall()
    finally:
        conn.close()


def test_process_default_mode_aligns_online_lyrics(env):
    runner, config, db, bus, state = env
    jid = add_job(runner, db)  # sem override: setting padrão `aligned`
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "done" and job["lyrics_source"] == "aligned"
    assert [t for t, _ in state["worker"]] == ["separate", "transcribe"]
    by_src = {c["source"]: c for c in cands_of(db)}
    assert set(by_src) == {"lrclib", "aligned"}  # CR-001: texto do Whisper não entra quando há letra oficial
    al = by_src["aligned"]
    assert al["derived_from"] == by_src["lrclib"]["id"] and al["timing"] == "word"
    song = row(db, "SELECT lyrics_candidate_id FROM songs WHERE id=?", SONG_ID)
    assert song[0] == al["id"]
    doc = json.loads(al["lyrics_json"])
    assert_lyrics_invariants(doc["lines"], 359000)
    # as 2 primeiras linhas seguem a voz (10,0 s / 11,75 s); "Mama..." não foi cantada → interpolada e marcada
    assert [ln["start_ms"] for ln in doc["lines"][:2]] == [10000, 11750]
    assert doc["lines"][2].get("interpolated") is True and "interpolated" not in doc["lines"][0]
    meta = json.loads(al["meta_json"])
    assert meta["align"]["anchored_lines"] == 2 and meta["derived_from"] == by_src["lrclib"]["id"]
    cache = json.loads((config.songs_dir / SONG_ID / "transcript.json").read_text())
    assert cache["version"] == 1 and cache["segments"]


def test_process_transcribe_mode_is_legacy_alias_of_aligned(env):
    runner, config, db, bus, state = env
    with db.tx() as conn:
        conn.execute("INSERT INTO settings(key, value_json) VALUES ('lyrics_mode', '\"transcribe\"')")
    jid = add_job(runner, db)
    runner.run_pending()
    assert runner.job_dict(jid)["lyrics_source"] == "aligned"
    assert sorted(c["source"] for c in cands_of(db)) == ["aligned", "lrclib"]
    # override do job vence a configuração
    assert runner.lyrics_mode({"lyrics_mode": "online"}) == "online"
    assert runner.lyrics_mode({"lyrics_mode": "xyz"}) == "aligned"


def test_aligned_mode_without_online_lyrics_falls_back_to_whisper(env):
    runner, config, db, bus, state = env
    state["lrclib"] = lrclib_transport(search=[])
    jid = add_job(runner, db, options={"lyrics_mode": "aligned"})
    runner.run_pending()
    assert runner.job_dict(jid)["lyrics_source"] == "whisper"


def _ready_with_transcript(config, db, segments=None):
    insert_ready_song(db)
    d = config.songs_dir / SONG_ID
    d.mkdir(parents=True)
    (d / "vocals.opus").write_bytes(b"x")
    (d / "transcript.json").write_text(
        json.dumps({"version": 1, "segments": segments or whisper_segments(), "language": "en"})
    )


def test_align_job_uses_cached_transcript_and_keeps_previous(env):
    runner, config, db, bus, state = env
    _ready_with_transcript(config, db)
    base = cands_of(db)[0]["id"]
    with db.tx() as conn:
        jid = runner.insert_job(conn, "align", SONG_ID, "host", {"candidate_id": base})
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "done" and job["kind"] == "align" and job["lyrics_source"] == "aligned"
    assert state["worker"] == []  # transcrição veio do cache
    assert "lyrics" in [d["stage"] for t, d in bus.events if t == "job.updated"]
    cs = cands_of(db)
    assert [c["source"] for c in cs] == ["lrclib", "aligned"] and cs[1]["derived_from"] == base
    assert row(db, "SELECT lyrics_candidate_id FROM songs WHERE id=?", SONG_ID)[0] == cs[1]["id"]
    assert (
        "lyrics.updated",
        {"song_id": SONG_ID, "candidate_id": cs[1]["id"], "source": "aligned"},
    ) in bus.events


def test_align_job_without_cache_transcribes_and_saves_cache(env):
    runner, config, db, bus, state = env
    insert_ready_song(db)
    (config.songs_dir / SONG_ID).mkdir(parents=True)
    (config.songs_dir / SONG_ID / "vocals.opus").write_bytes(b"x")
    with db.tx() as conn:
        jid = runner.insert_job(conn, "align", SONG_ID, "host", {"candidate_id": cands_of(db)[0]["id"]})
    runner.run_pending()
    assert runner.job_dict(jid)["stage"] == "done"
    assert [t for t, _ in state["worker"]] == ["transcribe"]
    assert (config.songs_dir / SONG_ID / "transcript.json").is_file()


def test_align_job_fails_when_voice_does_not_sing_the_text(env):
    runner, config, db, bus, state = env
    other = [
        {
            "start": 5.0,
            "end": 6.0,
            "text": "",
            "words": [
                {"word": " banana", "start": 5.0, "end": 5.5, "probability": 0.9},
                {"word": " laranja", "start": 5.5, "end": 6.0, "probability": 0.9},
            ],
        }
    ]
    _ready_with_transcript(config, db, other)
    with db.tx() as conn:
        jid = runner.insert_job(conn, "align", SONG_ID, "host", {"candidate_id": cands_of(db)[0]["id"]})
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "error" and job["error"]["code"] == "align_failed"
    assert len(cands_of(db)) == 1


def test_analyze_job_publishes_offset_suggestion(env):
    runner, config, db, bus, state = env
    insert_ready_song(db)
    (config.songs_dir / SONG_ID).mkdir(parents=True)
    (config.songs_dir / SONG_ID / "vocals.opus").write_bytes(b"x")
    base = cands_of(db)[0]["id"]
    with db.tx() as conn:
        jid = runner.insert_job(conn, "analyze", SONG_ID, "host", {"candidate_id": base})
    runner.run_pending()
    assert runner.job_dict(jid)["stage"] == "done"
    ev = [d for t, d in bus.events if t == "lyrics.offset_detected"]
    assert len(ev) == 1 and ev[0]["song_id"] == SONG_ID and ev[0]["candidate_id"] == base
    assert ev[0]["suggested_offset_ms"] < 0 and 0 <= ev[0]["confidence"] <= 1
    assert (config.songs_dir / SONG_ID / "transcript.json").is_file()


# ---- CR-001: letra oficial sempre, Whisper só para tempos (T-006) ----------------------------------------------
PLAIN = "Is this the real life?\nIs this just fantasy?\nCaught in a landslide, no escape from reality"


def plain_rec(id_=88, dur=359.5):
    return dict(lrclib_record(id_, dur), syncedLyrics=None, plainLyrics=PLAIN)


def test_process_aligns_plain_only_official_text(env):
    runner, config, db, bus, state = env
    state["lrclib"] = lrclib_transport(search=[plain_rec()])
    jid = add_job(
        runner, db, options={"lyrics_mode": "online"}
    )  # online sem LRC com tempos → igual a aligned
    runner.run_pending()
    assert runner.job_dict(jid)["lyrics_source"] == "aligned"
    by = {c["source"]: c for c in cands_of(db)}
    assert set(by) == {"online_plain", "aligned"}
    plain = json.loads(by["online_plain"]["lyrics_json"])
    assert plain["timing"] == "none" and plain["lines"] == [] and len(plain["text_lines"]) == 3
    al = json.loads(by["aligned"]["lyrics_json"])
    assert [ln["text"] for ln in al["lines"]] == PLAIN.split("\n")  # texto oficial, não o do Whisper
    assert al["lines"][0]["start_ms"] == 10000 and by["aligned"]["derived_from"] == by["online_plain"]["id"]
    assert_lyrics_invariants(al["lines"], 359000)


def test_process_without_official_lyrics_uses_whisper_text(env):
    runner, config, db, bus, state = env
    state["lrclib"] = lrclib_transport(fail=True)
    jid = add_job(runner, db)
    runner.run_pending()
    assert runner.job_dict(jid)["lyrics_source"] == "whisper"
    assert [c["source"] for c in cands_of(db)] == ["whisper"]
    assert any(t == "toast" and d["level"] == "warning" for t, d in bus.events)


def test_process_keeps_synced_official_when_voice_does_not_confirm(env):
    runner, config, db, bus, state = env
    other = [
        {
            "start": 5.0,
            "end": 6.0,
            "text": "",
            "words": [
                {"word": " banana", "start": 5.0, "end": 5.5, "probability": 0.9},
                {"word": " laranja", "start": 5.5, "end": 6.0, "probability": 0.9},
            ],
        }
    ]
    orig = runner._run_worker

    def worker(ctx, task, params, on_progress, workdir, result_file=None):
        r = orig(ctx, task, params, on_progress, workdir, result_file)
        return {"segments": other, "language": "pt"} if task == "transcribe" else r

    runner._run_worker = worker
    jid = add_job(runner, db)
    runner.run_pending()
    assert runner.job_dict(jid)["lyrics_source"] == "lrclib"
    assert [c["source"] for c in cands_of(db)] == ["lrclib"]


def test_align_job_with_search_finds_official_text(env):
    runner, config, db, bus, state = env
    insert_ready_song(db, lrc=None)
    d = config.songs_dir / SONG_ID
    d.mkdir(parents=True)
    (d / "vocals.opus").write_bytes(b"x")
    state["lrclib"] = lrclib_transport(search=[plain_rec(dur=359)])
    with db.tx() as conn:
        jid = runner.insert_job(conn, "align", SONG_ID, "host", {"candidate_id": None, "search": True})
    runner.run_pending()
    job = runner.job_dict(jid)
    assert job["stage"] == "done" and job["lyrics_source"] == "aligned"
    cs = cands_of(db)
    assert [c["source"] for c in cs] == ["online_plain", "aligned"] and cs[1]["derived_from"] == cs[0]["id"]
    assert row(db, "SELECT lyrics_candidate_id FROM songs WHERE id=?", SONG_ID)[0] == cs[1]["id"]


def test_align_job_with_search_and_nothing_found_falls_back_to_whisper(env):
    runner, config, db, bus, state = env
    insert_ready_song(db, lrc=None)
    d = config.songs_dir / SONG_ID
    d.mkdir(parents=True)
    (d / "vocals.opus").write_bytes(b"x")
    state["lrclib"] = lrclib_transport(search=[])
    with db.tx() as conn:
        jid = runner.insert_job(conn, "align", SONG_ID, "host", {"candidate_id": None, "search": True})
    runner.run_pending()
    assert runner.job_dict(jid)["lyrics_source"] == "whisper"
    assert [c["source"] for c in cands_of(db)] == ["whisper"]
