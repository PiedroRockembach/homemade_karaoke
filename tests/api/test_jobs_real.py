"""Teste real ponta a ponta (rede + GPU): fora do `make test`. Rode com
`.venv/bin/python -m pytest -m "gpu and network" tests/api/test_jobs_real.py -s`.
Usa um data dir temporário; baixa modelos para ele na 1ª vez (aponte KARAOKE_TEST_MODELS para reaproveitar)."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from karaoke import system
from karaoke.app import create_app
from karaoke.config import Config
from tests.conftest import BASE_URL, TEST_PORT

pytestmark = [pytest.mark.gpu, pytest.mark.network]

VIDEO = "dQw4w9WgXcQ"  # Rick Astley - Never Gonna Give You Up (3:33), letra sincronizada no LRCLIB


def test_full_pipeline(tmp_path, monkeypatch):
    config = Config(port=TEST_PORT, data_dir=tmp_path / "data", device="auto", open_browser=False)
    models_dir = os.environ.get("KARAOKE_TEST_MODELS")
    if models_dir:
        config.ensure_dirs()
        (config.data_dir / "models").rmdir()
        (config.data_dir / "models").symlink_to(models_dir)
    for k, v in system.model_env(config).items():
        monkeypatch.setenv(k, v)
    app = create_app(config)
    with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 5000)) as c:
        r = c.post("/api/jobs", json={"youtube_id": VIDEO})
        assert r.status_code == 201, r.text
        jid = r.json()["job"]["id"]
        t0 = time.monotonic()
        while True:
            job = c.get(f"/api/jobs/{jid}").json()
            if job["stage"] in ("done", "error", "canceled"):
                break
            assert time.monotonic() - t0 < 600
            time.sleep(1)
        assert job["stage"] == "done", job
        print(
            f"\npronto em {time.monotonic() - t0:.1f}s, letra={job['lyrics_source']}, device={job['device']}"
        )
        song = c.get(f"/api/songs/{VIDEO}").json()
        assert song["status"] == "ready" and song["media"]
        doc = c.get(f"/api/songs/{VIDEO}/lyrics").json()
        assert doc["lines"]
        assert c.get(song["media"]["instrumental"]).status_code == 200

        # v0.2 (T-001 ciclo 20261004-1742): modo padrão `aligned` → letra online com tempos da voz
        assert job["lyrics_source"] == "aligned", job
        assert doc["source"] == "aligned" and doc["timing"] == "word"
        cands = c.get(f"/api/songs/{VIDEO}/lyrics/candidates").json()["candidates"]
        al = next(x for x in cands if x["source"] == "aligned")
        print(f"alinhamento: {al['align']}")
        assert al["align"]["coverage"] >= 0.6 and al["derived_from"].startswith("lrclib:")
        # detectar offset sobre a própria alinhada (transcrição em cache): ~0
        det = c.post(f"/api/songs/{VIDEO}/lyrics/detect-offset").json()
        print(f"detect-offset na alinhada: {det}")
        assert abs(det["suggested_offset_ms"]) <= 150
        # "Extrair da voz" de novo mede o tempo do Whisper na GPU (NFR: ≤ 30 s para ~4 min)
        t1 = time.monotonic()
        tj = c.post(f"/api/songs/{VIDEO}/lyrics/transcribe").json()["job"]
        while (j := c.get(f"/api/jobs/{tj['id']}").json())["stage"] not in ("done", "error", "canceled"):
            assert time.monotonic() - t1 < 300
            time.sleep(0.5)
        dt = time.monotonic() - t1
        print(f"transcrição (Whisper) da música pronta: {dt:.1f}s em {j['device']}")
        assert j["stage"] == "done" and (j["device"] != "cuda" or dt <= 30)
        # "Sincronizar pela voz" volta a selecionar uma alinhada nova; as anteriores continuam listadas
        aj = c.post(f"/api/songs/{VIDEO}/lyrics/align").json()["job"]
        while (j := c.get(f"/api/jobs/{aj['id']}").json())["stage"] not in ("done", "error", "canceled"):
            time.sleep(0.5)
        assert j["stage"] == "done", j
        after = c.get(f"/api/songs/{VIDEO}/lyrics/candidates").json()
        assert after["selected_id"].startswith("aligned:") and len(after["candidates"]) > len(cands)
