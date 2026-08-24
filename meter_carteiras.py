"""Meter no .env carteiras criadas noutro sítio (MetaMask, etc.).

    .venv/bin/python meter_carteiras.py           # 10 carteiras, prefixo RW
    .venv/bin/python meter_carteiras.py 5 RX      # lote novo

Para cada carteira pede o ENDEREÇO (visível — é público) e a CHAVE PRIVADA
(tapada). Deixa em branco para parar antes do fim; o que já foi introduzido é
gravado na mesma.

A verificação que interessa: **confirma que a chave corresponde ao endereço**.
Colar a chave da conta errada é o erro mais fácil de cometer a exportar dez
contas seguidas do MetaMask, e sem esta verificação só se descobriria na hora
do mint — com um relic já criado e uma carteira queimada.

RECUSA-SE a sobrescrever refs que já existam: uma chave substituída depois do
mint deixa o troféu sem forma de ser transferido ao vencedor.
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ENV = Path(__file__).with_name(".env")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    prefix = (sys.argv[2] if len(sys.argv) > 2 else "RW").strip().upper()
    if not (1 <= n <= 100) or not re.fullmatch(r"[A-Z]{1,6}", prefix):
        print("uso: meter_carteiras.py [1-100] [PREFIXO]")
        return 2

    try:
        from eth_account import Account
    except ImportError:
        print("falta o eth_account (vem com o web3).")
        return 1

    if not ENV.exists():
        print(f"não encontrei {ENV}")
        return 1
    text = ENV.read_text()

    refs = [f"{prefix}{i:02d}" for i in range(1, n + 1)]
    existing = [r for r in refs if re.search(rf"^{r}_(ADDR|PK)=", text, re.M)]
    if existing:
        print(f"⛔ já existem no .env: {', '.join(existing)}")
        print("   Usa outro prefixo para um lote novo. Nada foi escrito.")
        return 1

    print(f"\n{n} carteiras. Deixa o endereço em branco para parar.\n")
    collected: list[tuple[str, str, str]] = []
    for ref in refs:
        addr = input(f"{ref} endereço: ").strip()
        if not addr:
            break
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
            print("   ⛔ não parece um endereço (0x + 40 hex). Repete esta carteira.")
            return 1
        key = getpass.getpass(f"{ref} chave privada (não aparece): ").strip()
        if key.startswith(("'", '"')) and key[-1] == key[0]:
            key = key[1:-1].strip()
        if not key.startswith("0x"):
            key = "0x" + key
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", key):
            print("   ⛔ chave com formato errado (0x + 64 hex). Nada foi escrito.")
            return 1

        # A verificação que apanha o erro real: a chave é mesmo desta conta?
        try:
            derived = Account.from_key(key).address
        except Exception as e:  # noqa: BLE001
            print(f"   ⛔ chave inválida: {e!r}. Nada foi escrito.")
            return 1
        if derived.lower() != addr.lower():
            print(f"   ⛔ ESTA CHAVE NÃO É DESTE ENDEREÇO — pertence a {derived}")
            print("      Trocaste as contas a exportar. Nada foi escrito.")
            return 1
        print("   ✅ chave confere com o endereço")
        collected.append((ref, addr, key))

    if not collected:
        print("nada introduzido.")
        return 1

    lines = text.splitlines()
    for ref, addr, key in collected:
        lines.append(f"{ref}_ADDR={addr}")
        lines.append(f"{ref}_PK={key}")
    ENV.write_text("\n".join(lines) + "\n")

    got = [r for r, _, _ in collected]
    print(f"\n✅ {len(got)} carteiras escritas no .env, todas verificadas")
    print(f"\nRELIC_WALLET_REFS={','.join(got)}")
    print("\nFalta copiar os pares _ADDR/_PK e esta linha para o Doppler.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
