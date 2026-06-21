"""Validate the on-chain payout path on a TESTNET (Base Sepolia).

⚠️  This sends REAL transactions on whatever network BASE_RPC_URL points to. Only
run it against a TESTNET RPC with a throwaway wallet. The script refuses Base
mainnet (chain id 8453).

Two modes, chosen automatically:
  • ERC-20 mode  — if FMML_TOKEN_ADDRESS + PAYOUT_CAP_FMML are set: does a real
    prize transfer via PayoutEngine (needs a test ERC-20 the EOA holds).
  • ETH smoke    — otherwise: a 0-ETH self-send that validates the full signing/
    sending plumbing with only faucet ETH for gas. The cheapest way to de-risk
    the on-chain path before a token exists.

Prerequisites (.env):
  BASE_RPC_URL           -> Base Sepolia RPC (e.g. https://sepolia.base.org)
  HOT_WALLET_PRIVATE_KEY -> throwaway test EOA, funded with Sepolia ETH for gas
  (ERC-20 mode also: FMML_TOKEN_ADDRESS, PAYOUT_CAP_FMML)

Usage:
  python scripts/test_payout.py [recipient_0x] [whole_token_amount]
"""

from __future__ import annotations

import sys

from web3 import Web3

from finding_memeland.chain.payout import PayoutEngine
from finding_memeland.config import get_settings


def _eth_smoke(w3: Web3, key: str, chain_id: int) -> int:
    sender = w3.eth.account.from_key(key).address
    print(f"hot wallet : {sender}")
    bal = w3.eth.get_balance(sender)
    print(f"ETH balance: {w3.from_wei(bal, 'ether')} (need a little for gas)")
    if bal == 0:
        print("FAIL — wallet has no Sepolia ETH. Get some from a Base Sepolia faucet.")
        return 1
    tx = {
        "to": sender, "value": 0, "nonce": w3.eth.get_transaction_count(sender),
        "gas": 21000, "gasPrice": w3.eth.gas_price, "chainId": chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=key)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h)
    ok = r.status == 1
    print(f"\n{'PASS' if ok else 'FAIL'} — ETH smoke test tx: {h.hex()} (status {r.status})")
    print("On-chain signing/sending plumbing works." if ok else "Transaction reverted.")
    return 0 if ok else 1


def main() -> int:
    s = get_settings()
    if not s.base_rpc_url or not s.hot_wallet_private_key:
        print("FAIL — set BASE_RPC_URL and HOT_WALLET_PRIVATE_KEY in .env.")
        return 2

    w3 = Web3(Web3.HTTPProvider(s.base_rpc_url))
    if not w3.is_connected():
        print(f"FAIL — cannot connect to RPC {s.base_rpc_url}")
        return 1
    chain_id = w3.eth.chain_id
    if chain_id == 8453:
        print("REFUSING — chain id 8453 is Base MAINNET. Use a testnet RPC.")
        return 1
    print(f"connected. chain id = {chain_id} (Base Sepolia = 84532)")

    # ETH smoke mode if no test token configured.
    if not s.fmml_token_address or not s.payout_cap_fmml:
        print("no FMML_TOKEN_ADDRESS/PAYOUT_CAP_FMML -> running ETH smoke test\n")
        return _eth_smoke(w3, s.hot_wallet_private_key, chain_id)

    # ERC-20 payout mode.
    engine = PayoutEngine(
        web3=w3, token_address=s.fmml_token_address,
        hot_wallet_key=s.hot_wallet_private_key, per_hunt_cap=int(s.payout_cap_fmml),
    )
    sender = w3.eth.account.from_key(s.hot_wallet_private_key).address
    recipient = sys.argv[1] if len(sys.argv) > 1 else sender
    amount = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    print(f"hot wallet : {sender}\nrecipient  : {recipient}\namount     : {amount} tokens")
    print(f"sender balance before : {engine.balance_of(sender)} tokens")
    try:
        result = engine.send_prize(hunt_id=0, to_wallet=recipient, amount_fmml=amount)
    except Exception as e:  # noqa: BLE001
        print(f"\nFAIL — payout error: {e!r}")
        return 1
    print(f"\nPASS — transfer mined. tx: {result.tx_hash}")
    print(f"sender balance after  : {engine.balance_of(sender)} tokens")
    if recipient != sender:
        print(f"recip balance after   : {engine.balance_of(recipient)} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
