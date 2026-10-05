"""QR do convite em SVG (ADR-0003 §3): `segno`, preto no branco nos dois temas, zona de silêncio de 4 módulos e
`viewBox` (o tamanho na tela vem do CSS do Palco)."""

from __future__ import annotations

import io

import segno


def qr_svg(url: str) -> bytes:
    qr = segno.make(url, error="m", micro=False)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", xmldecl=False, svgns=True, omitsize=True, border=4, dark="#000", light="#fff")
    return buf.getvalue()
