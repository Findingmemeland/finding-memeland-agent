"""DM Validator — the 4-filter eligibility check.

Validation order is chosen by COST (X API is pay-per-use):
    1. claim code match         — free (string compare)
    2. holding on-chain         — free (Base RPC, our node)
    3. public reshare of Clue 1 — PAID (X API lookup)
    4. bot defences             — mostly free signals + the bright-line rule

The first DM that passes all four, by arrival order (x_created_at, ms), wins.
'Humans win, agents help': the only objective disqualifier is an account that
PUBLICLY identifies as a bot/agent (or our own accounts). Covert farms are caught
by behavioural signals + manual review of the leading candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A wallet address anywhere in the DM body.
WALLET_RE = re.compile(r"0x[a-fA-F0-9]{40}")

# Standalone bot/agent tokens. "ai" is intentionally NOT matched in free-text bio
# (too many false positives like "I love AI"); it is matched in name/handle.
_BOT_TOKENS = re.compile(r"\b(bot|agent|gpt|autobot|automated)\b", re.IGNORECASE)
_AI_TOKEN = re.compile(r"\b(a\.?i\.?)\b", re.IGNORECASE)
# Explicit self-identification phrases checked in the bio.
_BIO_BOT_PHRASES = re.compile(
    r"(i am a bot|this is a bot|bot account|automated account|ai agent|i'?m an ai)",
    re.IGNORECASE,
)


@dataclass
class ParsedDM:
    dm_id: str
    sender_x_id: str
    wallet: str | None
    claim_code: str | None
    # ALL tokens of the expected length found in the body (uppercased, deduped,
    # in order). The validator accepts if ANY of them matches the hunt's code —
    # otherwise an innocent 8-letter word ("personal", "treasure") before the
    # real code would shadow it and unfairly reject a legitimate winner.
    claim_candidates: tuple[str, ...] = ()


@dataclass
class ValidationResult:
    won: bool
    outcome: str           # matches submission_outcome enum
    check_code: bool = False
    check_holding: bool = False
    check_reshare: bool = False
    check_bot: bool = False
    bot_reason: str | None = None


def _normalize_wallet(raw: str) -> str | None:
    """EIP-55 checksum validation at PARSE time (not at pay time, when it's too
    late — the winner is already declared).

    - all-lowercase / all-uppercase hex: no checksum encoded -> accept (payout's
      to_checksum_address converts these safely).
    - mixed-case: a checksum IS claimed -> verify it. A bad checksum means a
      typo'd address; paying it would send the prize into the void. Reject ->
      the DM parses as wallet-less -> the player gets the 'send your wallet
      address' reply and can correct it without losing their claim.
    """
    body = raw[2:]
    if body == body.lower() or body == body.upper():
        return raw  # no checksum claimed; to_checksum_address converts safely
    try:
        from eth_utils import is_checksum_address
    except ImportError:  # verification unavailable; pay-time conversion still guards
        return raw
    return raw if is_checksum_address(raw) else None


def parse_dm(dm_id: str, sender_x_id: str, body: str, expected_code_len: int = 8) -> ParsedDM:
    wallet_match = WALLET_RE.search(body or "")
    wallet = _normalize_wallet(wallet_match.group(0)) if wallet_match else None
    candidates: list[str] = []
    for tok in re.findall(r"\b[A-Za-z0-9]+\b", body or ""):
        if tok.lower().startswith("0x"):
            continue
        if len(tok) == expected_code_len:
            up = tok.upper()
            if up not in candidates:
                candidates.append(up)
    code = candidates[0] if candidates else None
    return ParsedDM(
        dm_id=dm_id, sender_x_id=sender_x_id, wallet=wallet,
        claim_code=code, claim_candidates=tuple(candidates),
    )


def screen_bot(
    *,
    display_name: str,
    handle: str,
    bio: str,
    automated_label: bool,
    own_handles: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str | None]:
    """Bright-line disclosed-bot screen. Returns (ok, reason_if_blocked).

    ok=True means the account is eligible (not a disclosed bot / not ours).
    Ambiguous covert automation is NOT decided here — that goes to manual review.
    """
    h = handle.lstrip("@").lower()
    if h in {x.lstrip("@").lower() for x in own_handles}:
        return (False, "Finding Memeland's own account")
    if automated_label:
        return (False, "carries X's Automated label")
    # name/handle: normalize separators (_, digits) to spaces so "@trade_bot"
    # and "ai_solver" are caught, then look for bot/agent/gpt or the AI token.
    for field, val in (("display name", display_name), ("handle", handle)):
        norm = _norm(val)
        if _BOT_TOKENS.search(norm) or _AI_TOKEN.search(norm):
            return (False, f"self-identifies as bot/agent in {field}")
    # bio: bot/agent tokens or explicit phrases (not the bare "ai", too noisy)
    if _BOT_TOKENS.search(_norm(bio)) or _BIO_BOT_PHRASES.search(bio or ""):
        return (False, "self-identifies as bot/agent in bio")
    return (True, None)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]+", " ", (s or "").lower())


class DMValidator:
    def __init__(self, *, chain, x_client, profile_lookup, own_handles=()):
        self._chain = chain                     # chain.holdings interface
        self._x = x_client                      # social.x_client interface
        self._profile_lookup = profile_lookup   # callable(x_id) -> dict
        self._own_handles = tuple(own_handles)

    def validate(self, dm: ParsedDM, hunt) -> ValidationResult:
        # 0. Need an address at all.
        if not dm.wallet:
            return ValidationResult(False, "malformed")

        # 1. Claim code (free). Accept if ANY extracted candidate matches, so an
        # unrelated 8-char word earlier in the message can't shadow the real code.
        candidates = dm.claim_candidates or (
            (dm.claim_code,) if dm.claim_code else ()
        )
        if hunt.claim_code not in candidates:
            return ValidationResult(False, "bad_code")

        # 2. Holding floor + continuity (free, our RPC).
        if not self._chain.has_continuous_holding(
            wallet=dm.wallet,
            min_balance=hunt.min_balance_fmml,
            holding_hours=hunt.holding_hours,
        ):
            return ValidationResult(False, "no_holding", check_code=True)

        # 3. Public reshare of Clue 1 (PAID — only reached if 1 & 2 pass).
        if not self._x.has_reshared(user_id=dm.sender_x_id, post_id=hunt.reshare_post_id):
            return ValidationResult(
                False, "no_reshare", check_code=True, check_holding=True
            )

        # 4. Bot defences — bright-line public self-identification.
        bot_ok, reason = self._bot_check(dm.sender_x_id)
        if not bot_ok:
            return ValidationResult(
                False, "bot_disqualified", check_code=True,
                check_holding=True, check_reshare=True, bot_reason=reason,
            )

        return ValidationResult(
            True, "won", check_code=True, check_holding=True,
            check_reshare=True, check_bot=True,
        )

    def _bot_check(self, sender_x_id: str) -> tuple[bool, str | None]:
        p = self._profile_lookup(sender_x_id) or {}
        return screen_bot(
            display_name=p.get("name", ""),
            handle=p.get("handle", ""),
            bio=p.get("bio", ""),
            automated_label=bool(p.get("automated", False)),
            own_handles=self._own_handles,
        )
