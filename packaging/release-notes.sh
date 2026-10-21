#!/usr/bin/env bash
# Notas do release a partir do CHANGELOG: seção [Não lançado]; se vazia, a seção da versão.
#   bash packaging/release-notes.sh <versão> <versão-completa> [commit]  > notas.md
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$1" FULL="$2" SHA="${3:-}"
secao() { awk -v h="$1" 'index($0, h) == 1 { on = 1; next } on && /^## \[/ { exit } on' CHANGELOG.md; }
corpo="$(secao '## [Não lançado]')"
[[ -n "${corpo//[[:space:]]/}" ]] || corpo="$(secao "## [$VERSION]")"
cat <<MD
## Karaokê $FULL

$corpo

---
### Instalar
- **Windows 10/11**: \`karaoke-$FULL-windows-setup.exe\` (sem administrador; usa o WSL).
- **Ubuntu/Debian**: \`sudo apt install ./karaoke_${FULL}_all.deb\` e depois abra "Karaokê" no menu.
- **Qualquer Linux** (sem sudo): \`chmod +x karaoke-$FULL-linux.run && ./karaoke-$FULL-linux.run\`.

A 1ª abertura baixa ~7 GB (PyTorch, Demucs, Whisper). Confira os arquivos com \`SHA256SUMS\`.
${SHA:+
Commit: $SHA}
MD
