"""`make doctor`: diagnóstico da instalação sem subir o servidor.

Verifica torch + CUDA (tensor na GPU), CTranslate2 na CUDA em processo **limpo** (sem torch importado, só com o
LD_LIBRARY_PATH do venv — exatamente como o worker de IA roda), carga real do faster-whisper `tiny` na GPU,
ffmpeg, yt-dlp + Deno e modelos baixados. Sai com código 0 mesmo em fallback CPU (o app funciona), 1 se faltar algo
obrigatório (ffmpeg, yt-dlp, deno).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from karaoke import system
from karaoke.config import ConfigError, load_config

_CT2_PROBE = r"""
import ctypes, json, sys, time
r = {"libcublas": None, "cuda_devices": 0, "whisper_tiny": None, "error": None}
try:
    ctypes.CDLL("libcublas.so.12"); r["libcublas"] = "ok"
except OSError as e:
    r["libcublas"] = f"falhou: {e}"
try:
    import ctranslate2
    r["ct2_version"] = ctranslate2.__version__
    r["cuda_devices"] = ctranslate2.get_cuda_device_count()
    if "--whisper" in sys.argv and r["cuda_devices"] > 0:
        import numpy as np
        from faster_whisper import WhisperModel
        t = time.time()
        m = WhisperModel("tiny", device="cuda", compute_type="float16")
        audio = (0.1 * np.sin(2 * np.pi * 440 * np.arange(16000 * 2) / 16000)).astype("float32")
        segs, info = m.transcribe(audio, word_timestamps=True)
        list(segs)
        r["whisper_tiny"] = f"ok ({time.time() - t:.1f}s, idioma detectado={info.language})"
    assert "torch" not in sys.modules, "torch foi importado (teste inválido)"
except Exception as e:
    r["error"] = f"{type(e).__name__}: {e}"
print(json.dumps(r))
"""


def _ct2_probe(with_ld_path: bool, whisper: bool, base_env: dict[str, str]) -> dict[str, Any]:
    env = system.cuda_env(base_env) if with_ld_path else dict(base_env)
    if not with_ld_path:
        env.pop("LD_LIBRARY_PATH", None)
    args = [sys.executable, "-c", _CT2_PROBE] + (["--whisper"] if whisper else [])
    out = subprocess.run(args, capture_output=True, text=True, timeout=600, env=env, check=False)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": (out.stderr or out.stdout).strip()[-500:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="karaoke.doctor")
    ap.add_argument(
        "--json", action="store_true", help="saída JSON (mesmo formato de GET /api/system + checks)"
    )
    ap.add_argument("--no-whisper", action="store_true", help="não baixar/carregar o faster-whisper tiny")
    args = ap.parse_args(argv)
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"ERRO de configuração: {exc}", file=sys.stderr)
        return 1
    config.ensure_dirs()
    base_env = {**os.environ, **system.model_env(config)}
    os.environ.update(system.model_env(config))

    info = system.collect(config)
    ct2_clean = _ct2_probe(with_ld_path=True, whisper=not args.no_whisper, base_env=base_env)
    ct2_bare = _ct2_probe(with_ld_path=False, whisper=False, base_env=base_env)
    gpu = system.probe_gpu()
    report = {
        "system": info,
        "torch_probe": gpu,
        "ctranslate2": {"with_venv_ld_path": ct2_clean, "without_ld_path": ct2_bare},
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print(report)
    t = info["tools"]
    return 0 if (t["ffmpeg"]["ok"] and t["yt_dlp"]["ok"] and t["js_runtime"]["ok"]) else 1


def _ok(flag: bool) -> str:
    return "OK  " if flag else "FALHA"


def _print(r: dict[str, Any]) -> None:
    s, g, c = r["system"], r["torch_probe"], r["ctranslate2"]
    cw, cb = c["with_venv_ld_path"], c["without_ld_path"]
    print("== Karaokê — doctor ==")
    print(f"python            {sys.version.split()[0]}  ({sys.executable})")
    print(f"torch             {g.get('torch')}  (CUDA {g.get('cuda')})")
    print(
        f"[{_ok(bool(g.get('available')))}] torch.cuda      {g.get('name')}  cc {g.get('compute_capability')}  "
        f"{g.get('vram_total_mb')} MB  tensor na GPU: {'ok' if g.get('available') else g.get('error')}"
    )
    print(
        f"[{_ok(cw.get('cuda_devices', 0) > 0)}] CTranslate2     {cw.get('ct2_version')}  "
        f"cuda_devices={cw.get('cuda_devices')}  libcublas.so.12: {cw.get('libcublas')}  (processo sem torch, "
        "LD_LIBRARY_PATH do venv)"
    )
    if cw.get("whisper_tiny") or cw.get("error"):
        print(
            f"[{_ok(bool(cw.get('whisper_tiny')))}] faster-whisper  tiny na CUDA: "
            f"{cw.get('whisper_tiny') or cw.get('error')}"
        )
    print(
        f"       sem LD_LIBRARY_PATH: libcublas.so.12 {cb.get('libcublas')}  cuda_devices={cb.get('cuda_devices')}"
        "  (informativo: o app sempre injeta o LD_LIBRARY_PATH)"
    )
    print(
        f"device efetivo    {s['device']}"
        + (f"  — AVISO: {s['gpu']['warning']}" if s["gpu"]["warning"] else "")
    )
    for name, tool in s["tools"].items():
        print(f"[{_ok(tool['ok'])}] {name:<15} {tool.get('version')}")
    print("modelos:")
    for m in s["models"]:
        flag = "baixado" if m["downloaded"] else "não baixado (baixa no 1º uso)"
        print(
            f"       {m['id']:<15} {m['kind']:<13} ~{m['size_mb']} MB  {flag}{'  [ativo]' if m['active'] else ''}"
        )
    print(f"dados             {s['data_dir']}  (livre: {s['disk_free_mb'] // 1024} GB)")


if __name__ == "__main__":
    raise SystemExit(main())
