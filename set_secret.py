"""Escrever um segredo no .env sem ele passar pelo ecrã, pelo chat ou pelo
histórico da shell.

    .venv/bin/python set_secret.py PINATA_JWT
    .venv/bin/python set_secret.py OPENSEA_API_KEY

Pede o valor com o terminal tapado (getpass), substitui a linha se já existir, e
imprime apenas o comprimento e os últimos 4 caracteres — o suficiente para
confirmares que colaste a coisa certa, insuficiente para servir a alguém.

Porque existe: colar segredos numa conversa queima-os. Já aconteceu uma vez com
a RELIC_POOL_KEY (24/08) e a chave teve de ser regerada.
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ENV = Path(__file__).with_name(".env")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    name = sys.argv[1].strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]+", name):
        print(f"nome inválido: {name!r} — só maiúsculas, dígitos e _")
        return 2

    if not ENV.exists():
        print(f"não encontrei {ENV}")
        return 1

    value = getpass.getpass(f"cola o valor de {name} (não aparece no ecrã): ").strip()
    if not value:
        print("vazio — nada foi escrito.")
        return 1
    # Aspas coladas por engano são o erro mais comum e dão um valor inválido
    # que só rebenta muito mais tarde.
    if value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1].strip()
        print("(tirei as aspas)")

    lines = [
        ln for ln in ENV.read_text().splitlines()
        if not ln.strip().startswith(f"{name}=")
    ]
    lines.append(f"{name}={value}")
    ENV.write_text("\n".join(lines) + "\n")

    print(f"✅ {name} escrito no .env — {len(value)} caracteres, acaba em …{value[-4:]}")
    print("   Não te esqueças de o pôr TAMBÉM no Doppler, com o mesmo valor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
