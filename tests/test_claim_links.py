"""A link is the main way to claim — from any site, and never a jeer.

EVERY TEST HERE IS HUNT #11 (17/09), measured live and paid for.

X shortens every URL in a tweet's `text` to t.co and keeps the real one in
`entities.urls[].expanded_url`, which the agent never asked for. So an
OpenSea link reached the claim parser as "https://t.co/aBcD1234" — no
contract, no tokenId, no chain. Not a claim, not even an unreadable link:
noise. The player got a joke about being wrong. Every link claim of that
hunt died this way, in silence, for two hours, while the pinned rules
promised "or just paste the marketplace link". The winner got through by
typing chain:contract:tokenId by hand, on his second attempt, with no help
from us.

The rule that came out of it (Pedro): NEVER tell a player they are wrong
when they might be right. A post we cannot read is our limit, not their
mistake — it costs no guess and the answer teaches the format.
"""
from __future__ import annotations

from finding_memeland.claims.matcher import TargetClaimMatcher
from finding_memeland.social.x_client import _expanded_text
from finding_memeland.target.claim import extract_target_refs
from finding_memeland.target.templates import (
    POST_REPLY_FORMAT,
    POST_REPLY_ONE_TOKEN,
    POST_REPLY_UNRESOLVED_LINK,
)

C = "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"
TARGET = f"ethereum:{C}:11385"
OPENSEA = f"https://opensea.io/item/ethereum/{C}/11385"


class _Tweet:
    def __init__(self, text, entities=None):
        self.text = text
        self.entities = entities
        self.id = "1"
        self.author_id = "9"
        self.created_at = None


# --------------------------------------------------------------------------- #
# The bug itself: t.co                                                         #
# --------------------------------------------------------------------------- #


def test_a_shortened_link_is_put_back_before_anyone_reads_it():
    """THE Hunt #11 bug. Without this the claim parser sees a t.co and the
    player sees a joke."""
    t = _Tweet("Aeth hopper https://t.co/aBcD1234",
               {"urls": [{"url": "https://t.co/aBcD1234",
                          "expanded_url": OPENSEA}]})
    assert _expanded_text(t) == f"Aeth hopper {OPENSEA}"
    assert extract_target_refs(_expanded_text(t)).refs[0].id() == TARGET


def test_expansion_survives_missing_or_partial_entities():
    """Worse than before must not be possible: no entities, an empty list,
    a url without its expansion — the text passes through untouched."""
    assert _expanded_text(_Tweet("plain text")) == "plain text"
    assert _expanded_text(_Tweet("x", {})) == "x"
    assert _expanded_text(_Tweet("x", {"urls": []})) == "x"
    assert _expanded_text(_Tweet("x https://t.co/a",
                                 {"urls": [{"url": "https://t.co/a"}]})) == \
        "x https://t.co/a"


def test_several_links_are_all_expanded():
    t = _Tweet("a https://t.co/1 and b https://t.co/2",
               {"urls": [{"url": "https://t.co/1", "expanded_url": "https://one.example/x"},
                         {"url": "https://t.co/2", "expanded_url": "https://two.example/y"}]})
    out = _expanded_text(t)
    assert "one.example" in out and "two.example" in out and "t.co" not in out


# --------------------------------------------------------------------------- #
# Any link, from any site                                                      #
# --------------------------------------------------------------------------- #


def test_a_link_from_a_site_we_never_heard_of_is_unreadable_not_noise():
    """A host we do not recognise is OUR gap. It becomes an unreadable
    link — which costs no guess and earns the format — instead of reading
    as plain chatter, which is what earned a jeer."""
    ext = extract_target_refs("https://some-new-marketplace.xyz/piece/aeth-hopper")
    assert ext.refs == ()
    assert ext.unresolved_links == ("https://some-new-marketplace.xyz/piece/aeth-hopper",)


def test_an_unexpanded_tco_still_counts_as_a_link_not_as_chatter():
    """The belt to the brace: if entities ever go missing again, a bare
    t.co must STILL be treated as a link we could not read."""
    ext = extract_target_refs("https://t.co/aBcD1234")
    assert ext.unresolved_links == ("https://t.co/aBcD1234",)


def test_the_marketplaces_all_resolve_to_the_same_token():
    """Measured against the piece that was actually won."""
    for url in (
        f"https://opensea.io/item/ethereum/{C}/11385",
        f"https://opensea.io/assets/ethereum/{C}/11385",
        f"https://rarible.com/token/ethereum/{C}:11385",
        f"https://rarible.com/items/ETHEREUM:{C}:11385",
        f"https://superrare.com/artwork/eth/{C}/11385",
    ):
        refs = extract_target_refs(url).refs
        assert refs and refs[0].id() == TARGET, url


# --------------------------------------------------------------------------- #
# Never "wrong" for something we could not read                                #
# --------------------------------------------------------------------------- #


class _Judge:
    """The real judge's shape: `checked` counts what it could evaluate."""

    def __init__(self, resolvable=False):
        self._resolvable = resolvable

    def judge(self, text, resolve_link=None):
        readable = bool(self._resolvable and extract_target_refs(text).unresolved_links)
        return type("V", (), {"matched": False,
                              "checked": 1 if readable else 0,
                              "unresolved": not readable})()


