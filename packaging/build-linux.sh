#!/usr/bin/env bash
# Gera os instaladores Linux em dist/:
#   karaoke-<versão>-linux.run     auto-extraível, qualquer distro, sem sudo
#   karaoke_<versão>_all.deb       Ubuntu/Debian (código em /opt/karaoke)
# <versão> = <version do pyproject>-build.<KARAOKE_BUILD> (padrão 0). Precisa de: python3, tar, gzip
# e dpkg-deb (ou, sem ele, ar). Uso local: bash packaging/build-linux.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
FULL="$("$PY" packaging/stage.py --version)-build.${KARAOKE_BUILD:-0}"
OUT="$ROOT/dist"
WORK="$OUT/work-linux"
TAR=(tar --owner=0 --group=0 --numeric-owner)
rm -rf "$WORK"
mkdir -p "$WORK"
"$PY" packaging/stage.py "$WORK/karaoke" "$FULL"
find "$WORK/karaoke" -type d -exec chmod 755 {} +
find "$WORK/karaoke" -type f -exec chmod 644 {} +
find "$WORK/karaoke" -type f -name '*.sh' -exec chmod 755 {} +

# --- .run: script de instalação + tar.gz da app logo após a linha __ARCHIVE_BELOW__
RUN="$OUT/karaoke-$FULL-linux.run"
sed "s/__KARAOKE_VERSION__/$FULL/" packaging/linux/installer-stub.sh >"$RUN"
"${TAR[@]}" -C "$WORK" -czf - karaoke >>"$RUN"
chmod 755 "$RUN"
echo ">> $RUN"

# --- .deb
R="$WORK/deb"
install -d "$R/DEBIAN" "$R/opt" "$R/usr/bin" "$R/usr/share/applications" "$R/usr/share/pixmaps" \
  "$R/usr/share/doc/karaoke"
cp -R "$WORK/karaoke" "$R/opt/karaoke"
install -m 755 packaging/debian/karaoke-launcher.sh "$R/usr/bin/karaoke"
install -m 644 packaging/debian/karaoke.desktop "$R/usr/share/applications/karaoke.desktop"
install -m 644 karaoke_icon.png "$R/usr/share/pixmaps/karaoke.png"
install -m 644 README.md CHANGELOG.md "$R/usr/share/doc/karaoke/"
size="$(du -sk "$R" | cut -f1)"
sed -e "s/@VERSION@/$FULL/" -e "s/@SIZE@/$size/" packaging/debian/control.in >"$R/DEBIAN/control"
DEB="$OUT/karaoke_${FULL}_all.deb"
rm -f "$DEB"
if command -v dpkg-deb >/dev/null; then
  dpkg-deb --root-owner-group -Zxz --build "$R" "$DEB" >/dev/null
else
  # Sem dpkg-deb (ex.: Arch/Fedora): monta o .deb na mão (ar com debian-binary, control.tar.gz e data.tar.gz).
  echo "AVISO: dpkg-deb não encontrado; montando o .deb com ar." >&2
  printf '2.0\n' >"$WORK/debian-binary"
  "${TAR[@]}" -C "$R/DEBIAN" -czf "$WORK/control.tar.gz" .
  "${TAR[@]}" -C "$R" --exclude=./DEBIAN -czf "$WORK/data.tar.gz" .
  (cd "$WORK" && ar rc "$DEB" debian-binary control.tar.gz data.tar.gz)
fi
echo ">> $DEB"
