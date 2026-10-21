"""Monta a pasta da app que vai dentro dos instaladores.

    python3 packaging/stage.py <destino> <versão-completa>   copia os arquivos da app para <destino>
    python3 packaging/stage.py --version                       imprime a versão do pyproject.toml

Só entram arquivos versionados no Git (ou novos e não ignorados pelo .gitignore): nunca `.venv/`,
`data/`, caches ou `.git/`. Ficam de fora também o que não é necessário para rodar (testes, CI, design).
"""

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE = (".github/", "packaging/", "tests/", "design/", "dist/", "build/", ".venv/", "data/")


def version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def app_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    files = {f for f in out.split("\0") if f}
    return sorted(f for f in files if not f.startswith(EXCLUDE) and (ROOT / f).is_file())


def stage(dest: Path, full_version: str) -> int:
    if dest.exists():
        shutil.rmtree(dest)
    files = app_files()
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    (dest / ".karaoke-version").write_text(full_version + "\n", encoding="utf-8", newline="\n")
    return len(files)


def main(argv: list[str]) -> int:
    if argv == ["--version"]:
        print(version())
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    n = stage(Path(argv[0]).resolve(), argv[1])
    print(f">> {n} arquivos copiados para {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
