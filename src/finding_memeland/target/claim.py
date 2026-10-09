"""Claim parsing/validation for Option A — the player names the TOKEN.

Ratified rules (Opus, 05/09) this module implements:

  · a claim identifies the target as chain:contract:tokenId — the SAME
    canonical string as Target.id(), which is what the commitment sealed
  · a LINK — from any site — is the PRIMARY way to claim (Pedro, 17/09:
    it is the easiest thing a player can do). What gets validated is still
    ALWAYS the chain:contract:tokenId resolved from it — never the URL
    text, never a slug, never a name; the commitment sealed the triple.
    A link we cannot resolve is OUR limit, so it costs no guess and the
    answer teaches the explicit format. NEVER "wrong" for something that
    might be right: that is our failure wearing the player's name.
  · clues NEVER state the chain, so the chain is part of the answer: an
    explicit paste WITHOUT a chain ("0x…:12") is claim-shaped (the oracle
    may answer with the public format rule) but can never match. The format
    is a pre-committed public rule, not a judgment call.
  · ONE TOKEN PER REPLY (Opus, 06/09, P0). The guess cap counts posts; a
    judge that accepted `any(ref matches)` would let one post carry 200
    links — five posts, a thousand candidates, one account, no sybils — and
    blind the spray detector at the same time (one post = one guess = one
    "distinct target"). So a post naming more than one token (distinct refs
    plus unresolved links > 1) is MALFORMED: it can never match, even if
    one of its refs is the target, it costs no guess, and the oracle answers
    with the format rule. Closing the door beats counting it. Public rule:
    "name one token per reply".

Secrecy discipline (same as the whole target package): nothing here ever
formats the hunt's target id into a repr, log line or verdict — verdicts
carry COUNTS. Player-pasted addresses are the player's own public post and
still don't get echoed by us.

Link parsing is structural (find contract+tokenId+chain in the URL), so it
covers OpenSea /assets/ and /item/, Rarible /token/<chain>/, Zora
/collect/ and the *scan NFT pages without one parser per marketplace. The
chain must be IN the link (a path segment, or an explorer host whose
identity is a chain) — never implied by a marketplace's usual chain (R1).
Links with no chain, and slug-only links (Foundation @artist pages,
SuperRare artworks, Blur/LooksRare paths), go to `unresolved_links` for an
injected resolver the production wiring provides (marketplace API →
chain:contract:tokenId). Fail-closed: an unresolved or unresolvable link
matches nothing.

⚠️ Before Hunt #11's dry-run, exercise the link shapes against REAL
marketplace URLs (the measured-adapter discipline) — URL formats drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

# Canonical chain slugs — Target.id() vocabulary. Aliases map what players
# and marketplaces write to what the commitment sealed.
CHAIN_ALIASES = {
    "ethereum": "ethereum", "eth": "ethereum", "mainnet": "ethereum",
    "base": "base",
    "polygon": "polygon", "matic": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism", "oeth": "optimism",
    "zora": "zora",
}

# Domains whose IDENTITY is a chain (block explorers): the host names the
# chain, so this is data, not a default. Multi-chain marketplaces (Rarible,
# Blur, LooksRare, OpenSea) are deliberately NOT here (R1, Opus re-review
# 05/09): "rarible.com means Ethereum unless the path says otherwise" is a
# default chain, and a default chain refuses a legitimate winner by the
# side door. A marketplace link that names no chain goes to
# `unresolved_links` for the injected resolver, which asks the marketplace.
_DOMAIN_CHAIN = {
    "etherscan.io": "ethereum",
    "basescan.org": "base",
    "polygonscan.com": "polygon",
    "arbiscan.io": "arbitrum",
}

# Marketplace-ish hosts: a link here that we could NOT parse still counts
# as a claim attempt (unresolved), never as chatter.
_MARKET_HOSTS = (
    "opensea.io", "rarible.com", "foundation.app", "superrare.com",
    "zora.co", "looksrare.org", "blur.io", "etherscan.io", "basescan.org",
    "polygonscan.com", "arbiscan.io",
)

_URL_RE = re.compile(r"https?://\S+")
_ADDR_TID_RE = re.compile(r"(0x[0-9a-fA-F]{40})[:/](\d+)\b")
_QUERY_TID_RE = re.compile(r"[?&](?:tokenId|token_id)=(\d+)\b")
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}\b")
# Espaços à volta dos dois pontos são TOLERADOS (medido ao vivo, hunt #12,
# 18/09). O formato publicado é `chain:contract:tokenId`, mas a forma como um
# humano o escreve é `Ethereum: 0xabc…def:42` — com o espaço que qualquer
# pessoa põe depois de dois pontos. Quatro jogadores diferentes fizeram-no na
# mesma hunt, e o agente respondeu-lhes que faltava o tokenId QUE ELES TINHAM
# ENVIADO. Dizer a alguém que lhe falta o que ele deu é a mesma falha que a
# regra "nunca dizer que está errado quando pode estar certo" proíbe, e a
# versão cara dela é não reconhecer a resposta vencedora.
#
# O que NÃO se tolera: quebras de linha. Um `\n` entre a cadeia e o contrato
# é outra pessoa a citar duas coisas distintas, não um claim mal escrito.
_SP = r"[ \t]*"
_EXPLICIT_RE = re.compile(
    rf"\b([A-Za-z]{{2,12}}):{_SP}(0x[0-9a-fA-F]{{40}}){_SP}:{_SP}(\d+)\b")
_CHAINLESS_RE = re.compile(
    rf"(?<![:\w])(0x[0-9a-fA-F]{{40}}){_SP}:{_SP}(\d+)\b")
_SEG_SPLIT_RE = re.compile(r"[/:?=&#.]+")


@dataclass(frozen=True)
class TargetRef:
    """A player's identification of a token, canonicalised."""

    chain: str
    contract: str
    token_id: int

    def id(self) -> str:
        return f"{self.chain}:{self.contract.lower()}:{self.token_id}"


