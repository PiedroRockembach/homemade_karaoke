#!/usr/bin/env bash
# Instala o karaokê em .venv/ (sem sudo, nada global).
# - escolhe o índice do torch: cu129 se houver nvidia-smi, senão cpu (KARAOKE_TORCH_INDEX força: cu129|cpu|<url>)
# - instala o pacote em modo editável com extras de desenvolvimento
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
TORCH_VERSION="2.13.0"

if ! command -v "$PY" >/dev/null; then
  echo "ERRO: python3 não encontrado (precisa de Python >= 3.12)." >&2; exit 1
fi
"$PY" - <<'PYEOF' || { echo "ERRO: precisa de Python >= 3.12." >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)
PYEOF
command -v ffmpeg >/dev/null || echo "AVISO: ffmpeg não encontrado no PATH — instale pelo gerenciador de pacotes do sistema." >&2

idx="${KARAOKE_TORCH_INDEX:-auto}"
if [[ "$idx" == "auto" ]]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then idx="cu129"; else idx="cpu"; fi
fi
case "$idx" in
  cu129|cpu) TORCH_INDEX_URL="https://download.pytorch.org/whl/$idx" ;;
  http*)     TORCH_INDEX_URL="$idx" ;;
  *) echo "ERRO: KARAOKE_TORCH_INDEX inválido: $idx (use cu129, cpu ou uma URL)" >&2; exit 1 ;;
esac
echo ">> torch $TORCH_VERSION de $TORCH_INDEX_URL"

[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
VPY=.venv/bin/python
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install "torch==$TORCH_VERSION" --index-url "$TORCH_INDEX_URL"
# torchaudio não é necessário (demucs 4.1 usa soundfile/ffmpeg); constraints impede troca do torch.
"$VPY" -m pip install -c constraints.txt -e ".[dev]"

mkdir -p data

# Opcional: liberar as portas do modo convidado no ufw/firewalld (pergunta; sem TTY pula; nunca falha).
bash scripts/firewall-linux.sh || echo "AVISO: o passo do firewall falhou; rode depois: make firewall" >&2
echo ">> instalação concluída. Rode: make doctor  |  make start"
