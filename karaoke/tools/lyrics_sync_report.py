"""Relatório de sincronia das letras (US-10 / AC-08.6): LRCLIB × voz × alinhamento, por música.

Uso (sempre sobre uma **cópia** do diretório de dados — o banco é aberto somente leitura e nada é gravado nele):

    cp -r --reflink=auto data /tmp/karaoke-copia
    .venv/bin/python -m karaoke.tools.lyrics_sync_report --data /tmp/karaoke-copia [--device cuda] [--songs ID ...]

Para cada música com candidatas LRCLIB:
- transcrição Whisper (palavra a palavra) do vocal: usa `songs/<id>/transcript.json` da cópia se existir; senão
  `--transcripts DIR/<id>.whisper.json` (+ `<id>.env.json`); senão roda o worker de IA (GPU) e guarda o resultado em
  `--out` (padrão: `<data>/../sync-report/`), nunca dentro de `--data`;
- referência por linha = início Whisper da 1ª palavra da linha quando ela (e a 2ª, ou ≥ 3 palavras da linha) casam
  no alinhamento de sequência;
- desvio LRC − voz (médio |d|, mediana, máx), melhor offset constante, deriva (ms/min) e resíduo após reta;
- `aligned` (lyrics.align) × referência Whisper e × ataque de voz no envelope do vocal (referência independente),
  com o tempo do alinhamento.

Sinal: d > 0 = linha do LRC/alinhada **depois** da voz (letra atrasada).

Sem rede. Dono: backend-engineer (T-001, ciclo 20261004-1742).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from karaoke import system
from karaoke.config import PROJECT_DIR, Config
from karaoke.lyrics import align

MEAN_TARGET_MS = 150


def stats(xs: list[int]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "mean_abs": None, "median": None, "max_abs": None}
    a = [abs(x) for x in xs]
    return {
        "n": len(xs),
        "mean_abs": round(statistics.fmean(a)),
        "median": round(statistics.median(xs)),
        "max_abs": max(a),
    }


def fmt(s: dict[str, Any]) -> str:
    if not s["n"]:
        return "n=0"
    return f"n={s['n']:<3} médio|d|={s['mean_abs']:>5} ms  mediana={s['median']:>+6} ms  máx|d|={s['max_abs']:>5} ms"


def reference_starts(lines: list[dict[str, Any]], segments: list[dict[str, Any]], env: Any) -> dict[int, int]:
    ww = align.whisper_words(segments, env)
    lw = align.lyric_words(lines)
    pairs = align.needleman_wunsch(lw, ww)
    off = align._median_offset(lw, ww, pairs)
    if off is not None:
        pairs = align.needleman_wunsch(lw, ww, off)
    by: dict[int, dict[int, int]] = {}
    for i, j, _ in pairs:
        by.setdefault(lw[i].line, {})[lw[i].pos] = ww[j].start_ms
    return {li: m[0] for li, m in by.items() if 0 in m and (1 in m or len(m) >= 3)}


def nearest_rise(env: align.Envelope, t: int, win: int = 1000) -> int | None:
    best, lo, hi = None, t - win, t + win
    while hi > lo:
        r = env.last_rise(lo, hi)
        if r is None:
            break
        if best is None or abs(r - t) < abs(best - t):
            best = r
        hi = r - 10
    return best


def drift_and_residual(lines: list[dict[str, Any]], ref: dict[int, int]) -> tuple[float, list[int]]:
    xs = [lines[i]["start_ms"] for i in ref]
    ys = [ref[i] - lines[i]["start_ms"] for i in ref]  # quanto a voz está depois do LRC
    if len(xs) < 4:
        return 0.0, []
    b = align._slope(xs, ys)
    a = statistics.fmean(ys) - b * statistics.fmean(xs)
    return b * 60000, [round(y - (a + b * x)) for x, y in zip(xs, ys, strict=True)]


def transcribe(vocals: Path, out: Path, cfg: Config, device: str, language: str | None) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    task = out.with_suffix(".task.json")
    model = "large-v3-turbo" if device == "cuda" else "small"
    params = {"input": str(vocals), "model": model, "device": device, "language": language}
    task.write_text(json.dumps({"task": "transcribe", "params": params, "result_file": str(out)}))
    env = system.cuda_env()
    env.update(system.model_env(cfg))
    env = system.venv_bin_on_path(env)
    env["HF_HUB_OFFLINE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "karaoke.jobs.worker", str(task)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_DIR),
        check=False,
    )
    if not out.is_file():
        raise SystemExit(f"transcrição falhou ({vocals}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return json.loads(out.read_text())


def load_transcript(sid: str, data: Path, transcripts: Path | None, out: Path) -> dict[str, Any] | None:
    for p in (data / "songs" / sid / "transcript.json", out / f"{sid}.transcript.json"):
        if p.is_file():
            return json.loads(p.read_text())
    if transcripts is not None and (transcripts / f"{sid}.whisper.json").is_file():
        res = json.loads((transcripts / f"{sid}.whisper.json").read_text())
        envp = transcripts / f"{sid}.env.json"
        if envp.is_file() and not res.get("envelope"):
            res["envelope"] = json.loads(envp.read_text())
        return res
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="CÓPIA do diretório de dados")
    ap.add_argument("--songs", nargs="*", help="ids (padrão: todas com candidata LRCLIB)")
    ap.add_argument(
        "--transcripts", type=Path, help="pasta com <id>.whisper.json/<id>.env.json já calculados"
    )
    ap.add_argument("--out", type=Path, help="onde guardar transcrições novas e o JSON do relatório")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--no-transcribe", action="store_true", help="pula músicas sem transcrição pronta")
    ap.add_argument("--all-candidates", action="store_true", help="mede todas as candidatas LRCLIB")
    args = ap.parse_args(argv)

    data = args.data.resolve()
    if data == (PROJECT_DIR / "data").resolve():
        print("Use uma CÓPIA do diretório de dados (ex.: cp -r --reflink=auto data /tmp/karaoke-copia).")
        return 2
    out = (args.out or data.parent / "sync-report").resolve()
    cfg = Config(port=8810, data_dir=data, device=args.device, open_browser=False)
    conn = sqlite3.connect(f"file:{data / 'karaoke.db'}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    ids = args.songs or [
        r[0] for r in conn.execute("SELECT DISTINCT song_id FROM lyrics_candidates WHERE source='lrclib'")
    ]
    report: list[dict[str, Any]] = []
    for sid in ids:
        song = conn.execute("SELECT * FROM songs WHERE id=?", (sid,)).fetchone()
        if song is None:
            continue
        rows = conn.execute(
            "SELECT * FROM lyrics_candidates WHERE song_id=? AND source='lrclib' ORDER BY rowid", (sid,)
        ).fetchall()
        if not rows:
            continue
        res = load_transcript(sid, data, args.transcripts, out)
        if res is None:
            if args.no_transcribe:
                print(f"\n{sid}: sem transcrição (pulei)")
                continue
            t = time.monotonic()
            lang = None
            res = transcribe(
                data / "songs" / sid / "vocals.opus", out / f"{sid}.transcript.json", cfg, args.device, lang
            )
            print(f"(transcrição de {sid}: {time.monotonic() - t:.1f} s)")
        segs = res.get("segments") or []
        env = align.Envelope.from_dict(res.get("envelope"))
        sel_id = song["lyrics_candidate_id"]
        # a candidata medida: a selecionada (se for LRCLIB ou derivada de uma) ou a de duração mais próxima
        sel_row = (
            conn.execute("SELECT * FROM lyrics_candidates WHERE id=?", (sel_id,)).fetchone()
            if sel_id
            else None
        )
        main_id = (
            sel_row["derived_from"] if sel_row is not None and sel_row["source"] == "aligned" else sel_id
        )
        if main_id not in {r["id"] for r in rows}:
            main_id = min(
                rows, key=lambda r: abs(json.loads(r["meta_json"]).get("duration_diff_ms") or 10**9)
            )["id"]
        print(
            f"\n=== {song['title']} — {song['artist']} ({sid})  duração={song['duration_ms']} ms  "
            f"offset salvo={song['offset_ms']} ms"
        )
        for r in rows:
            if not args.all_candidates and r["id"] != main_id:
                continue
            meta = json.loads(r["meta_json"] or "{}")
            lines = json.loads(r["lyrics_json"])["lines"]
            ref = reference_starts(lines, segs, env)
            raw = [lines[i]["start_ms"] - t for i, t in ref.items()]
            med = round(statistics.median(raw)) if raw else 0
            drift, resid = drift_and_residual(lines, ref)
            t0 = time.monotonic()
            al, st = align.align_lines(lines, segs, song["duration_ms"], env)
            dt_ms = round((time.monotonic() - t0) * 1000)
            al_by_text = dict(enumerate(al)) if len(al) == len(lines) else {}
            d_al = [al_by_text[i]["start_ms"] - t for i, t in ref.items() if i in al_by_text]
            on_al: list[int] = []
            on_lrc: list[int] = []
            if env is not None:
                for i, ln in al_by_text.items():
                    o = nearest_rise(env, ln["start_ms"])
                    if o is not None:
                        on_al.append(ln["start_ms"] - o)
                    o2 = nearest_rise(env, lines[i]["start_ms"], 2500)
                    if o2 is not None:
                        on_lrc.append(lines[i]["start_ms"] - o2)
            entry = {
                "song_id": sid,
                "title": song["title"],
                "candidate_id": r["id"],
                "provider_id": meta.get("provider_id"),
                "album": meta.get("album_name"),
                "duration_diff_ms": meta.get("duration_diff_ms"),
                "lines": len(lines),
                "lrc_vs_voice": stats(raw),
                "best_constant_offset_ms": -med,
                "after_constant_offset": stats([x - med for x in raw]),
                "drift_ms_per_min": round(drift),
                "after_linear_fit": stats(resid),
                "aligned_vs_whisper": stats(d_al),
                "aligned_vs_voice_onset": stats(on_al),
                "lrc_vs_voice_onset": stats(on_lrc),
                "align_ms": dt_ms,
                "align": st.as_meta(),
            }
            report.append(entry)
            mark = "*" if r["id"] == main_id else " "
            print(
                f"{mark}{r['id']}  LRCLIB id={meta.get('provider_id')}  álbum={meta.get('album_name')!r}  "
                f"Δduração={meta.get('duration_diff_ms')} ms  linhas={len(lines)}"
            )
            print(f"   LRC − voz (Whisper) ........ {fmt(entry['lrc_vs_voice'])}")
            print(f"   após offset constante {-med:+6d} {fmt(entry['after_constant_offset'])}")
            print(f"   deriva {drift:+.0f} ms/min; após reta: {fmt(entry['after_linear_fit'])}")
            print(
                f"   ALIGNED − voz (Whisper) .... {fmt(entry['aligned_vs_whisper'])}  "
                f"[{dt_ms} ms; ancoradas {st.anchored_lines}/{st.lines}, interpoladas {st.interpolated_lines}]"
            )
            if env is not None:
                print(f"   ALIGNED − ataque de voz .... {fmt(entry['aligned_vs_voice_onset'])}")
                print(f"   LRC − ataque de voz ........ {fmt(entry['lrc_vs_voice_onset'])}")
            ok = entry["aligned_vs_whisper"]["mean_abs"]
            if ok is not None:
                print(
                    f"   meta AC-08.6 (≤ {MEAN_TARGET_MS} ms): {'OK' if ok <= MEAN_TARGET_MS else 'FALHOU'}"
                )
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nJSON completo: {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
