"""Gerar as carteiras de mint dos relics e escrevê-las no .env.

    .venv/bin/python gerar_carteiras_relic.py          # 10 carteiras (RW01..RW10)
    .venv/bin/python gerar_carteiras_relic.py 10 RW    # explícito
    .venv/bin/python gerar_carteiras_relic.py 5 RX     # mais um lote, prefixo novo

Cada carteira serve UM relic e nunca mais — é isso que impede que dois relics
sejam ligados pelo minter comum. Dez carteiras são dez relics.

O que aparece no ecrã: só os ENDEREÇOS, que são públicos e é o que precisas para
financiar. As chaves privadas vão direitas para o .env, sem passarem pelo ecrã
nem pelo histórico da shell.

RECUSA-SE a sobrescrever refs que já existam no .env — uma carteira sobrescrita
depois de mintar é um relic que fica sem forma de transferir o troféu.

Depois de correr:
  1. financia cada endereço com ~$0.50 em ETH na Base, com valores DIFERENTES
     e não redondos (mesma quantia em todas, na mesma meia hora, agrupa-as)
  2. copia os 20 pares {REF}_ADDR / {REF}_PK do .env para o Doppler
  3. mete RELIC_WALLET_REFS=RW01,RW02,... no .env e no Doppler
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENV = Path(__file__).with_name(".env")


def main() -> int:
    n = 10
    prefix = "RW"
    if len(sys.argv) > 1:
        try:
            n = int(sys.argv[1])
        except ValueError:
            print(__doc__)
            return 2
    if len(sys.argv) > 2:
        prefix = sys.argv[2].strip().upper()
    if not (1 <= n <= 100) or not re.fullmatch(r"[A-Z]{1,6}", prefix):
        print("uso: gerar_carteiras_relic.py [1-100] [PREFIXO]")
        return 2

    try:
        from eth_account import Account
    except ImportError:
        print("falta o eth_account (vem com o web3). corre: .venv/bin/pip install web3")
        return 1

    if not ENV.exists():
        print(f"não encontrei {ENV}")
        return 1
    text = ENV.read_text()

    refs = [f"{prefix}{i:02d}" for i in range(1, n + 1)]
    # Nunca sobrescrever: uma chave substituída depois do mint deixa o relic
    # órfão — o troféu fica sem forma de ser transferido ao vencedor.
    existing = [r for r in refs if re.search(rf"^{r}_(ADDR|PK)=", text, re.M)]
    if existing:
        print(f"⛔ já existem no .env: {', '.join(existing)}")
        print("   Usa outro prefixo (ex.: RX) para um lote novo. Nada foi escrito.")
        return 1

    lines = text.splitlines()
    addresses: list[tuple[str, str]] = []
    for ref in refs:
        acct = Account.create()
        addresses.append((ref, acct.address))
        lines.append(f"{ref}_ADDR={acct.address}")
        lines.append(f"{ref}_PK={acct.key.hex()}")
    ENV.write_text("\n".join(lines) + "\n")

    print(f"\n✅ {n} carteiras escritas no .env (chaves privadas NÃO mostradas)\n")
    for ref, addr in addresses:
        print(f"  {ref}  {addr}")

    print(f"\nRELIC_WALLET_REFS={','.join(refs)}")
    print(
        "\nA seguir:\n"
        "  1. financia cada endereço com ~$0.50 em ETH na Base — valores DIFERENTES\n"
        "     e não redondos ($0.42, $0.61, $0.55…), senão o padrão agrupa-as\n"
        "  2. NÃO envies da hot wallet: manda da exchange, senão ligas as carteiras\n"
        "     de mint à carteira que paga os prémios e a camuflagem cai\n"
        "  3. copia os pares _ADDR/_PK do .env para o Doppler, mais a linha\n"
        "     RELIC_WALLET_REFS acima\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
