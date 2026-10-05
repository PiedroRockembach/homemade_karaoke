"""Roda os testes JS sem DOM (Palco: letra/formatação; Controle: modelo do editor de sincronia) com o Deno do .venv.
Dono: frontend-engineer (T-007); glob ampliado pelo qa-engineer (T-007, ciclo 20261004-1742)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_TESTS = sorted(Path(__file__).parent.glob("test_*_test.js"))


def _deno() -> str | None:
    local = Path(sys.prefix) / "bin" / "deno"
    return str(local) if local.exists() else shutil.which("deno")


@pytest.mark.parametrize("js_test", JS_TESTS, ids=lambda p: p.name)
def test_stage_js(js_test: Path) -> None:
    deno = _deno()
    if not deno:
        pytest.skip("deno não encontrado")
    proc = subprocess.run(
        [deno, "test", "--quiet", "--no-check", str(js_test)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