@dataclass(frozen=True)
class ClaimExtraction:
    """What one post contains: fully-parsed refs, plus marketplace links we
    could not turn into a triple (a production resolver may still can)."""

    refs: tuple[TargetRef, ...]
    unresolved_links: tuple[str, ...]


def _canonical_chain(word: str) -> str | None:
    return CHAIN_ALIASES.get(word.strip().lower())


def parse_explicit(text: str) -> tuple[TargetRef, ...]:
    """chain:contract:tokenId pastes — the published claim format."""
    out: list[TargetRef] = []
    for chain_word, addr, tid in _EXPLICIT_RE.findall(text or ""):
        chain = _canonical_chain(chain_word)
        if chain is None:
            continue
        ref = TargetRef(chain=chain, contract=addr.lower(), token_id=int(tid))
        if ref not in out:
            out.append(ref)
    return tuple(out)


def parse_link(url: str) -> TargetRef | None:
    """One URL → a triple, or None. Structural: needs a 0x-contract with an
    adjacent tokenId AND a chain (URL segment, else domain implication)."""
    url = (url or "").strip().rstrip(")>.,;!?'\"")
    m = _ADDR_TID_RE.search(url)
    if m:
        addr, tid = m.group(1), int(m.group(2))
    else:
        # contract in the path, tokenId in the query (?tokenId=42) — the
        # other shape marketplaces use (Opus review, 05/09)
        ma = _ADDR_RE.search(url)
        mq = _QUERY_TID_RE.search(url)
        if not (ma and mq):
            return None
        addr, tid = ma.group(0), int(mq.group(1))

    chain: str | None = None
    parts = urlsplit(url)
    for seg in _SEG_SPLIT_RE.split(parts.path):
        c = _canonical_chain(seg)
        if c is not None:
            chain = c
            break
    if chain is None:
        host = parts.netloc.lower().split("@")[-1].split(":")[0]
        for dom, c in _DOMAIN_CHAIN.items():
            if host == dom or host.endswith("." + dom):
                chain = c
                break
    if chain is None:
        return None
    return TargetRef(chain=chain, contract=addr.lower(), token_id=tid)


def _is_market_host(url: str) -> bool:
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    return any(host == d or host.endswith("." + d) for d in _MARKET_HOSTS)


def extract_target_refs(text: str) -> ClaimExtraction:
    """Everything claim-relevant in a post, deduped, in order."""
    refs: list[TargetRef] = []
    unresolved: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(")>.,;!?'\"")
        ref = parse_link(url)
        if ref is not None:
            if ref not in refs:
                refs.append(ref)
        else:
            # ANY link, from ANY site (Pedro, 17/09). A link is the easiest
            # way to claim and must be the main one, so a host we do not
            # recognise is OUR gap, not the player's mistake: it becomes an
            # unreadable link — which costs no guess and earns the format,
            # never a jeer. The old rule only admitted a short list of
            # marketplace hosts, so a t.co, an aggregator or a new
            # marketplace read as plain noise.
            if url not in unresolved:
                unresolved.append(url)
    # explicit triples OUTSIDE urls (strip urls first so a rarible
    # "…/token/0x…:1" never double-counts as an explicit paste)
    stripped = _URL_RE.sub(" ", text or "")
    for ref in parse_explicit(stripped):
        if ref not in refs:
            refs.append(ref)
    return ClaimExtraction(refs=tuple(refs), unresolved_links=tuple(unresolved))


