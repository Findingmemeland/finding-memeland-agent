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
