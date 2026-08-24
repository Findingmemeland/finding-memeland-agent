"""Contar carteiras únicas que já interagiram com o $FIND.

    .venv/bin/python contar_carteiras.py
    .venv/bin/python contar_carteiras.py 48000000      # bloco inicial explícito

Varre os eventos Transfer do token na cadeia e conta endereços distintos. É o
número que o jesseXBT pediu — "wallets that interacted", não só vencedores — e é
verificável por qualquer pessoa, que é o que o torna útil numa candidatura.

Distingue três coisas, porque significam coisas diferentes:
  · TOCARAM        — alguma vez enviaram ou receberam $FIND (o número grande)
  · DETÊM AGORA    — saldo > 0 neste momento (o que o BaseScan mostra)
  · RECEBERAM DO   — carteiras que receberam directamente da hot wallet
    PROJECTO         (prémios), útil para separar prémios de mercado

O mint (from 0x0) não conta como carteira: é criação, não interacção.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

ZERO = "0x0000000000000000000000000000000000000000"
# keccak("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DEFAULT_FROM_BLOCK = 48_000_000     # antes do primeiro hunt pago; ajustável


def main() -> int:
    from web3 import Web3

    from finding_memeland.config import Settings

    s = Settings()

    # Argumentos em qualquer ordem: 0x… é o token, um número é o bloco inicial.
    # O .env local tem um placeholder no FMML_TOKEN_ADDRESS (o valor real vive
    # só no Doppler), por isso o endereço tem de poder vir por argumento.
    token_arg = next((a for a in sys.argv[1:] if a.lower().startswith("0x")), "")
    block_arg = next((a for a in sys.argv[1:] if a.isdigit()), "")

    raw = token_arg or s.fmml_token_address
    if not raw or not raw.strip().lower().startswith("0x"):
        print(
            "endereço do token em falta ou inválido.\n"
            f"  no .env está: {raw!r}\n"
            "  passa-o como argumento:\n"
            "    .venv/bin/python contar_carteiras.py 0xTOKEN [bloco_inicial]"
        )
        return 1

    w3 = Web3(Web3.HTTPProvider(s.base_rpc_url))
    token = Web3.to_checksum_address(raw.strip())
    latest = w3.eth.block_number
    start = int(block_arg) if block_arg else DEFAULT_FROM_BLOCK

    print(f"\ntoken {token}")
    print(f"blocos {start} → {latest}  ({latest - start:,} blocos)\n")

    touched: set[str] = set()
    senders: set[str] = set()
    receivers: set[str] = set()
    transfers = 0

    # Chunk adaptativo: os RPCs públicos limitam o intervalo do eth_getLogs e o
    # limite não é o mesmo em todos. Começa optimista e reduz quando se queixam,
    # em vez de assumir um valor e falhar a meio de uma varredura longa.
    chunk = 50_000
    block = start
    t0 = time.time()
    while block <= latest:
        end = min(block + chunk - 1, latest)
        try:
            logs = w3.eth.get_logs({
                "address": token,
                "topics": [TRANSFER_TOPIC],
                "fromBlock": block,
                "toBlock": end,
            })
        except Exception as e:  # noqa: BLE001
            if chunk > 1_000:
                chunk //= 4
                print(f"  (intervalo reduzido para {chunk} blocos: {str(e)[:60]})")
                continue
            print(f"⛔ falhou mesmo com intervalo pequeno: {e!r}")
            return 1

        for log in logs:
            transfers += 1
            frm = "0x" + log["topics"][1].hex()[-40:]
            to = "0x" + log["topics"][2].hex()[-40:]
            if frm.lower() != ZERO:
                senders.add(frm.lower())
                touched.add(frm.lower())
            if to.lower() != ZERO:
                receivers.add(to.lower())
                touched.add(to.lower())

        pct = (end - start) / max(1, latest - start) * 100
        print(f"  {pct:5.1f}%  bloco {end:,}  ·  {len(touched):,} carteiras  ·  {transfers:,} transfers")
        block = end + 1

    hot = ""
    if s.hot_wallet_private_key:
        try:
            hot = w3.eth.account.from_key(s.hot_wallet_private_key).address.lower()
        except Exception:  # noqa: BLE001
            pass

    print("\n" + "=" * 56)
    print(f"CARTEIRAS QUE TOCARAM NO $FIND: {len(touched):,}")
    print(f"  · receberam:  {len(receivers):,}")
    print(f"  · enviaram:   {len(senders):,}")
    print(f"  · transfers:  {transfers:,}")
    if hot:
        print(f"\n(hot wallet do projecto excluída da contagem: {hot[:10]}…)")
        touched.discard(hot)
        print(f"CARTEIRAS EXTERNAS: {len(touched):,}")
    print(f"\nvarrido em {time.time() - t0:.0f}s")
    print("=" * 56 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
