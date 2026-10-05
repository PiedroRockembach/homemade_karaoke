"""Entrada do subprocesso de IA: despacha separate/transcribe e reporta progresso.

Protocolo (desvio do `multiprocessing`/`mp.Queue` da ARCHITECTURE — ver HANDOFF T-006): o JobRunner executa
`python -m karaoke.jobs.worker <task.json>` com `subprocess.Popen` e um ambiente explícito
(`system.cuda_env()` + `model_env()`, `HF_HUB_OFFLINE=1`), o que garante o `LD_LIBRARY_PATH` das libs CUDA mesmo
sob `make dev`. O worker escreve **uma linha JSON por mensagem** no stdout original:

    {"type": "progress", "value": 0.42}
    {"type": "log", "message": "..."}
    {"type": "result", "data": {...}}
    {"type": "error", "code": "gpu_out_of_memory", "message": "..."}

Tudo que as bibliotecas imprimem vai para o stderr (o runner grava em data/tmp/<job>/worker.log).
Cancelamento = `terminate()` do processo (a VRAM é liberada ao sair).

Dono: backend-engineer (T-006).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from typing import Any


def classify(exc: BaseException, task: str) -> tuple[str, str]:
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "out of memory" in low or "outofmemory" in low or "cublas_status_alloc_failed" in low:
        return (
            "gpu_out_of_memory",
            "A GPU ficou sem memória. Feche outros programas que usam a GPU e tente de novo.",
        )
    if (isinstance(exc, OSError) and getattr(exc, "errno", None) == 28) or "no space left" in low:
        return "disk_full", "Disco cheio. Libere espaço e tente de novo."
    if "local_files_only" in low or "cannot find the requested files" in low or "localentrynotfound" in low:
        return "model_download_failed", "O modelo de IA não está baixado."
    if task == "separate":
        return "separation_failed", "Falha ao separar a voz da música."
    return "transcription_failed", "Falha ao transcrever a letra."


def _set_pdeathsig() -> None:
    """Linux: o kernel manda SIGKILL ao worker quando a thread do servidor que o criou morre (prctl no próprio
    processo filho, sem `preexec_fn`, que não é seguro num servidor com threads). Falha silenciosa."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        import signal

        pr_set_pdeathsig = 1
        ctypes.CDLL(None, use_errno=True).prctl(pr_set_pdeathsig, int(signal.SIGKILL), 0, 0, 0)
    except (OSError, AttributeError):
        pass


def _watch_parent() -> None:
    """Se o servidor morrer (até com SIGKILL), o worker sai junto: não fica preso à GPU nem escreve no tmp."""
    parent = os.getppid()
    _set_pdeathsig()
    if os.getppid() != parent:  # o pai morreu antes do prctl
        os._exit(3)

    def loop() -> None:  # rede de segurança (e único mecanismo fora do Linux)
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(3)

    threading.Thread(target=loop, name="parent-watch", daemon=True).start()


def main(argv: list[str]) -> int:
    _watch_parent()
    # stdout original = canal do protocolo; o resto (prints das libs) vai para o stderr
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def send(msg: dict[str, Any]) -> None:
        proto.write(json.dumps(msg, ensure_ascii=False) + "\n")
        proto.flush()

    task: dict[str, Any] = {}
    try:
        with open(argv[1], encoding="utf-8") as fh:
            task = json.load(fh)
        kind = task["task"]
        if kind == "separate":
            from karaoke.jobs import separate

            result = separate.run(task["params"], send)
        elif kind == "transcribe":
            from karaoke.jobs import transcribe

            result = transcribe.run(task["params"], send)
        else:
            raise ValueError(f"tarefa desconhecida: {kind}")
        out_file = task.get("result_file")
        if out_file:  # resultado grande (segmentos) vai para arquivo
            tmp = out_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
            os.replace(tmp, out_file)
            send({"type": "result", "data": {"result_file": out_file}})
        else:
            send({"type": "result", "data": result})
        return 0
    except BaseException as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        code, message = classify(exc, task.get("task", ""))
        send(
            {
                "type": "error",
                "code": code,
                "message": message,
                "detail": f"{type(exc).__name__}: {exc}"[:500],
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