def collection_link(url: str) -> bool:
    """O URL tem CONTRATO mas não tem tokenId — a página da colecção.

    Medido no hunt #12: dois jogadores colaram a colecção em vez da peça
    (`rarible.com/base/collections/0x…`, `opensea.io/collection/…`). Nesse
    caso sabemos exactamente o que falta, e responder "não consigo ler esse
    link" é uma verdade inútil — manda a pessoa adivinhar o que já sabemos.

    Não é acusação nenhuma: continua a não gastar tentativa e continua a
    não dizer que está errado. Só é mais preciso."""
    u = (url or "").strip()
    if not _ADDR_RE.search(u):
        return False
    return not (_ADDR_TID_RE.search(u) or _QUERY_TID_RE.search(u))


# --------------------------------------------------------------------------- #
# What we could not read — and can say why (09/10, after Hunt #17)             #
# --------------------------------------------------------------------------- #
#
# Two families of post never reached the format replies at all. Neither named
# a token we could judge, so neither may be told it is wrong; both can be
# told what to send instead, at no cost to the player.

# A NEAR ADDRESS: 0x + 38..42 letters and digits that is not an address — one
# character that is not hex (an "S" typed for a "5": hunt #17, and a second
# player copied the slip), or one character too many or too few. The window
# stops short of a transaction hash (64) and of anything a player would not
# mistake for an address.
_NEAR_ADDR_RE = re.compile(r"(?<![0-9A-Za-z])0[xX]([0-9A-Za-z]{38,42})(?![0-9A-Za-z])")
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")
ADDRESS_LEN = 40


@dataclass(frozen=True)
class NearAddress:
    """An address-shaped token that is not an address. `bad_chars`: the
    characters that are not hex, as typed, in order, once each. `length`:
    how many characters follow the 0x (an address has 40)."""

    bad_chars: tuple[str, ...]
    length: int


def near_address(text: str) -> NearAddress | None:
    """The first address-shaped token in the post that is not an address, or
    None. A well-formed address is never one, wherever it stands."""
    for m in _NEAR_ADDR_RE.finditer(text or ""):
        body = m.group(1)
        bad = tuple(dict.fromkeys(c for c in body if c not in _HEX_CHARS))
        if len(body) == ADDRESS_LEN and not bad:
            continue
        return NearAddress(bad_chars=bad, length=len(body))
    return None


# ANOTHER CHAIN: a token named in a way that can never become one of ours.
#
# These words RECOGNISE a post; they never answer one. No public reply lists
# the chains this game reads (Pedro, 09/10): the chain is part of the answer,
# and the oracle must not narrow it.
OTHER_CHAIN_WORDS = frozenset({
    # 0x chains this game does not read
    "bsc", "bnb", "binance", "opbnb", "avalanche", "avax", "fantom", "ftm",
    "gnosis", "xdai", "linea", "scroll", "blast", "mantle", "celo", "zksync",
    "abstract", "apechain", "shape", "sei", "berachain", "bera", "monad",
    "ronin", "immutable", "imx", "unichain", "soneium", "moonbeam", "cronos",
    "kaia", "klaytn", "taiko", "metis", "sonic", "worldchain", "hyperevm",
    # not 0x chains at all
    "tezos", "xtz", "solana", "sol", "btc", "bitcoin", "ordinals", "ordinal",
    "flow", "cardano", "aptos", "sui", "tron", "starknet", "stacks",
    "stargaze", "xrpl", "algorand", "hedera",
})
# The ones a player writes as a prefix to an id that is not a 0x address
# (`tezos:KT1…:42`, `solana:<mint>`, `ordinals:<inscription>`). The id must be
# long enough to be one: "bitcoin: still king" is chatter.
_NON_EVM_PREFIX_RE = re.compile(
    r"(?<![0-9A-Za-z])(?:tezos|xtz|solana|sol|btc|bitcoin|ordinals?)"
    rf"{_SP}:{_SP}[0-9A-Za-z]{{20,}}", re.IGNORECASE)
_TEZOS_CONTRACT_RE = re.compile(r"(?<![0-9A-Za-z])KT1[1-9A-HJ-NP-Za-km-z]{33}(?![0-9A-Za-z])")
# Marketplaces and explorers of chains without 0x contracts. A link there
# that we could not read is not "a link we could not read": nothing on our
# side would ever read it.
_NON_EVM_HOSTS = (
    "objkt.com", "teia.art", "versum.xyz", "akaswap.com", "tzkt.io",
    "tensor.trade", "solscan.io", "solana.fm", "exchange.art",
    "ordinals.com", "ord.io", "ordiscan.com",
)
_NON_EVM_SEGMENTS = frozenset({"tezos", "solana", "bitcoin", "ordinals", "runes"})
_SOLANA_ITEM_RE = re.compile(r"^/item-details/[1-9A-HJ-NP-Za-km-z]{32,44}/?$")


