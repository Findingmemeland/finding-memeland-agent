"""The BURNED LIST (Pedro, 09/10: "lista de queimados") — in the code, EXPOSED.

"Burned" already means something here: a token whose ownerOf reverts
(LIVE_BURNED; the harvest report's "queimados"). So in code and in reports
this list is called EXPOSED, and the cause it gives is "exposto".

WHAT IT IS. Any contract:tokenId that was ever written in a versioned file of
the public repository can never be a target — never deposited, never drawn,
never launched. On 09/10 fourteen data files were found versioned: a
short-list, the candidates of the first censuses and four raw measurement
captures, public since the day they were committed. They leave the
repository going forward, but the history stays (rewriting it would break
every clone and prove nothing: it was public), and anyone who read it holds
a list. A hunt whose answer is on that list is a hunt with a short-list.

HOW IT IS KEPT. The pairs are extracted on the operator's machine by
`scripts/build_exposed_list.py`, from every blob of every commit, and what is
committed is `exposed.txt`: one truncated SHA-256 per pair. Digests, not ids
— nothing here adds to what the history already says, and nothing in this
package ever formats a pair into a log, a report or a repr. The digest is
not a secret (the pairs are public); it is simply the form in which the list
never has to be read by a person.

IT IS A GUARD THAT APPROVES BY NOT FINDING, so it has a canary (rule 8): a
synthetic pair the builder always writes. A list that cannot see its own
canary is blind — an empty or truncated file must never read as "nothing is
exposed" — and a blind list raises instead of answering.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("exposed.txt")
DIGEST_HEX = 16                       # 64 bits: ~1e-15 false hits per check
_SALT = b"finding-memeland/exposed/v1:"

# The canary: no contract lives at this address and nothing will ever be
# minted there. The builder always writes it; a loaded list must find it.
CANARY = ("0x" + "ca" * 20, 1)


class ExposedListBlind(RuntimeError):
    """The list could not be read, or does not contain its canary. Whoever
    asks must refuse, not pass (fail-closed). The message carries no pair."""


def exposed_digest(contract: str, token_id: int | str) -> str:
    """The form in which a pair is kept. The CHAIN is deliberately left out:
    the rule is about contract:tokenId (Pedro), and the same address on
    another chain is at worst one more token that can never be a target."""
    key = f"{str(contract).strip().lower()}:{int(token_id)}".encode()
    return hashlib.sha256(_SALT + key).hexdigest()[:DIGEST_HEX]


# --------------------------------------------------------------------------- #
# Extraction — what counts as "a contract:tokenId written in a file"            #
# --------------------------------------------------------------------------- #

_ADDRESS = r"0x[0-9a-fA-F]{40}"
_FULL_ADDRESS_RE = re.compile(rf"^{_ADDRESS}$")
# In running text: chain:0x…:42, 0x… : 42, a marketplace or explorer path
# …/0x…/42, and a contract with its tokenId in the query (?tokenId=, ?a=).
_TEXT_PAIR_RE = re.compile(rf"({_ADDRESS})\s*[:/]\s*(\d{{1,80}})(?![0-9A-Za-z])")
_QUERY_PAIR_RE = re.compile(
    rf"({_ADDRESS})[^\s\"'<>]*?[?&](?:tokenId|token_id|a)=(\d{{1,80}})")
# In structured data: the field that names the CONTRACT (never an owner, a
# creator or a wallet) and the field that names the token.
_CONTRACT_KEYS = frozenset({"contract", "contract_address", "contractaddress",
                            "asset_contract", "token_address", "tokenaddress",
                            "collection_address"})
_TOKEN_KEYS = frozenset({"tokenid", "token_id", "identifier", "token"})


def _as_token_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_contract(value) -> str | None:
    if isinstance(value, str) and _FULL_ADDRESS_RE.match(value.strip()):
        return value.strip().lower()
    if isinstance(value, dict):                 # {"asset_contract": {"address": …}}
        return _as_contract(value.get("address"))
    return None


def pairs_in_text(text: str) -> set[tuple[str, int]]:
    """Every contract:tokenId that can be read off running text."""
    found = _TEXT_PAIR_RE.findall(text or "") + _QUERY_PAIR_RE.findall(text or "")
    return {(addr.lower(), int(tid)) for addr, tid in found}


def pairs_in_json(doc) -> set[tuple[str, int]]:
    """Every contract:tokenId a JSON document holds in separate fields.

    Three shapes, all found in the files this was written for:
      · an object with a contract field and a token field;
      · an object with a contract field whose token fields sit further down
        (`{"contract": …, "tokens": [{"tokenId": …}, …]}`) — the nearest
        contract above a token is its contract;
      · a two-element list `[address, tokenId]`.
    A contract with no token anywhere below it gives nothing: the rule is
    about pieces, and a contract can hold a hundred thousand of them."""
    out: set[tuple[str, int]] = set()

    def walk(node, contract: str | None) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in _CONTRACT_KEYS:
                    contract = _as_contract(value) or contract
            for key, value in node.items():
                if contract and str(key).lower() in _TOKEN_KEYS:
                    tid = _as_token_id(value)
                    if tid is not None:
                        out.add((contract, tid))
                walk(value, contract)
        elif isinstance(node, list):
            if len(node) == 2:
                addr, tid = _as_contract(node[0]), _as_token_id(node[1])
                if addr and tid is not None and not isinstance(node[0], dict):
                    out.add((addr, tid))
            for item in node:
                walk(item, contract)

    walk(doc, None)
    return out


def pairs_in_blob(raw: bytes) -> set[tuple[str, int]]:
    """Everything one versioned file exposes: its text, and — when it parses
    as JSON — its fields. Binary files expose nothing."""
    if b"\x00" in raw[:8192]:
        return set()
    text = raw.decode("utf-8", "replace")
    pairs = pairs_in_text(text)
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            pairs |= pairs_in_json(json.loads(text))
        except ValueError:
            pass
    return pairs


# --------------------------------------------------------------------------- #
# The list                                                                     #
# --------------------------------------------------------------------------- #


def render_digests(pairs: Iterable[tuple[str, int]]) -> str:
    """The file's content for these pairs — the canary always among them."""
    digests = sorted({exposed_digest(c, t) for c, t in [*pairs, CANARY]})
    head = (
        "# Finding Memeland — exposed tokens (see exposed.py). One truncated\n"
        "# SHA-256 per contract:tokenId ever written in a versioned file of this\n"
        "# repository. Written by scripts/build_exposed_list.py — never by hand.\n"
        f"# entries: {len(digests)}\n")
    return head + "\n".join(digests) + "\n"


