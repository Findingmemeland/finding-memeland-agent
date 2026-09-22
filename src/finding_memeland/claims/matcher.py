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
    # is_malformed(text) — the post is a REFUSED claim attempt (>1 token), to
    # be LOGGED (outcome 'malformed') even though it spends no guess (Opus
    # audit 09/09, P1-1: the highest-volume attack shape left no trace).
    def is_malformed(self, text: str) -> bool: ...


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

    def is_malformed(self, text: str) -> bool:
        return False

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
                 format_reply: str, unresolved_reply: str, one_token_reply: str,
                 collection_reply: str = ""):
        self._judge = judge
        self._resolve = resolve_link
        self._format_reply = format_reply
        self._unresolved_reply = unresolved_reply
        self._one_token_reply = one_token_reply
        # Opcional: sem ele, o caminho antigo ("não consigo ler") mantém-se.
        self._collection_reply = collection_reply
        self._readable_memo: dict[str, bool] = {}

    def _extract(self, text: str):
        from ..target.claim import extract_target_refs
        return extract_target_refs(text)

    def _malformed(self, ext) -> bool:
        from ..target.claim import MAX_TOKENS_PER_REPLY, tokens_named
        return tokens_named(ext) > MAX_TOKENS_PER_REPLY

    def looks_like_claim(self, text: str) -> bool:
        """The TRIGGER (guess cap, wrong door). A post naming more than one
        token is NOT a claim attempt — it is malformed (Opus, 06/09, P0):
        it goes to format_hint, costs no guess, can never match.

        A LINK ONLY COUNTS ONCE WE CAN TURN IT INTO A TOKEN (17/09). Saying
        yes to every link would spend a guess on something we cannot even
        read and answer it with a jeer — which is the Hunt #11 failure
        wearing a new hat. Unreadable links fall through to format_hint,
        cost nothing, and get taught. The resolve is memoised so the judge
        is not asked twice for the same post."""
        ext = self._extract(text)
        if self._malformed(ext):
            return False
        if ext.refs:
            return True
        if not ext.unresolved_links:
            return False
        return self._readable(text)

    def _readable(self, text: str) -> bool:
        """Can the resolver turn this post's links into a token? Memoised
        per post: `looks_like_claim` and `format_hint` both ask."""
        key = text or ""
        if key in self._readable_memo:
            return self._readable_memo[key]
        try:
            ok = self._judge.judge(key, resolve_link=self._resolve).checked > 0
        except Exception:  # noqa: BLE001 — unreadable, which is the safe side
            ok = False
        if len(self._readable_memo) > 512:          # one hunt, bounded
            self._readable_memo.clear()
        self._readable_memo[key] = ok
        return ok

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
        # A bare contract paste now gets the FORMAT (see format_hint) and
        # never reaches the jeer, so nothing is left to skip the judge for.
        # Kept so the port stays the same shape for the code matcher.
        return False

    def spray_key(self, text: str) -> str | None:
        ext = self._extract(text)
        if self._malformed(ext) or not ext.refs:
            return None
        return ext.refs[0].id()

    def is_malformed(self, text: str) -> bool:
        return self._malformed(self._extract(text))

    def tokens_named(self, text: str) -> int:
        from ..target.claim import tokens_named
        return tokens_named(self._extract(text))

    def format_hint(self, text: str) -> str | None:
        """The public system reply that TEACHES the format. Never a guess,
        never a verdict.

        THE RULE THIS SERVES (Pedro, 17/09, after Hunt #11): never tell a
        player they are wrong when they might be right. Everything here is
        a post we could not turn into a token — and not being able to read
        it is OUR limit. The player hears what to send instead."""
        from ..target.claim import claim_shaped
        ext = self._extract(text)
        if self._malformed(ext):
            return self._one_token_reply
        if ext.refs:
            return None
        if ext.unresolved_links:
            v = self._judge.judge(text, resolve_link=self._resolve)
            if v.checked == 0 and v.unresolved:
                # Se o link traz contrato mas não traz token, sabemos o que
                # falta — e "não consigo ler esse link" seria uma verdade
                # inútil, a mandar a pessoa adivinhar o que já sabemos.
                # Dois jogadores do hunt #12 pararam na página da colecção.
                from ..target.claim import collection_link
                if (self._collection_reply
                        and all(collection_link(u) for u in ext.unresolved_links)):
                    return self._collection_reply
                return self._unresolved_reply
            return None
        if claim_shaped(text):                     # contract:tokenId, no chain
            return self._format_reply
        if contract_paste_like(text):
            # A bare contract with no tokenId: someone naming a COLLECTION
            # and believing they claimed a token. Hunt #11 jeered at three
            # of these ("mechanical engagement"), and one of them had the
            # right piece. They are claiming — teach them.
            return self._format_reply
        return None
