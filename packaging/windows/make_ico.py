"""Gera o ícone do Windows (.ico, 16 a 256 px) a partir do PNG.  Uso: python make_ico.py <png> <ico>  (precisa de Pillow)."""

import sys

from PIL import Image

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main(src: str, dst: str) -> None:
    img = Image.open(src).convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    square.resize((256, 256), Image.Resampling.LANCZOS).save(dst, format="ICO", sizes=SIZES)
    print(f">> icone gerado: {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
