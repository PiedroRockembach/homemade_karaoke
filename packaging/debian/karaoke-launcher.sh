#!/usr/bin/env bash
# /usr/bin/karaoke (pacote .deb). O código fica em /opt/karaoke (somente leitura); cada usuário
# recebe uma cópia em ~/.local/share/karaoke/app, onde ficam o .venv (make install) e, ao lado, os dados.
# A cópia é refeita só quando a versão do pacote muda, preservando .venv, karaoke.env e dados.
set -euo pipefail
SRC=/opt/karaoke
BASE="${XDG_DATA_HOME:-$HOME/.local/share}/karaoke"
APP="$BASE/app"

want="$(cat "$SRC/.karaoke-version")"
extra=()
mkdir -p "$BASE"
exec 9>"$BASE/.lock"
flock 9 # dois cliques seguidos no 1º uso: o segundo espera a cópia terminar
have="$(cat "$APP/.karaoke-version" 2>/dev/null || true)"
if [[ "$want" != "$have" ]]; then
  echo ">> Preparando o Karaokê $want para o seu usuário em $APP"
  mkdir -p "$APP"
  find "$APP" -mindepth 1 -maxdepth 1 ! -name .venv ! -name karaoke.env ! -name data -exec rm -rf {} +
  cp -R "$SRC/." "$APP/"
  # Pacote atualizado: roda o make install de novo (rápido se nada mudou) para pegar dependências novas.
  [[ -n "$have" && -x "$APP/.venv/bin/python" ]] && extra=(--reinstalar)
fi
if [[ ! -f "$APP/karaoke.env" ]]; then
  printf "KARAOKE_DATA_DIR='%s'\n" "${KARAOKE_DATA_DIR:-$BASE/data}" >"$APP/karaoke.env"
fi
exec 9>&- # solta a trava (o karaokê não fica segurando)

[[ "${1:-}" == --criar-atalho ]] && rm -f "$BASE/.atalho-criado"
# 1º uso: atalho na área de trabalho (o menu já vem do pacote). O postinst não mexe no HOME de ninguém.
if [[ ! -e "$BASE/.atalho-criado" ]]; then
  desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  if mkdir -p "$desk" && cp /usr/share/applications/karaoke.desktop "$desk/karaoke.desktop"; then
    chmod +x "$desk/karaoke.desktop"
    command -v gio >/dev/null && gio set "$desk/karaoke.desktop" metadata::trusted true 2>/dev/null || true
    echo ">> Atalho criado em: $desk/karaoke.desktop"
  fi
  touch "$BASE/.atalho-criado"
fi

case "${1:-}" in
  --criar-atalho) exit 0 ;;
  --versao) echo "Karaokê $want"; exit 0 ;;
esac
exec "$APP/karaoke.sh" "${extra[@]}" "$@"