def non_evm_link(url: str) -> bool:
    """A link to a piece on a chain without 0x contracts: a marketplace or
    explorer of one, or any link whose path names such a chain. Only asked
    about links we already failed to read."""
    parts = urlsplit((url or "").strip())
    host = parts.netloc.lower().split("@")[-1].split(":")[0]
    if any(host == d or host.endswith("." + d) for d in _NON_EVM_HOSTS):
        return True
    if any(seg.lower() in _NON_EVM_SEGMENTS for seg in parts.path.split("/")):
        return True
    return bool(_SOLANA_ITEM_RE.match(parts.path))     # Magic Eden's old Solana shape


def other_chain(text: str) -> bool:
    """Does the post name a token on a chain, or in a format, we do not read?

      · chain:contract:tokenId, well formed, with a chain word we do not read
      · a Tezos contract (KT1…), or an id prefixed tezos: / solana: / btc: /
        ordinals:
      · a link to a marketplace of such a chain

    Never a verdict: the same artwork may live on a chain we do read."""
    raw = text or ""
    if _TEZOS_CONTRACT_RE.search(raw) or _NON_EVM_PREFIX_RE.search(raw):
        return True
    urls = [u.rstrip(")>.,;!?'\"") for u in _URL_RE.findall(raw)]
    if any(non_evm_link(u) for u in urls):
        return True
    stripped = _URL_RE.sub(" ", raw)
    return any(_canonical_chain(word) is None and word.lower() in OTHER_CHAIN_WORDS
               for word, _addr, _tid in _EXPLICIT_RE.findall(stripped))


def claim_shaped(text: str) -> bool:
    """Does the post LOOK like a claim attempt? Explicit triple, chainless
    contract:tokenId paste, or a link that could plausibly BE a token.

    Deliberately narrower than `unresolved_links` (17/09). Extraction now
    keeps EVERY url, because any of them might resolve to a token and we
    would rather try and fail than ignore. But "might resolve" is not
    "looks like a claim": a blog link in the claim thread is chatter, and
    calling it claim-shaped would drag it into the guess cap."""
    ext = extract_target_refs(text)
    if ext.refs:
        return True
    if any(_is_market_host(u) or _ADDR_RE.search(u)
           for u in ext.unresolved_links):
        return True
    stripped = _URL_RE.sub(" ", text or "")
    return bool(_CHAINLESS_RE.search(stripped))


@dataclass(frozen=True)
class ClaimVerdict:
    """Counts only — never the target, never the refs. `malformed`: the post
    named more than one token — refused before any comparison."""

    matched: bool
    checked: int
    unresolved: int
    malformed: bool = False

    def render(self) -> str:
        if self.malformed:
            return "claim: MALFORMED (more than one token in the reply)"
        if self.matched:
            return "claim: MATCH"
        bits = [f"claim: no match ({self.checked} verificado(s)"]
        if self.unresolved:
            bits.append(f", {self.unresolved} link(s) por resolver")
        return "".join(bits) + ")"


MAX_TOKENS_PER_REPLY = 1


def tokens_named(ext: ClaimExtraction) -> int:
    """How many tokens a post names: distinct parsed refs + links still to
    resolve (each could become a ref)."""
    return len({r.id() for r in ext.refs}) + len(ext.unresolved_links)


class ClaimJudge:
    """Holds the hunt's sealed target id; judges posts. The id NEVER
    appears in repr/str/verdicts — this object lives next to the salt."""

    def __init__(self, *, target_id: str):
        self._target = target_id.strip()

    def judge(self, text: str, *,
              resolve_link: Callable[[str], TargetRef | None] | None = None,
              ) -> ClaimVerdict:
        ext = extract_target_refs(text)
        if tokens_named(ext) > MAX_TOKENS_PER_REPLY:
            return ClaimVerdict(matched=False, checked=0,
                                unresolved=len(ext.unresolved_links),
                                malformed=True)
        refs: list[TargetRef] = list(ext.refs)
        unresolved = 0
        for url in ext.unresolved_links:
            ref = None
            if resolve_link is not None:
                try:
                    ref = resolve_link(url)
                except Exception:  # noqa: BLE001 — fail-closed: no match
                    ref = None
            if ref is None:
                unresolved += 1
            elif ref not in refs:
                refs.append(ref)
        matched = any(r.id() == self._target for r in refs)
        return ClaimVerdict(matched=matched, checked=len(refs),
                            unresolved=unresolved)

    def __repr__(self) -> str:  # never the id
        return "ClaimJudge(target set)"

    __str__ = __repr__
