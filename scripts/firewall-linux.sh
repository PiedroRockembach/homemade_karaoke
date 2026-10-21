#!/usr/bin/env bash
# Libera (ou fecha, com --undo) as portas do modo convidado no ufw ou no firewalld. ADR-0006 §4.
#   bash scripts/firewall-linux.sh           pergunta antes (sem terminal interativo: pula)
#   bash scripts/firewall-linux.sh --yes     não pergunta (make firewall)
#   bash scripts/firewall-linux.sh --undo    remove a regra (make firewall-undo)
# KARAOKE_FIREWALL=yes|no força a resposta. Nunca falha: sai sempre com 0 (a instalação não depende disto).
set -uo pipefail

PORT_FIRST=8765
PORT_LAST=8774
UNDO=0
ASSUME_YES=0
for a in "$@"; do
  case "$a" in
    --undo) UNDO=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "firewall: opção desconhecida: $a" >&2; exit 0 ;;
  esac
done

say() { echo ">> firewall: $*"; }

is_wsl() {
  [[ -n "${WSL_INTEROP:-}${WSL_DISTRO_NAME:-}" ]] && return 0
  grep -qi microsoft /proc/version 2>/dev/null
}

if is_wsl; then
  say "rodando no WSL: no Windows, a rede e o firewall se configuram com  karaoke.bat --configurar-rede"
  exit 0
fi

svc_active() { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$1" 2>/dev/null; }

tool=""
if svc_active ufw && grep -qE '^[[:space:]]*ENABLED[[:space:]]*=[[:space:]]*"?yes' /etc/ufw/ufw.conf 2>/dev/null; then
  tool="ufw"
elif svc_active firewalld || { command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; }; then
  tool="firewalld"
fi

if [[ -z "$tool" ]]; then
  say "nenhum firewall ativo detectado (ufw/firewalld); nada a fazer."
  exit 0
fi

if [[ "$tool" == "ufw" ]]; then
  if (( UNDO )); then
    cmds=("sudo ufw delete allow $PORT_FIRST:$PORT_LAST/tcp")
  else
    cmds=("sudo ufw allow $PORT_FIRST:$PORT_LAST/tcp comment 'karaoke convidados'")
  fi
else
  if (( UNDO )); then op="--remove-port"; else op="--add-port"; fi
  cmds=("sudo firewall-cmd --permanent $op=$PORT_FIRST-$PORT_LAST/tcp" "sudo firewall-cmd --reload")
fi

manual() {
  say "rode à mão, se quiser:"
  for c in "${cmds[@]}"; do echo "     $c"; done
}

if (( ! UNDO && ! ASSUME_YES )); then
  answer="${KARAOKE_FIREWALL:-}"
  if [[ -z "$answer" ]]; then
    if [[ ! -t 0 ]]; then
      say "sem terminal interativo; passo pulado. Depois: make firewall"
      exit 0
    fi
    read -r -p ">> Liberar as portas $PORT_FIRST-$PORT_LAST/tcp no $tool para os convidados? (pede sudo) [S/n] " answer || answer="n"
    answer="${answer:-s}"
  fi
  case "${answer,,}" in
    s|sim|y|yes) ;;
    *) say "ok, não mexi no firewall. Depois: make firewall"; exit 0 ;;
  esac
fi

for c in "${cmds[@]}"; do
  echo "   \$ $c"
  if ! bash -c "$c"; then
    say "o comando falhou (sudo negado?)."
    manual
    exit 0
  fi
done

if (( UNDO )); then
  say "portas $PORT_FIRST-$PORT_LAST/tcp fechadas no $tool."
else
  say "portas $PORT_FIRST-$PORT_LAST/tcp liberadas no $tool (só abrem de fato quando o modo convidado está ligado)."
  say "para fechar: make firewall-undo"
fi
exit 0
