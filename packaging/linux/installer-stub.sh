#!/usr/bin/env bash
# Instalador do Karaokê para Linux (qualquer distro, sem sudo). Gerado por packaging/build-linux.sh.
#   ./karaoke-<versão>-linux.run               instala ou atualiza (pergunta antes de baixar ~7 GB)
#   ./karaoke-<versão>-linux.run --yes         sem perguntas
#   ./karaoke-<versão>-linux.run --uninstall   desinstala (pergunta antes de apagar suas músicas)
# Variáveis: KARAOKE_SKIP_DEPS=1 (não roda o `make install` agora; ele roda na 1ª abertura),
#            KARAOKE_HOME (padrão ~/.local/share/karaoke), KARAOKE_DATA_DIR (padrão $KARAOKE_HOME/data).
set -euo pipefail
VERSION="__KARAOKE_VERSION__"
SELF="$(readlink -f "$0")"
ARCHIVE_LINE="$(awk '/^__ARCHIVE_BELOW__$/ { print NR + 1; exit }' "$SELF")"
if [[ -z "$ARCHIVE_LINE" ]]; then
  BASE="$(dirname "$SELF")" # cópia uninstall.sh: desinstala a pasta onde ela está (ignora o ambiente)
else
  BASE="${KARAOKE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/karaoke}"
fi
APP="$BASE/app"
BIN="$HOME/.local/bin"

YES=0 PURGE=0 SHORTCUT=1 ACTION=install
[[ -z "$ARCHIVE_LINE" ]] && ACTION=uninstall # cópia do desinstalador (sem a app embutida)
for arg in "$@"; do
  case "$arg" in
    -y | --yes) YES=1 ;;
    --uninstall) ACTION=uninstall ;;
    --purge) PURGE=1 ;;
    --no-shortcut) SHORTCUT=0 ;;
    -h | --help) sed -n '2,7p' "$SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $arg (veja --help)" >&2; exit 2 ;;
  esac
done

perguntar() { # perguntar "texto" S|N  → 0 = sim
  local padrao="$2" r
  if [[ $YES == 1 ]]; then [[ $padrao == S ]]; return; fi
  if [[ ! -t 0 ]]; then [[ $padrao == S ]]; return; fi
  if [[ $padrao == S ]]; then read -rp "$1 [S/n] " r; [[ ! $r =~ ^[nN] ]]; else read -rp "$1 [s/N] " r; [[ $r =~ ^[sSyY] ]]; fi
}

