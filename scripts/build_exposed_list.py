"""Construir a lista de queimados (expostos) a partir do histórico do repositório.

Qualquer contrato:tokenId que tenha estado num ficheiro versionado deste
repositório público nunca pode ser alvo (Pedro, 09/10). Este script lê TODOS
os ficheiros de TODOS os commits, tira-lhes os pares e escreve
`src/finding_memeland/target/exposed.txt` — um resumo SHA-256 truncado por
par, nunca o par.

SÓ IMPRIME CONTAGENS. Nem um contrato, nem um tokenId, nem um nome saem
daqui para o ecrã — só quantos, e de que ficheiros.

    .venv/bin/python scripts/build_exposed_list.py            # constrói e grava
    .venv/bin/python scripts/build_exposed_list.py --check    # só compara

Corre-se da raiz do repositório, na máquina do operador: precisa do
histórico git (o Railway não o tem, e não precisa — lê o ficheiro gravado).
Voltar a correr depois de qualquer commit que tenha posto dados no
repositório por engano; a lista só cresce.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finding_memeland.target.exposed import (  # noqa: E402
    DEFAULT_PATH,
    ExposedList,
    pairs_in_blob,
    render_digests,
)


def _git(*args: str, cwd: Path, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, input=stdin, check=True,
                          capture_output=True).stdout


def versioned_blobs(repo: Path) -> dict[str, set[str]]:
    """{blob sha: every path it was ever committed under}, over all refs."""
    blobs: dict[str, set[str]] = {}
    listing = _git("rev-list", "--all", "--objects", cwd=repo).decode("utf-8", "replace")
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            blobs.setdefault(sha, set()).add(path)
    kinds = _git("cat-file", "--batch-check", cwd=repo,
                 stdin=("\n".join(blobs) + "\n").encode()).decode().splitlines()
    return {line.split()[0]: blobs[line.split()[0]]
            for line in kinds if line.split()[1:2] == ["blob"]}


def blob_contents(repo: Path, shas: list[str]):
    """(sha, bytes) for every blob, through ONE `git cat-file --batch`."""
    out = _git("cat-file", "--batch", cwd=repo, stdin=("\n".join(shas) + "\n").encode())
    pos = 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        sha, _kind, size = out[pos:end].decode().split()
        start = end + 1
        yield sha, out[start:start + int(size)]
        pos = start + int(size) + 1                 # the newline after the content


def scan(repo: Path) -> tuple[set[tuple[str, int]], dict[str, int], int]:
    """(pairs, {path: distinct pairs that path ever exposed}, blobs read)."""
    blobs = versioned_blobs(repo)
    pairs: set[tuple[str, int]] = set()
    by_path: dict[str, set] = {}
    for sha, raw in blob_contents(repo, list(blobs)):
        paths = blobs[sha]
        found = pairs_in_blob(raw)
        if not found:
            continue
        pairs |= found
        for path in paths:
            by_path.setdefault(path, set()).update(found)
    return pairs, {p: len(s) for p, s in by_path.items()}, len(blobs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="não grava: diz se o ficheiro gravado cobre o histórico")
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--out", default=str(DEFAULT_PATH))
    args = ap.parse_args(argv)

    pairs, by_path, n_blobs = scan(Path(args.repo))
    data = sum(n for p, n in by_path.items() if not p.endswith(".py"))
    print(f"histórico: {n_blobs} ficheiro(s) em todas as versões · "
          f"{len(by_path)} caminho(s) com pares · {len(pairs)} par(es) distintos")
    for path, n in sorted(by_path.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:>6}  {path}")
    print(f"(pares em ficheiros que não são código: {data}, contados por caminho)")

    out = Path(args.out)
    if args.check:
        have = ExposedList.load(out)
        missing = sum(1 for c, t in pairs if not (have.sees and have.has(c, t)))
        print(f"gravado: {have!r} · em falta: {missing}")
        return 1 if missing or not have.sees else 0
    # a lista só cresce: o que já lá estava fica, mesmo que o histórico mude
    kept = [line.strip() for line in (out.read_text().splitlines() if out.exists() else [])
            if line.strip() and not line.startswith("#")]
    text = render_digests(pairs)
    body = sorted(set(kept) | {ln for ln in text.splitlines() if ln and not ln.startswith("#")})
    head = "".join(ln + "\n" for ln in text.splitlines() if ln.startswith("#")
                   and not ln.startswith("# entries"))
    out.write_text(f"{head}# entries: {len(body)}\n" + "\n".join(body) + "\n",
                   encoding="utf-8")
    print(f"gravado: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out.name} · "
          f"{len(body)} resumo(s) (inclui o canário)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
