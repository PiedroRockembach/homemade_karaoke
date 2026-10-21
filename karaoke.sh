#!/usr/bin/env bash
# Abre o karaokê com um clique: instala na 1ª vez (make install) e inicia (make start).
#   ./karaoke.sh                 instala se preciso e inicia
#   ./karaoke.sh --reinstalar    força o make install antes de iniciar
#   ./karaoke.sh --criar-atalho  cria o atalho na área de trabalho e no menu de aplicativos
set -uo pipefail
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$DIR" || exit 1
# Instalado pelo .run/.deb: karaoke.env (ao lado deste script) define KARAOKE_DATA_DIR. No clone não existe.
if [[ -f "$DIR/karaoke.env" ]]; then set -a; . "$DIR/karaoke.env"; set +a; fi

pausa() { [[ -t 0 ]] && read -rp "Aperte Enter para fechar..." _; }

criar_atalho() {
  local desk apps entry icon=multimedia-audio-player
  [[ -f "$DIR/karaoke_icon.png" ]] && icon="$DIR/karaoke_icon.png"
  desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  apps="$HOME/.local/share/applications"
  mkdir -p "$apps" "$desk"
  entry="[Desktop Entry]
Type=Application
Name=Karaokê
Comment=Karaokê local (YouTube + letra sincronizada)
Exec=\"$DIR/karaoke.sh\"
Path=$DIR
Icon=$icon
Terminal=true
Categories=AudioVideo;Audio;"
  printf '%s\n' "$entry" > "$apps/karaoke.desktop"
  printf '%s\n' "$entry" > "$desk/karaoke.desktop"
  chmod +x "$apps/karaoke.desktop" "$desk/karaoke.desktop"
  # GNOME exige marcar o atalho como confiável; nos outros ambientes é ignorado.
  command -v gio >/dev/null && gio set "$desk/karaoke.desktop" metadata::trusted true 2>/dev/null
  echo "Atalho criado em: $desk/karaoke.desktop"
  echo "                  $apps/karaoke.desktop"
}

case "${1:-}" in
  --criar-atalho) criar_atalho; exit 0 ;;
  --reinstalar)   ;;
esac

if ! command -v make >/dev/null; then
  echo "ERRO: 'make' não encontrado. Instale pelo gerenciador de pacotes (ex.: sudo pacman -S make)." >&2
  pausa; exit 1
fi

if [[ ! -x .venv/bin/python || "${1:-}" == "--reinstalar" ]]; then
  echo ">> Primeira execução: instalando (pode levar alguns minutos)..."
  if ! make install; then
    echo "ERRO: a instalação falhou. Veja as mensagens acima." >&2
    pausa; exit 1
  fi
fi

make start || { echo "O karaokê encerrou com erro." >&2; pausa; exit 1; }