desktop_dir() { xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"; }

desinstalar() {
  local data="" f desk
  [[ -f "$APP/karaoke.env" ]] && data="$(sed -n "s/^KARAOKE_DATA_DIR=//p" "$APP/karaoke.env" | tail -1)"
  data="${data//\'/}"
  data="${data:-$BASE/data}"
  desk="$(desktop_dir)"
  echo ">> Desinstalando o Karaokê de $APP"
  for f in "$HOME/.local/share/applications/karaoke.desktop" "$desk/karaoke.desktop"; do
    # Só remove atalhos desta instalação (não mexe no atalho de um clone do repositório).
    [[ -f "$f" ]] && grep -qF "$APP/" "$f" && rm -f "$f" && echo "   removido: $f"
  done
  [[ -L "$BIN/karaoke" && "$(readlink "$BIN/karaoke")" == "$APP/"* ]] && rm -f "$BIN/karaoke"
  rm -rf "$APP" # inclui o .venv (PyTorch, modelos do pip): pode ser baixado de novo
  rm -f "$BASE/uninstall.sh"
  if [[ -d "$data" ]]; then
    if [[ $PURGE == 1 ]] || { [[ $YES == 0 && -t 0 ]] && perguntar "Apagar também suas músicas e dados em $data? Não dá para desfazer." N; }; then
      rm -rf "$data" && echo "   dados apagados: $data"
    else
      echo "   Seus dados foram mantidos em: $data  (apague a pasta quando quiser)"
    fi
  fi
  rmdir "$BASE" 2>/dev/null || true
  echo ">> Pronto. Se você liberou o firewall, feche a porta com: sudo ufw delete allow 8765:8774/tcp (ou firewalld)."
}

checar_requisitos() {
  local falta=()
  command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null ||
    falta+=("python3 (>= 3.12)")
  python3 -c 'import venv, ensurepip' 2>/dev/null || falta+=("python3-venv")
  command -v make >/dev/null || falta+=("make")
  command -v ffmpeg >/dev/null || falta+=("ffmpeg")
  ((${#falta[@]} == 0)) && return 0
  echo "Faltam no sistema: ${falta[*]}" >&2
  echo "  Ubuntu/Debian: sudo apt install python3 python3-venv make ffmpeg" >&2
  echo "  Fedora:        sudo dnf install python3 make ffmpeg" >&2
  echo "  Arch:          sudo pacman -S python make ffmpeg" >&2
  return 1
}

instalar() {
  echo "== Karaokê $VERSION =="
  echo "   app:   $APP"
  echo "   dados: ${KARAOKE_DATA_DIR:-$BASE/data}"
  perguntar "Instalar agora?" S || { echo "Cancelado."; exit 1; }
  if ! checar_requisitos; then
    [[ "${KARAOKE_SKIP_DEPS:-0}" == 1 ]] || { echo "Instale o que falta e rode o instalador de novo." >&2; exit 1; }
    echo "AVISO: seguindo assim mesmo (KARAOKE_SKIP_DEPS=1)." >&2
  fi

  TMPD="$(mktemp -d)"
  trap 'rm -rf "${TMPD:-}"' EXIT
  tail -n +"$ARCHIVE_LINE" "$SELF" | tar -xzf - -C "$TMPD"
  mkdir -p "$APP"
  # Atualização: troca o código e mantém o ambiente (.venv), a configuração e os dados.
  find "$APP" -mindepth 1 -maxdepth 1 ! -name .venv ! -name karaoke.env ! -name data -exec rm -rf {} +
  cp -R "$TMPD/karaoke/." "$APP/"
  if [[ ! -f "$APP/karaoke.env" ]]; then
    printf "KARAOKE_DATA_DIR='%s'\n" "${KARAOKE_DATA_DIR:-$BASE/data}" >"$APP/karaoke.env"
  fi
  mkdir -p "$(sed -n "s/^KARAOKE_DATA_DIR=//p" "$APP/karaoke.env" | tr -d "'")"
  head -n "$((ARCHIVE_LINE - 2))" "$SELF" | sed "s/^VERSION=.*/VERSION=\"$VERSION\"/" >"$BASE/uninstall.sh"
  chmod +x "$BASE/uninstall.sh" "$APP/karaoke.sh"
  mkdir -p "$BIN" && ln -sfn "$APP/karaoke.sh" "$BIN/karaoke"

  if [[ "${KARAOKE_SKIP_DEPS:-0}" == 1 ]]; then
    echo ">> KARAOKE_SKIP_DEPS=1: as dependências serão instaladas na 1ª abertura."
  else
    echo
    echo "A instalação das dependências baixa ~7 GB (PyTorch, Demucs, Whisper) e leva de 5 a 30 minutos."
    if perguntar "Baixar agora? (se não, baixa na 1ª vez que você abrir o karaokê)" S; then
      (cd "$APP" && make install) || echo "AVISO: o 'make install' falhou; ele roda de novo ao abrir o karaokê." >&2
    fi
  fi

  if [[ $SHORTCUT == 1 ]]; then
    "$APP/karaoke.sh" --criar-atalho
  fi
  echo
  echo "== Karaokê $VERSION instalado =="
  echo "   Abrir:        clique em \"Karaokê\" na área de trabalho ou no menu (ou rode: karaoke)"
  echo "   Desinstalar:  $BASE/uninstall.sh"
}

if [[ $ACTION == uninstall ]]; then desinstalar; else instalar; fi
exit 0
__ARCHIVE_BELOW__
