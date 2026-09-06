"""Claim matchers — what a claim IS, per hunt kind, behind one interface.

The claim loop (state_machine._claim_loop_body) used to know the shape of a
claim itself: a code of `len(hunt.claim_code)` characters, `code_like` as
the trigger, `hunt.claim_code in candidates` as the match. A target hunt
(Option A) claims with the token's IDENTITY — chain:contract:tokenId or a
marketplace link — so the loop now asks a matcher and stays shape-agnostic:

  · looks_like_claim(text)  — the TRIGGER: does this post attempt a claim
                              (guess cap, wrong-door, taunt routing)
  · matches(text)           — the MATCH: is it THE answer
  · submitted_label(text)   — what to log as the attempt (never secret)
  · skip_judge(text)        — jeer directly, no humour judge (wrong-shape)
  · format_hint(text)       — a public system reply teaching the format
                              (target: a paste WITHOUT the chain, or a link
                              nobody could resolve) — replied, NOT counted
                              as a guess: a format slip must not burn one of
                              the five attempts
  · spray_key(text)         — the CANONICAL identity of the guessed target
                              for the anti-spray detector, or None. Only a
                              parsed TargetRef qualifies (Opus, 06/09): the
                              detector's ratio is distinct/total, and if the
                              same piece counted as three "distinct targets"
                              (OpenSea link, Rarible link, explicit triple)
                              an honest crowd would look like a farm and be
                              paused exactly when the hunt is going well

CodeClaimMatcher reproduces the pre-existing behaviour exactly (the
claim-by-post tests are the proof); TargetClaimMatcher is the Option A
shape. Neither ever formats the answer into a reply.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .parser import code_like, contract_paste_like, extract_candidates, guess_like


class ClaimMatcher(Protocol):
    def looks_like_claim(self, text: str) -> bool: ...
    def matches(self, text: str) -> bool: ...
    def submitted_label(self, text: str) -> str | None: ...
    def skip_judge(self, text: str) -> bool: ...
    def format_hint(self, text: str) -> str | None: ...
    def spray_key(self, text: str) -> str | None: ...


class CodeClaimMatcher:
    """The persona/relic claim: an N-character code."""

    def __init__(self, claim_code: str):
        self._code = claim_code
        self._len = len(claim_code)

    def looks_like_claim(self, text: str) -> bool:
        # "matching is generous" (parser.py): the CORRECT code in any casing
        # must open the code path, or a lowercase-typed winner is chatter.
        return code_like(text, self._len) or self._code in extract_candidates(text, self._len)

    def matches(self, text: str) -> bool:
        return self._code in extract_candidates(text, self._len)

    def submitted_label(self, text: str) -> str | None:
        cands = extract_candidates(text, self._len)
        return cands[0] if cands else None

    def skip_judge(self, text: str) -> bool:
        return guess_like(text, self._len) or contract_paste_like(text)

    def format_hint(self, text: str) -> str | None:
        return None

    def spray_key(self, text: str) -> str | None:
        return None                      # the code game has no spray detector


class TargetClaimMatcher:
    """The Option A claim: chain:contract:tokenId, or a marketplace link.

    `judge` is a target.claim.ClaimJudge (holds the sealed id; verdicts are
    counts only); `resolve_link` is the production marketplace resolver for
    slug-only / chainless links (None in tests ⇒ such links are unresolved,
    which is a format hint, never a match)."""

    def __init__(self, *, judge, resolve_link: Callable[[str], object] | None = None,
                 format_reply: str, unresolved_reply: str, one_token_reply: str):
        self._judge = judge
        self._resolve = resolve_link
        self._format_reply = format_reply
        self._unresolved_reply = unresolved_reply
        self._one_token_reply = one_token_reply

    def _extract(self, text: str):
        from ..target.claim import extract_target_refs
        return extract_target_refs(text)

    def _malformed(self, ext) -> bool:
        from ..target.claim import MAX_TOKENS_PER_REPLY, tokens_named
        return tokens_named(ext) > MAX_TOKENS_PER_REPLY

    def looks_like_claim(self, text: str) -> bool:
        """The TRIGGER (guess cap, wrong door). A post naming more than one
        token is NOT a claim attempt — it is malformed (Opus, 06/09, P0):
        it goes to format_hint, costs no guess, can never match."""
        ext = self._extract(text)
        if self._malformed(ext):
            return False
        return bool(ext.refs or ext.unresolved_links)

    def matches(self, text: str) -> bool:
        return self._judge.judge(text, resolve_link=self._resolve).matched

    def submitted_label(self, text: str) -> str | None:
        ext = self._extract(text)
        if ext.refs:
            return ext.refs[0].id()
        if ext.unresolved_links:
            return ext.unresolved_links[0][:120]
        return None

    def skip_judge(self, text: str) -> bool:
        # a bare contract paste (no tokenId) is mechanical engagement — jeer
        return contract_paste_like(text) and not self.looks_like_claim(text)

    def spray_key(self, text: str) -> str | None:
        ext = self._extract(text)
        if self._malformed(ext) or not ext.refs:
            return None
        return ext.refs[0].id()

    def format_hint(self, text: str) -> str | None:
        from ..target.claim import claim_shaped
        ext = self._extract(text)
        if self._malformed(ext):
            return self._one_token_reply
        if ext.refs:
            return None
        if ext.unresolved_links:
            v = self._judge.judge(text, resolve_link=self._resolve)
            if v.checked == 0 and v.unresolved:
                return self._unresolved_reply
            return None
        if claim_shaped(text):                     # contract:tokenId, no chain
            return self._format_reply
        return None
