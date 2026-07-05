"""Pre-flight 'services alive' check, run before a hunt launches.

It does NOT check credit *balances* — Anthropic, OpenAI and X expose no reliable
balance API, so a "low credits" meter isn't feasible. Money running out is
handled by enabling AUTO-RECHARGE on each platform.

What this DOES catch, cheaply, is the catastrophic case: a revoked/invalid key,
a suspended account, or an API that's down/hard-rate-limited — so we never fire a
public hunt into a broken service and kill it mid-way. Each check is one tiny
call; on failure it returns a human-readable problem string (shown on Telegram).
"""

from __future__ import annotations


def preflight_check(*, anthropic=None, anthropic_model: str = "", openai=None, x=None) -> list[str]:
    """Return a list of problems (empty = all good). None clients are skipped."""
    problems: list[str] = []

    if anthropic is not None:
        try:
            anthropic.messages.create(
                model=anthropic_model or "claude-sonnet-4-6",
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
        except Exception as e:  # noqa: BLE001
            problems.append(f"Anthropic (Claude): {type(e).__name__}: {e}")

    if openai is not None:
        try:
            openai.models.list()  # free, confirms the key/account is live
        except Exception as e:  # noqa: BLE001
            problems.append(f"OpenAI: {type(e).__name__}: {e}")

    if x is not None:
        try:
            x.verify()  # cheap get_me on the main account
        except Exception as e:  # noqa: BLE001
            problems.append(f"X API: {type(e).__name__}: {e}")

    return problems


# Rough Base L2 gas cushion for one ERC-20 transfer + margin. Deliberately
# generous — if the hot wallet can't cover this, don't launch.
MIN_GAS_ETH = 0.0002


def preflight_money(*, web3=None, payout=None, hot_address: str = "", prize_fmml: int = 0) -> list[str]:
    """The money checks the service preflight can't do: RPC alive, hot wallet
    has gas, hot wallet holds >= the prize. Run before /launch so the failure
    surfaces NOW — not at pay time, after a winner has been declared."""
    problems: list[str] = []
    if web3 is None:
        return problems

    try:
        web3.eth.block_number  # is the RPC alive at all?
    except Exception as e:  # noqa: BLE001
        return [f"Base RPC unreachable: {type(e).__name__}: {e}"]

    if hot_address:
        try:
            gas_wei = web3.eth.get_balance(hot_address)
            if gas_wei < int(MIN_GAS_ETH * 1e18):
                problems.append(
                    f"hot wallet gas: {gas_wei / 1e18:.6f} ETH < {MIN_GAS_ETH} ETH "
                    "needed to pay the winner's transfer"
                )
        except Exception as e:  # noqa: BLE001
            problems.append(f"gas balance check failed: {type(e).__name__}: {e}")

    if payout is not None and hot_address and prize_fmml > 0:
        try:
            tokens = payout.balance_of(hot_address)
            if tokens < prize_fmml:
                problems.append(
                    f"hot wallet holds {tokens:,} $FIND < the {prize_fmml:,} prize"
                )
        except Exception as e:  # noqa: BLE001
            problems.append(f"token balance check failed: {type(e).__name__}: {e}")

    return problems
