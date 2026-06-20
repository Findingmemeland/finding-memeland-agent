"""DM Validator — the 4-filter eligibility check.

Validation order is chosen by COST (X API is pay-per-use):
    1. claim code match        — free (string compare)
    2. holding on-chain        — free (Base RPC, our node)
    3. public reshare of Clue 1 — PAID (X API timeline lookup)
    4. bot defences            — mostly free signals + the bright-line rule

The first DM that passes all four, by arrival order (x_created_at, ms), wins.
'Humans win, agents help': the only objective disqualifier is an account that
PUBLICLY identifies as a bot/agent. Covert farms are caught by behavioural
signals + manual review of the leading candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A wallet address anywhere in the DM body.
WALLET_RE = re.compile(r"0x[a-fA-F0-9]{40}")

# Public self-identification as a bot/agent (display name / handle / bio).
BOT_SELF_ID_RE = re.compile(r"\b(bot|agent|a\.?i\.?|gpt|autobot)\b", re.IGNORECASE)


@dataclass
class ParsedDM:
    dm_id: str
    sender_x_id: str
    wallet: str | None
    claim_code: str | None


@dataclass
class ValidationResult:
    won: bool
    outcome: str           # matches submission_outcome enum
    check_code: bool = False
    check_holding: bool = False
    check_reshare: bool = False
    check_bot: bool = False
    bot_reason: str | None = None


def parse_dm(dm_id: str, sender_x_id: str, body: str, expected_code_len: int = 8) -> ParsedDM:
    wallet_match = WALLET_RE.search(body or "")
    wallet = wallet_match.group(0) if wallet_match else None
    # Claim code = an alnum token of the expected length that isn't the address.
    code = None
    for tok in re.findall(r"\b[A-Za-z0-9]+\b", body or ""):
        if tok.lower().startswith("0x"):
            continue
        if len(tok) == expected_code_len:
            code = tok.upper()
            break
    return ParsedDM(dm_id=dm_id, sender_x_id=sender_x_id, wallet=wallet, claim_code=code)


class DMValidator:
    def __init__(self, *, chain, x_client, sender_profile_lookup):
        self._chain = chain                     # chain.holdings interface
        self._x = x_client                      # social.x_client interface
        self._profile = sender_profile_lookup   # callable(x_id) -> profile dict

    def validate(self, dm: ParsedDM, hunt) -> ValidationResult:
        # 0. Need an address at all.
        if not dm.wallet:
            return ValidationResult(False, "malformed")

        # 1. Claim code (free).
        if not dm.claim_code or dm.claim_code != hunt.claim_code:
            return ValidationResult(False, "bad_code")
        code_ok = True

        # 2. Holding floor + continuity (free, our RPC).
        holding_ok = self._chain.has_continuous_holding(
            wallet=dm.wallet,
            min_balance=hunt.min_balance_fmml,
            holding_hours=hunt.holding_hours,
        )
        if not holding_ok:
            return ValidationResult(False, "no_holding", check_code=code_ok)

        # 3. Public reshare of Clue 1 (PAID — only reached if 1 & 2 pass).
        reshare_ok = self._x.has_reshared(
            user_id=dm.sender_x_id, post_id=hunt.reshare_post_id
        )
        if not reshare_ok:
            return ValidationResult(
                False, "no_reshare", check_code=code_ok, check_holding=True
            )

        # 4. Bot defences — bright-line public self-identification + signals.
        bot_ok, reason = self._bot_check(dm.sender_x_id)
        if not bot_ok:
            return ValidationResult(
                False, "bot_disqualified", check_code=code_ok,
                check_holding=True, check_reshare=True, bot_reason=reason,
            )

        return ValidationResult(
            True, "won", check_code=True, check_holding=True,
            check_reshare=True, check_bot=True,
        )

    def _bot_check(self, sender_x_id: str) -> tuple[bool, str | None]:
        """Bright-line rule + soft signals. Returns (ok, reason_if_failed).

        TODO(step 26): fetch profile via self._profile; disqualify if:
          (a) 'bot'/'agent'/'AI' in display name/handle/bio (BOT_SELF_ID_RE),
          (b) X 'Automated' label present,
          (c) registered agent on Virtuals/Bankr (best-effort),
          (d) our own main/persona accounts.
        Soft signals (account age, organic history, submission latency) feed a
        manual-review hold rather than an auto-fail.
        """
        raise NotImplementedError("bot check — implemented in step 26")
