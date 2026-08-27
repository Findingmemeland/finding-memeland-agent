"""Meter UMA carteira nova no .env e deixar só ela na lista de mint.

    .venv/bin/python trocar_carteira.py                 # ref RX01
    .venv/bin/python trocar_carteira.py RX02            # ref à escolha

O que faz, por esta ordem:
  1. pede o ENDEREÇO (visível — é público) e a CHAVE PRIVADA (tapada), e
     confirma que a chave é mesmo dessa conta antes de escrever seja o que for;
  2. acrescenta {REF}_ADDR / {REF}_PK ao .env (recusa-se se a ref já existir);
  3. reescreve RELIC_WALLET_REFS para conter SÓ a ref nova — as carteiras que
     ainda não mintaram (RW06-RW10) deixam de poder ser escolhidas. Os pares
     RWxx_ADDR/RWxx_PK ficam intactos: a transferência do troféu resolve a chave
     pela ref gravada no relic, e RW01-RW05 ainda guardam relics.

Nunca mostra a chave. No fim imprime os comandos para o Doppler — a chave vai
por pipe a partir do .env, sem passar pelo ecrã nem pelo histórico da shell.
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ENV = Path(__file__).with_name(".env")


def main() -> int:
    ref = (sys.argv[1] if len(sys.argv) > 1 else "RX01").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,6}[0-9]{2}", ref):
        print("uso: trocar_carteira.py [REF]   (ex.: RX01)")
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

    if re.search(rf"^{ref}_(ADDR|PK)=", text, re.M):
        print(f"⛔ {ref} já existe no .env — uma carteira sobrescrita depois de mintar é um "
              "relic sem forma de transferir o troféu. Usa outra ref. Nada foi escrito.")
        return 1

    addr = input(f"{ref} endereço: ").strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
        print("⛔ não parece um endereço (0x + 40 hex). Nada foi escrito.")
        return 1
    key = getpass.getpass(f"{ref} chave privada (não aparece): ").strip()
    if key.startswith(("'", '"')) and key[-1] == key[0]:
        key = key[1:-1].strip()
    if not key.startswith("0x"):
        key = "0x" + key
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", key):
        print("⛔ chave com formato errado (0x + 64 hex). Nada foi escrito.")
        return 1
    try:
        derived = Account.from_key(key).address
    except Exception as e:  # noqa: BLE001
        print(f"⛔ chave inválida: {e!r}. Nada foi escrito.")
        return 1
    if derived.lower() != addr.lower():
        print(f"⛔ ESTA CHAVE NÃO É DESTE ENDEREÇO — pertence a {derived}. Nada foi escrito.")
        return 1
    print("✅ chave confere com o endereço")

    lines = text.splitlines()
    old_refs = ""
    for i, line in enumerate(lines):
        if line.startswith("RELIC_WALLET_REFS="):
            old_refs = line.split("=", 1)[1].strip()
            lines[i] = f"RELIC_WALLET_REFS={ref}"
            break
    else:
        lines.append(f"RELIC_WALLET_REFS={ref}")
    lines.append(f"{ref}_ADDR={addr}")
    lines.append(f"{ref}_PK={key}")
    ENV.write_text("\n".join(lines) + "\n")

    dropped = [r for r in old_refs.split(",") if r.strip() and r.strip() != ref]
    print(f"\n✅ {ref} escrita no .env; RELIC_WALLET_REFS = {ref}")
    if dropped:
        print(f"   fora da lista de mint (chaves mantidas): {', '.join(dropped)}")

    print("\nAgora o Doppler (config prd). A chave vai por pipe, nunca pelo ecrã:\n")
    print(f"  doppler secrets set RELIC_WALLET_REFS={ref} {ref}_ADDR={addr} "
          "--project finding-memeland --config prd")
    print(f"  grep '^{ref}_PK=' .env | cut -d= -f2- | tr -d '\\n' | "
          f"doppler secrets set {ref}_PK --project finding-memeland --config prd")
    print("\nA Railway reinicia o processo ao mudar o env — faz isto sem hunt a decorrer.")
    print(f"Depois: /relic_mint → confirma eth_getCode do contrato novo == o do Maroon Ledgerkeep "
          "e o slot EIP-1967 a acabar em 95d452fc85869a7834189f41ec6bb0915f943aa3.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