def _read_digests(text: str) -> Iterator[str]:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            yield line


class ExposedList:
    """`has(contract, token_id)` — True when the pair was ever exposed.
    Raises ExposedListBlind when the list cannot vouch for its answer.
    repr shows a count and nothing else."""

    def __init__(self, digests: Iterable[str] = (), *, readable: bool = True):
        self._digests = frozenset(d.strip().lower() for d in digests if d.strip())
        self._readable = bool(readable)

    @classmethod
    def load(cls, path: Path | str | None = None) -> ExposedList:
        """Never raises: a file that is missing or unreadable gives a BLIND
        list, which refuses at the moment it is asked — so the agent still
        boots, and what stops is depositing, drawing and launching."""
        try:
            text = Path(path or DEFAULT_PATH).read_text(encoding="utf-8")
        except OSError:
            return cls(readable=False)
        return cls(_read_digests(text))

    @classmethod
    def of(cls, pairs: Iterable[tuple[str, int]]) -> ExposedList:
        return cls(_read_digests(render_digests(pairs)))

    def __len__(self) -> int:
        return len(self._digests)

    @property
    def sees(self) -> bool:
        """Can this list vouch for a "no"? Only when it finds its canary."""
        return self._readable and exposed_digest(*CANARY) in self._digests

    def has(self, contract: str, token_id: int | str) -> bool:
        if not self.sees:
            raise ExposedListBlind(
                "the exposed list is unreadable or has no canary — not answering")
        return exposed_digest(contract, token_id) in self._digests

    def state(self) -> str:
        """For /status — a count, or that it is blind. Never an entry."""
        if not self.sees:
            return ("⚠️ ilegível — o depósito, o sorteio, o /prepare e o /launch "
                    "recusam até a lista voltar")
        return f"{len(self) - 1} expostos"            # the canary is not one

    def __repr__(self) -> str:
        return f"ExposedList({len(self)} digests{'' if self.sees else ', BLIND'})"

    __str__ = __repr__
