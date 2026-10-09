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
                              (target: a paste WITHOUT the chain, a link
                              nobody could resolve, an address that is not
                              one, a chain we do not read) — replied, NOT
                              counted as a guess: a format slip must not burn
                              one of the five attempts
  · format_kind(text)       — which format rule that was (our label, for the
                              'format' row in the log), or None. Its TYPE
                              (`format_type_of`) is what a player is answered
                              once for: one reply per type, not per profile
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

# ONE FORMAT REPLY PER TYPE (Pedro, 09/10). Until then it was one per profile
# for every kind of slip: a player who pasted the collection, was told so,
# and then sent the contract without its tokenId got silence the second
# time. The type is the reply the player READS — "no chain" and "no tokenId"
# are answered with the same words, so they are one type.
_FORMAT_TYPES = {"no_chain": "missing", "no_token_id": "missing"}


def format_type_of(kind: str | None) -> str:
    """The once-per-profile key for a format reply of this kind."""
    kind = (kind or "").strip()
    return _FORMAT_TYPES.get(kind, kind) or "format"


class ClaimMatcher(Protocol):
    def looks_like_claim(self, text: str) -> bool: ...
    def matches(self, text: str) -> bool: ...
    def submitted_label(self, text: str) -> str | None: ...
    def skip_judge(self, text: str) -> bool: ...
    def format_hint(self, text: str) -> str | None: ...
    def format_kind(self, text: str) -> str | None: ...
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

    def format_kind(self, text: str) -> str | None:
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
                 collection_reply: str = "",
                 bad_address_reply: Callable[..., str] | None = None,
                 other_chain_reply: str = ""):
        self._judge = judge
        self._resolve = resolve_link
        self._format_reply = format_reply
        self._unresolved_reply = unresolved_reply
        self._one_token_reply = one_token_reply
        # Opcional: sem ele, o caminho antigo ("não consigo ler") mantém-se.
        self._collection_reply = collection_reply
        # Optional too (09/10): without them these posts go where they went —
        # a near address to the humour judge, another chain's id to whatever
        # caught it. `bad_address_reply(bad_chars, length)` builds the text.
        self._bad_address_reply = bad_address_reply
        self._other_chain_reply = other_chain_reply
        self._readable_memo: dict[str, bool] = {}
        self._format_last: tuple[str, tuple[str, str] | None] | None = None

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
        case = self._format_case(text)
        return case[1] if case else None

    def format_kind(self, text: str) -> str | None:
        """WHICH format rule the post fell under — OUR label for it, logged
        with the 'format' row (09/10) so the replies can be counted by type.
        Never the post's own text. Asked right after `format_hint` for the
        same post, it costs nothing: an unreadable link is a resolver call,
        and the answer just given is kept (that one answer only — a later
        post with the same text is read again, as before)."""
        key = text or ""
        if self._format_last is not None and self._format_last[0] == key:
            case = self._format_last[1]
        else:
            case = self._format_case(key)
        return case[0] if case else None

    def _format_case(self, text: str) -> tuple[str, str] | None:
        """(kind, reply) for a post we could not turn into a token, or None."""
        case = self._classify_format(text or "")
        self._format_last = (text or "", case)
        return case

    def _unreadable(self, text: str) -> tuple[str, str] | None:
        """Two things we can name about a post we could not read, before the
        general rules (09/10). An address that is not one comes first: until
        it is fixed nothing else about the post can be read at all."""
        from ..target.claim import near_address, other_chain
        if self._bad_address_reply is not None:
            near = near_address(text)
            if near is not None:
                return ("bad_address",
                        self._bad_address_reply(near.bad_chars, near.length))
        if self._other_chain_reply and other_chain(text):
            return ("other_chain", self._other_chain_reply)
        return None

    def _classify_format(self, text: str) -> tuple[str, str] | None:
        from ..target.claim import claim_shaped
        ext = self._extract(text)
        if self._malformed(ext):
            return ("one_token", self._one_token_reply)
        if ext.refs:
            return None
        if ext.unresolved_links:
            v = self._judge.judge(text, resolve_link=self._resolve)
            if v.checked == 0 and v.unresolved:
                named = self._unreadable(text)
                if named is not None:
                    return named
                # Se o link traz contrato mas não traz token, sabemos o que
                # falta — e "não consigo ler esse link" seria uma verdade
                # inútil, a mandar a pessoa adivinhar o que já sabemos.
                # Dois jogadores do hunt #12 pararam na página da colecção.
                from ..target.claim import collection_link
                if (self._collection_reply
                        and all(collection_link(u) for u in ext.unresolved_links)):
                    return ("collection_link", self._collection_reply)
                return ("unreadable_link", self._unresolved_reply)
            return None
        # BEFORE the two rules below: an address one character short reads as
        # "a contract with no tokenId", and a chain we do not read in front
        # of a full triple reads the same way — both were answered "a claim
        # needs chain, contract AND tokenId", to a player who had sent all
        # three.
        named = self._unreadable(text)
        if named is not None:
            return named
        if claim_shaped(text):                     # contract:tokenId, no chain
            return ("no_chain", self._format_reply)
        if contract_paste_like(text):
            # A bare contract with no tokenId: someone naming a COLLECTION
            # and believing they claimed a token. Hunt #11 jeered at three
            # of these ("mechanical engagement"), and one of them had the
            # right piece. They are claiming — teach them.
            return ("no_token_id", self._format_reply)
        return None