def _matcher(resolvable=False):
    return TargetClaimMatcher(
        judge=_Judge(resolvable), resolve_link=None,
        format_reply=POST_REPLY_FORMAT,
        unresolved_reply=POST_REPLY_UNRESOLVED_LINK,
        one_token_reply=POST_REPLY_ONE_TOKEN)


def test_an_unreadable_link_teaches_the_format_and_never_jeers():
    m = _matcher()
    hint = m.format_hint("https://t.co/aBcD1234")
    assert hint == POST_REPLY_UNRESOLVED_LINK
    assert "cost you nothing" in hint
    assert m.skip_judge("https://t.co/aBcD1234") is False


def test_a_bare_contract_teaches_instead_of_being_mocked():
    """Hunt #11 answered three of these with "mechanical engagement" jeers.
    One of them held the right piece. Someone pasting a contract is
    claiming — badly — and the honest answer is the missing tokenId."""
    m = _matcher()
    text = f"DickButt\nChain: Base\nContract ID: {C}"
    assert m.format_hint(text) == POST_REPLY_FORMAT
    assert m.skip_judge(text) is False         # never the direct jeer


def test_a_readable_link_gets_no_hint_and_goes_to_the_judge():
    """Teaching must not swallow a real claim."""
    m = _matcher(resolvable=True)
    assert m.format_hint("https://rarible.com/token/" + C + ":11385") is None


def test_a_link_that_resolves_is_a_claim_that_spends_a_guess():
    m = _matcher()
    assert m.looks_like_claim(OPENSEA) is True
    assert m.format_hint(OPENSEA) is None
    assert m.submitted_label(OPENSEA) == TARGET


def test_a_link_we_cannot_read_never_spends_a_guess():
    """The trap my own first draft fell into: making EVERY link a claim
    attempt sends the unreadable ones down the guess path, where they cost
    an attempt and come back as a jeer — the Hunt #11 failure wearing a new
    hat. A link is only an attempt once we can turn it into a token."""
    m = _matcher(resolvable=False)
    assert m.looks_like_claim("https://t.co/aBcD1234") is False
    assert m.format_hint("https://t.co/aBcD1234") == POST_REPLY_UNRESOLVED_LINK


def test_a_link_we_can_read_is_a_claim_and_does_spend_one():
    m = _matcher(resolvable=True)
    assert m.looks_like_claim(f"https://rarible.com/token/{C}:11385") is True


def test_a_blog_link_in_the_thread_is_not_claim_shaped():
    """Extraction keeps every url — we would rather try and fail than
    ignore. But "might resolve" is not "looks like a claim": chatter with
    a link must not be dragged into the guess cap."""
    from finding_memeland.target.claim import claim_shaped
    assert claim_shaped("https://example.org/blog/42") is False
    assert claim_shaped(OPENSEA) is True
    assert claim_shaped(f"https://rarible.com/token/{C}:11385") is True


def test_two_tokens_in_one_reply_is_still_malformed():
    """The one rule that does NOT soften: a post naming several tokens can
    never match, costs no guess, and says so."""
    m = _matcher()
    text = f"{OPENSEA} or maybe https://opensea.io/item/ethereum/{C}/999"
    assert m.is_malformed(text) is True
    assert m.format_hint(text) == POST_REPLY_ONE_TOKEN
    assert m.looks_like_claim(text) is False


# --------------------------------------------------------------------------- #
# O link do Rarible SEM cadeia — o que eu julguei partido e não está           #
# --------------------------------------------------------------------------- #


def test_a_chainless_marketplace_link_is_resolved_not_guessed():
    """22/09: diagnostiquei isto como avaria depois de correr o parser
    SOZINHO, sem resolver. Em produção o MarketplaceLinkResolver apanha
    `0x…:123` no URL e PERGUNTA em que cadeia vive — não adivinha, que é a
    regra R1 (nenhum componente assume uma cadeia por omissão).

    O teste fixa isso: o parser sozinho não resolve (e não deve — não tem
    como saber), e o resolver de produção resolve."""
    from finding_memeland.target.adapters import MarketplaceLinkResolver

    url = f"https://rarible.com/token/{C}:11385"
    assert extract_target_refs(url).refs == ()          # sem resolver: nada
    assert extract_target_refs(url).unresolved_links == (url,)

    asked: list[tuple] = []

    def probe(addr, tid):
        asked.append((addr.lower(), tid))
        return "ethereum"

    ref = MarketplaceLinkResolver(chain_probe=probe)(url)
    assert ref is not None and ref.id() == TARGET
    assert asked == [(C, 11385)], "o resolver tem de PERGUNTAR, não assumir"


def test_the_resolver_refuses_when_the_chain_cannot_be_established():
    """Fail-closed: sem resposta do probe, não há claim — nunca uma cadeia
    por omissão."""
    from finding_memeland.target.adapters import MarketplaceLinkResolver

    r = MarketplaceLinkResolver(chain_probe=lambda a, t: None)
    assert r(f"https://rarible.com/token/{C}:11385") is None
