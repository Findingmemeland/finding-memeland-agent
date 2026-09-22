"""The larder and `/prepare` — find targets in advance, use one at a time.

Replaces the snapshot pipeline (spec 16/09, rewritten 17/09 after the first
live measurements). Two phases, deliberately separated:

  `/fill`     runs whenever, for as long as it takes. Draws tokens at random
              from the sources and VERIFIES them for real — name, image
              BYTES, owner, unique name — until the larder holds N. Slow
              network is not a problem here: nobody is waiting.

  `/prepare`  takes one at random from the larder, RE-VERIFIES it (things
              age: IPFS pins expire, pieces get sold into contracts, names
              stop being unique), describes the artwork and writes Clue 1.

  `/launch`   live check and publish. Seconds.

WHY THIS IS NOT THE SNAPSHOT AGAIN (Pedro, 17/09). The snapshot did not fail
because it stored candidates. It failed because it listed 850k tokens to
build the store (40 h, $46, 53% lost to transport), because it never
verified that an image could be FETCHED, and because it ran against the
clock. The larder keeps the good half: fifty entries, each actually read,
built with patience on a day when nothing is at stake.

THE LARDER IS THE NEXT THIRTY ANSWERS. It is encrypted at rest with
TARGET_POOL_KEY, it never appears in a log, an error, a Telegram line or a
report — only counts do. A leak does not cost one hunt, it costs every hunt
still in the larder. Treat it like the reserved registry it replaces.

TWO MEASUREMENTS THAT SHAPED THIS FILE (live runs, 16-17/09):

  · 6 of 10 draws pass the name filter — the universe is ~100k pieces with
    a two-word name, not the 218 the snapshot suggested. The old number was
    an artefact of sampling 200 per stratum, never scarcity.
  · artworks are HUGE (14 MB, 19 MB, one of 171 MB) and a free public
    gateway times out pulling them. So verification reads the first few KB
    with a Range request and the size header — enough to prove the bytes
    exist and to refuse what vision could never process. The full download
    happens once, for the accepted candidate.

NO DECOYS ANYWHERE (Opus + Fable, 17/09 — removing a design Opus argued
for, on the evidence). What padding bought was hiding WHICH token is the
target from an infrastructure provider. What it cost, measured: the P0-A
intersection bug, its mirror at the judge, theatre at verification, a batch
that failed when a decoy was dead, and a floor of five on the larder.
Attacks prevented: none. So:

  · the vision read is ONE read of the target, once, on the day before;
  · the live check is ONE RPC read per clue.

WHAT ACTUALLY DOES THE WORK IS THE ROTATION, AND IT ALWAYS DID. The
repeated read during a hunt goes through PUBLIC RPCs in rotation, so no
single provider ever sees the pattern — provider A answers clue 1,
provider B clue 3, none of them sees a series. Four decoys never had the
scale to do that: they divide by five a thing that needs dividing by a
thousand (the guess budget is ~5,000).

So the line that must not be touched is the rotation. Anyone who
"simplifies" the live check onto ONE provider — and above all onto our own
keyed RPC — is handing that provider the answer to every hunt, in real
time, with our identity attached. No amount of padding restores that. If
the live check ever moves to the keyed RPC, this decision reverses.

Everything effectful is injected; the logic tests offline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .commitment import compute_commitment_v2, generate_salt
from .refresh import content_id, uri_is_content_addressed
from .selector import Target, artist_of, metadata_hash, name_qualifies, normalize_name

# --------------------------------------------------------------------------- #
# Sources                                                                      #
# --------------------------------------------------------------------------- #


DRAW_INDEX = "index"     # tokenByIndex(i) — exacto, sem buracos
DRAW_ID = "id"           # tokenURI(id) com id ao calhas — para quem não enumera


@dataclass(frozen=True)
class Source:
    """One shared 1/1 contract we draw from.

    DOIS MODOS DE SORTEIO, e o segundo é uma inversão deliberada (22/09).

    `index` — `totalSupply()` + `tokenByIndex(i)`. Exacto: cada sorteio
    devolve um token que existe, sem buracos. É o que esta classe exigia em
    exclusivo, e a razão estava escrita aqui: "probing ids blind is the
    complexity this rewrite exists to delete".

    `id` — `totalSupply()` para o limite, e um id ao calhas dentro dele. O
    que mudou não foi a opinião, foi a medição. Os hunts #12 e #13 saíram do
    MESMO contrato porque o universo tinha duas fontes, e tinha duas porque
    exigir ERC721Enumerable exclui quase toda a gente: é caro e poucos o
    implementam. A sonda (scripts/probe_sources.py) mostrou o superrare1 a
    devolver tokenURI em 3/3 ids ao calhas — os ids são densos, e a
    complexidade que se queria apagar custa, na prática, uma leitura
    falhada de vez em quando, fora do relógio.

    O modo `id` ASSUME ids densos em [1, totalSupply]. Um contrato com ids
    esparsos falha quase sempre — e é por isso que a sonda imprime a taxa
    de acerto: nenhuma fonte entra aqui sem essa medição."""

    slug: str
    chain: str
    contract: str
    draw: str = DRAW_INDEX


# MEDIDO ao vivo, nunca assumido (scripts/probe_sources.py, 22/09 — a
# medição de 17/09 dizia o mesmo dos dois primeiros):
#   foundation  ✓ totalSupply 116,168 · tokenByIndex ok      → index
#   superrare2  ✓ totalSupply  50,922 · tokenByIndex ok      → index
#   superrare1  ✓ totalSupply   4,436 · tokenByIndex reverte,
#                 mas tokenURI respondeu a 3/3 ids ao calhas → id
#   makersplace ✗ totalSupply reverte — fica de fora
#
# PORQUE É QUE O SUPERRARE1 ENTROU AGORA. Os hunts #12 e #13 saíram do MESMO
# contrato, e o contador da despensa mostrou porquê: duas fontes, uma cadeia.
# Quem reparasse cortava o espaço de busca a meio sem que nenhuma pista o
# tivesse dado. A terceira fonte não resolve a variedade de CADEIAS — isso
# precisa de contratos fora de Ethereum — mas acaba com o "ou é uma ou é a
# outra", e o modo `id` abre a porta a contratos que não enumeram, que são
# a esmagadora maioria.
#
# Acrescentar uma fonte continua a ser uma linha aqui. O que NÃO é opcional
# é passar pela sonda primeiro: o modo `id` assume ids densos, e só a taxa
# de acerto medida diz se um contrato os tem.
SOURCES: tuple[Source, ...] = (
    Source("foundation", "ethereum", "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"),
    Source("superrare2", "ethereum", "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"),
    Source("superrare1", "ethereum", "0x41a322b28d0ff354040e2cbc676f0320d8c8850d",
           draw=DRAW_ID),
)


def enumerable_sources(sources: Sequence[Source], *, total_supply, token_by_index,
                       ) -> tuple[list[Source], dict[str, str]]:
    """Split `sources` into the ones that enumerate and the reasons the rest
    were dropped. Two calls each — measured, never assumed."""
    ok: list[Source] = []
    dropped: dict[str, str] = {}
    for s in sources:
        try:
            total = total_supply(s.chain, s.contract)
        except Exception as e:  # noqa: BLE001 — a source is dropped, never fatal
            dropped[s.slug] = f"totalSupply raised ({type(e).__name__})"
            continue
        if not total or total <= 0:
            dropped[s.slug] = "no totalSupply"
            continue
        try:
            first = token_by_index(s.chain, s.contract, 0)
        except Exception as e:  # noqa: BLE001
            dropped[s.slug] = f"tokenByIndex raised ({type(e).__name__})"
            continue
        if first is None:
            dropped[s.slug] = "no tokenByIndex"
            continue
        ok.append(s)
    return ok, dropped


# --------------------------------------------------------------------------- #
# The larder                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Candidate:
    """A verified target-in-waiting. SECRET: never rendered, never logged."""

    chain: str
    contract: str
    token_id: int
    name: str                 # base name (serial stripped) — what clues cipher
    name_onchain: str
    description: str
    image: str
    token_uri: str
    artist: str
    metadata: dict
    found_at: str = ""

    def id(self) -> str:
        return f"{self.chain}:{self.contract.lower()}:{self.token_id}"

    def to_target(self, epoch: str) -> Target:
        return Target(
            chain=self.chain, contract=self.contract.lower(),
            token_id=self.token_id, name=self.name,
            name_onchain=self.name_onchain, description=self.description,
            image=self.image, metadata_sha256=metadata_hash(self.metadata),
            epoch=epoch, token_uri=self.token_uri,
            content_id=content_id(self.token_uri) or "", artist=self.artist)

    def __repr__(self) -> str:      # never the contents
        return "Candidate(<sealed>)"

    __str__ = __repr__


@dataclass
class Larder:
    """Verified targets waiting their turn, plus the ids already used (a
    target is never drawn twice). repr shows counts, never contents."""

    candidates: list[Candidate] = field(default_factory=list)
    used: list[str] = field(default_factory=list)

    def size(self) -> int:
        return len(self.candidates)

    def has(self, cid: str) -> bool:
        return cid in self.used or any(c.id() == cid for c in self.candidates)

    def add(self, c: Candidate) -> bool:
        if self.has(c.id()):
            return False
        self.candidates.append(c)
        return True

    def take(self, rng: random.Random) -> Candidate | None:
        if not self.candidates:
            return None
        return self.candidates[rng.randrange(len(self.candidates))]

    def consume(self, cid: str) -> None:
        self.candidates = [c for c in self.candidates if c.id() != cid]
        if cid not in self.used:
            self.used.append(cid)

    def spread(self) -> dict:
        """A COMPOSIÇÃO da despensa, em contagens — nunca em nomes.

        Hunts #12 e #13 (18 e 22/09) saíram do MESMO contrato partilhado do
        SuperRare, na mesma cadeia; só o tokenId mudou. Nenhuma pista deu
        isso: fomos nós. Jogadores já varriam esse contrato a meio da #13,
        porque quem repara deixa de procurar "em qualquer NFT onchain" e
        passa a procurar num contrato só.

        A causa provável está aqui dentro: se a amostragem favorece fontes
        grandes, um contrato com centenas de milhares de tokens domina a
        despensa e o /prepare nunca chega a ter de escolher. Filtrar no fim
        não chega se o balde já vem torto — mas antes de mexer na
        amostragem é preciso MEDIR, e até hoje não havia como.

        Devolve só números, pela disciplina de sempre: o operador vê a
        forma da despensa sem nunca ver um alvo."""
        by_contract: dict[str, int] = {}
        by_chain: dict[str, int] = {}
        for c in self.candidates:
            key = f"{c.chain}:{c.contract.lower()}"
            by_contract[key] = by_contract.get(key, 0) + 1
            by_chain[c.chain] = by_chain.get(c.chain, 0) + 1
        counts = sorted(by_contract.values(), reverse=True)
        return {
            "total": len(self.candidates),
            "contracts": len(by_contract),
            "biggest": counts[0] if counts else 0,
            "chains": dict(sorted(by_chain.items(), key=lambda kv: -kv[1])),
        }

    def __repr__(self) -> str:
        return f"Larder({len(self.candidates)} ready, {len(self.used)} used)"

    __str__ = __repr__


class ReadUnavailable(RuntimeError):
    """OUR transport failed — the gateway threw, the RPC threw. It says
    nothing about the candidate.

    R8 applied to the larder (measured 17/09): the first live `/fill` of 600
    draws lost 442 of them at the metadata read, against 1 in 20 on a laptop
    the same day. That gap is a throttled gateway, not four hundred dead
    NFTs. During `/fill` the distinction costs nothing — a lost draw is a
    redraw. During `/prepare` it is everything: a candidate dropped for OUR
    outage is a verified target thrown away, and six of them in a row empties
    a third of the larder for a 429."""


class LarderIntegrityError(RuntimeError):
    """The larder could not be read. The message never carries contents."""


class LarderStore:
    """Encrypted persistence (PoolCipher port + injected read/write), the
    same shape as the stores it replaces. THE LARDER IS THE NEXT THIRTY
    ANSWERS — a leak costs every hunt still in it, not one."""

    VERSION = 1

    def __init__(self, *, cipher, read: Callable[[], str | None],
                 write: Callable[[str], None]):
        self._cipher = cipher
        self._read = read
        self._write = write

    def save(self, larder: Larder) -> None:
        payload = json.dumps({
            "v": self.VERSION,
            "used": list(larder.used),
            "candidates": [{
                "chain": c.chain, "contract": c.contract, "tokenId": c.token_id,
                "name": c.name, "name_onchain": c.name_onchain,
                "description": c.description, "image": c.image,
                "token_uri": c.token_uri, "artist": c.artist,
                "metadata": c.metadata, "found_at": c.found_at,
            } for c in larder.candidates],
        }, ensure_ascii=False)
        self._write(self._cipher.encrypt(payload))

    def load(self) -> Larder:
        blob = self._read()
        if blob is None:
            return Larder()
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            if doc.get("v") != self.VERSION:
                raise ValueError("unexpected larder version")
            return Larder(
                candidates=[Candidate(
                    chain=r["chain"], contract=r["contract"],
                    token_id=int(r["tokenId"]), name=r["name"],
                    name_onchain=r["name_onchain"],
                    description=r.get("description", ""), image=r["image"],
                    token_uri=r.get("token_uri", ""), artist=r.get("artist", ""),
                    metadata=r["metadata"], found_at=r.get("found_at", ""),
                ) for r in doc["candidates"]],
                used=[str(u) for u in doc.get("used", [])])
        except Exception as e:  # noqa: BLE001 — fail closed, no contents
            raise LarderIntegrityError(
                f"larder unreadable ({type(e).__name__}) — wrong key or "
                "corrupted store") from e


# --------------------------------------------------------------------------- #
# Tally — this replaces the gate                                               #
# --------------------------------------------------------------------------- #


@dataclass
class Tally:
    """Why draws were spent. A healthy source yields in a handful of draws;
    a cause that dominates is a named symptom instead of a 40-hour census."""

    draws: int = 0
    metadata: int = 0        # tokenURI reverts, or is not content-addressed
    name: int = 0            # base name has fewer than two real words
    image: int = 0           # the image did not come back as bytes
    too_big: int = 0         # bigger than vision can use (171 MB, measured)
    owner: int = 0           # owner is a contract (escrow, vault, fraction)
    unique: int = 0          # another piece carries the same base name
    duplicate: int = 0       # already in the larder or already used
    # /prepare only, and each one is a DIFFERENT symptom. Folding them into
    # the counters above is what made the first refusal unreadable (17/09):
    # "nome 3" could have meant three bad names or three clues the guards
    # refused to write, and those two call for opposite responses.
    blind: int = 0           # vision refused the bytes (4xx / empty answer)
    unwritable: int = 0      # guards exhausted: no clue can be written here
    no_index: int = 0        # the marketplace index cannot see this piece
    unavailable: int = 0     # OURS (gateway/RPC/guard down) — candidate KEPT
    found: int = 0

    def render(self) -> str:
        causes = ", ".join(f"{k} {v}" for k, v in (
            ("metadata", self.metadata), ("nome", self.name),
            ("imagem", self.image), ("tamanho", self.too_big),
            ("dono", self.owner), ("único", self.unique),
            ("repetido", self.duplicate), ("visão-recusou", self.blind),
            ("sem-pista", self.unwritable), ("sem-índice", self.no_index),
            ("indisponível-NOSSO", self.unavailable)) if v)
        return (f"{self.found} encontrado(s) em {self.draws} sorteio(s)"
                + (f" — {causes}" if causes else ""))


class PrepareRefused(RuntimeError):
    """Nothing was written or published. COUNTS ONLY in the message."""


# --------------------------------------------------------------------------- #
# Used targets: the authority lives on the hunt rows, not in the larder        #
# --------------------------------------------------------------------------- #


def used_hmac(target_id: str, key: str) -> str:
    """HMAC-SHA256 of a target id, keyed with TARGET_POOL_KEY (Fable, 17/09).

    The larder remembers what it has used, but a blob is MUTABLE: restore a
    backup from before a hunt and a target that was already revealed comes
    back to life, gets drawn again, and the reveal of a month ago already
    named it. So "used" does not live only in the larder — every hunt row
    carries this fingerprint, and `/prepare` excludes anything that matches
    one. The hunt table only ever grows, so a restored blob loses its
    power.

    It is an HMAC and not a plain hash because a bare SHA-256 of
    `chain:contract:tokenId` is an ENUMERATION ORACLE: anyone with the
    source list could hash all 167k ids and read the hunt table to learn
    every past target — and, worse, confirm a guess about a LIVE one. The
    key makes the fingerprint useless to anyone who does not already have
    it. Same reasoning that put the salt inside the commitment."""
    return hmac.new(key.encode("utf-8"), target_id.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def excluded_ids(candidates: Sequence["Candidate"], *, used_hmacs: Sequence[str],
                 key: str) -> set[str]:
    """Which candidates are already spent, by matching their fingerprint
    against the ones the hunt rows carry."""
    seen = set(used_hmacs)
    return {c.id() for c in candidates if used_hmac(c.id(), key) in seen}


# --------------------------------------------------------------------------- #
# Finding — the network half, off the clock                                    #
# --------------------------------------------------------------------------- #


class TargetFinder:
    """Ports (all injected, stateless per call):

      total_supply(chain, contract)              -> int | None
      token_by_index(chain, contract, index)     -> int | None
      read_token(chain, contract, token_id)      -> TokenRead | None
      probe_image(url)                           -> (bytes, size) | None
          A RANGED read: the first few KB plus the total size. Proves the
          bytes are there without pulling a 15 MB artwork.
      owner_is_eoa(chain, contract, token_id)    -> bool | None
      name_is_unique(base, chain, contract, tid) -> bool | None
    """

    def __init__(self, *, sources: Sequence[Source], total_supply, token_by_index,
                 read_token, probe_image, owner_is_eoa, name_is_unique,
                 min_words: int = 2, max_image_bytes: int = 24 * 1024 * 1024,
                 rng: random.Random | None = None,
                 now_iso: Callable[[], str] | None = None):
        if not sources:
            raise ValueError("TargetFinder needs at least one source")
        self._sources = tuple(sources)
        self._total_supply = total_supply
        self._token_by_index = token_by_index
        self._read_token = read_token
        self._probe_image = probe_image
        self._owner_is_eoa = owner_is_eoa
        self._name_is_unique = name_is_unique
        self._min_words = int(min_words)
        self._max_image = int(max_image_bytes)
        self._rng = rng or random.SystemRandom()
        self._now = now_iso or (lambda: "")
        self._sizes: dict[str, int] = {}

    def load_sizes(self) -> dict[str, int]:
        """One call per source, then every draw is two calls."""
        self._sizes = {}
        for s in self._sources:
            try:
                total = self._total_supply(s.chain, s.contract)
            except Exception:  # noqa: BLE001 — absent from this run, loudly below
                total = None
            if total and total > 0:
                self._sizes[s.slug] = int(total)
        if not self._sizes:
            raise PrepareRefused(
                "no source answered totalSupply — the RPC is down or the "
                "source list is wrong; nothing drawn")
        return dict(self._sizes)

    def draw(self) -> tuple[Source, int] | None:
        """Uniform over the union of the sources: the source in proportion to
        its size, then a token inside it.

        Em modo `index` são duas chamadas e o token existe de certeza. Em
        modo `id` é UMA chamada — o id sai do gerador, sem ir à cadeia — e o
        token pode não existir: quem descobre isso é a leitura seguinte, que
        falha e conta como rejeição. É o preço de usar contratos que não
        enumeram, e paga-se fora do relógio, no /fill."""
        if not self._sizes:
            self.load_sizes()
        slugs = list(self._sizes)
        slug = self._rng.choices(slugs, weights=[self._sizes[s] for s in slugs],
                                 k=1)[0]
        src = next(s for s in self._sources if s.slug == slug)
        total = self._sizes[slug]
        if getattr(src, "draw", DRAW_INDEX) == DRAW_ID:
            return (src, self._rng.randrange(1, max(2, total + 1)))
        try:
            tid = self._token_by_index(src.chain, src.contract,
                                       self._rng.randrange(total))
        except Exception:  # noqa: BLE001 — a hole in the index; draw again
            return None
        return (src, int(tid)) if tid is not None else None

    def named_token(self, src: Source, tid: int, tally: Tally, *,
                    strict: bool = False):
        """Checks 1 and 2 — metadata resolves, image is content-addressed,
        base name has two real words. Local and cheap; kills first.

        `strict` (the /prepare path): a transport failure RAISES instead of
        counting as a rejection, so the caller can tell 'this candidate is
        dead' from 'we could not read it right now' and keep the candidate."""
        try:
            read = self._read_token(src.chain, src.contract, tid)
        except Exception as e:  # noqa: BLE001 — this draw, not the run
            if strict:
                raise ReadUnavailable(type(e).__name__) from None
            tally.metadata += 1
            return None
        if read is None or not isinstance(read.metadata, dict) or not read.metadata:
            tally.metadata += 1
            return None
        meta = read.metadata
        if not uri_is_content_addressed(str(meta.get("image") or "")):
            tally.metadata += 1
            return None
        base = normalize_name(str(meta.get("name") or "").strip())
        if not name_qualifies(base, min_words=self._min_words):
            tally.name += 1
            return None
        return read, base

    def verify(self, src: Source, tid: int, read, base: str, tally: Tally,
               *, strict: bool = False) -> Candidate | None:
        """Checks 3, 4 and 5 — the image really is there, the owner is an
        EOA, the name is unique. Uniqueness LAST: it is the only paid call,
        so nothing that a free check can kill ever spends one.

        `strict`: see named_token. Note what it does NOT cover — a probe
        that ANSWERS with no bytes is a dead pin, and that is the candidate's
        fault, not ours (Hunt #11). Only a throw is ours."""
        meta = read.metadata
        image_uri = str(meta.get("image") or "")
        try:
            head = self._probe_image(image_uri)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise ReadUnavailable(type(e).__name__) from None
            head = None
        if not head or not head[0]:
            tally.image += 1            # Hunt #11: a perfect URI, no bytes
            return None
        if self._max_image and head[1] and head[1] > self._max_image:
            tally.too_big += 1          # 171 MB, measured — vision cannot use it
            return None
        try:
            eoa = self._owner_is_eoa(src.chain, src.contract, tid)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise ReadUnavailable(type(e).__name__) from None
            eoa = None
        if eoa is not True:
            tally.owner += 1
            return None
        try:
            uniq = self._name_is_unique(base, src.chain, src.contract, tid)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise ReadUnavailable(type(e).__name__) from None
            uniq = None
        if uniq is not True:
            tally.unique += 1
            return None
        return Candidate(
            chain=src.chain, contract=src.contract.lower(), token_id=tid,
            name=base, name_onchain=str(meta.get("name") or "").strip(),
            description=str(meta.get("description") or "")[:600],
            image=image_uri, token_uri=read.token_uri, artist=artist_of(meta),
            metadata=meta, found_at=self._now())

    def fill(self, larder: Larder, want: int, *, max_draws: int = 400,
             notify: Callable[[str], None] | None = None, every: int = 25,
             ) -> Tally:
        """Draw and verify until the larder holds `want`. Off the clock: slow
        gateways cost time here and nothing else."""
        note = notify or (lambda _t: None)
        tally = Tally()
        self.load_sizes()
        while larder.size() < want and tally.draws < max_draws:
            tally.draws += 1
            if tally.draws % every == 0:
                note(f"fill: {tally.render()} · despensa {larder.size()}/{want}")
            drawn = self.draw()
            if drawn is None:
                tally.metadata += 1
                continue
            src, tid = drawn
            if larder.has(f"{src.chain}:{src.contract.lower()}:{tid}"):
                tally.duplicate += 1
                continue
            named = self.named_token(src, tid, tally)
            if named is None:
                continue
            read, base = named
            cand = self.verify(src, tid, read, base, tally)
            if cand is None:
                continue
            if larder.add(cand):
                tally.found += 1
        note(f"fill: {tally.render()} · despensa {larder.size()}/{want}")
        return tally


# --------------------------------------------------------------------------- #
# Preparing — larder -> sealed hunt                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Prepared:
    """What `/launch` publishes. Clue 1 is already written and judged; launch
    adds a live check (RPC, seconds) and the post.

    It is SEALED TO THE DATABASE, not kept in memory (Fable, 17/09): a
    restart between the day before and the hour must not lose the
    preparation — and losing it silently is worse, because the operator
    would find out at the announced minute."""

    target: Target
    clue_one: object
    image_description: str
    attempts: int
    salt: str = ""
    commitment: str = ""
    prepared_at: str = ""

    def id(self) -> str:
        return self.target.id()

    def __repr__(self) -> str:          # never the contents
        return f"Prepared(<sealed>, {self.attempts} attempt(s))"

    __str__ = __repr__


# How long a preparation is good for. Past this the artwork has had too
# long to change under us and the Clue 1 has had too long to become a
# search; `/launch` refuses and asks for a fresh `/prepare`.
PREPARED_TTL_HOURS = 72


class PreparedStore:
    """The prepared hunt, encrypted with TARGET_POOL_KEY, one slot. Reading
    it is how `/launch` finds the hunt — never a variable in memory."""

    VERSION = 1

    def __init__(self, *, cipher, read: Callable[[], str | None],
                 write: Callable[[str], None]):
        self._cipher = cipher
        self._read = read
        self._write = write

    def save(self, p: Prepared) -> None:
        t = p.target
        self._write(self._cipher.encrypt(json.dumps({
            "v": self.VERSION, "prepared_at": p.prepared_at,
            "salt": p.salt, "commitment": p.commitment,
            "attempts": p.attempts, "image_description": p.image_description,
            "clue_one": p.clue_one if isinstance(p.clue_one, (dict, str))
                        else getattr(p.clue_one, "__dict__", {}),
            "target": {"chain": t.chain, "contract": t.contract,
                       "tokenId": t.token_id, "name": t.name,
                       "name_onchain": t.name_onchain,
                       "description": t.description, "image": t.image,
                       "metadata_sha256": t.metadata_sha256,
                       "epoch": t.epoch, "token_uri": t.token_uri,
                       "content_id": t.content_id, "artist": t.artist},
        }, ensure_ascii=False)))

    def clear(self) -> None:
        self._write("")

    def load(self) -> Prepared | None:
        blob = self._read()
        if not blob:
            return None
        try:
            doc = json.loads(self._cipher.decrypt(blob))
            if doc.get("v") != self.VERSION:
                raise ValueError("unexpected prepared version")
            t = doc["target"]
            return Prepared(
                target=Target(
                    chain=t["chain"], contract=t["contract"],
                    token_id=int(t["tokenId"]), name=t["name"],
                    name_onchain=t["name_onchain"],
                    description=t.get("description", ""), image=t["image"],
                    metadata_sha256=t["metadata_sha256"], epoch=t["epoch"],
                    token_uri=t.get("token_uri", ""),
                    content_id=t.get("content_id", ""),
                    artist=t.get("artist", "")),
                clue_one=_draft_from(doc.get("clue_one")),
                image_description=doc.get("image_description", ""),
                attempts=int(doc.get("attempts", 1)),
                salt=doc.get("salt", ""), commitment=doc.get("commitment", ""),
                prepared_at=doc.get("prepared_at", ""))
        except Exception as e:  # noqa: BLE001 — fail closed, no contents
            raise LarderIntegrityError(
                f"prepared hunt unreadable ({type(e).__name__}) — wrong key "
                "or corrupted store; run /prepare again") from e


def _is_guard_unavailable(exc: BaseException) -> bool:
    """Is this OUR service failing, or this target being impossible?

    ClueGuardUnavailable (the marketplace not answering, the consistency
    judge down) is ours: R8 says we neither publish nor DISCARD on a cause
    we could not measure — the candidate stays.

    SearchIndexBlind is the exception that proves the rule, and it cost two
    live /prepare runs on 17/09 before it had a name of its own: the canary
    failing means the index cannot see THIS piece searched by its own
    on-chain name. Nothing about that is transient. Treating it as our
    outage keeps a permanently unclearable candidate in the larder and
    re-tests it for ever — a loop that never closes.

    Imported lazily: prepare.py must not drag the clue engine into every
    import of the larder."""
    try:
        from ..content.relic_clues import ClueGuardUnavailable
        from .clues import SearchIndexBlind
    except Exception:  # noqa: BLE001 — cannot tell → treat as ours (keep it)
        return True
    if isinstance(exc, SearchIndexBlind):
        return False
    return isinstance(exc, ClueGuardUnavailable)


def _is_blind_index(exc: BaseException) -> bool:
    try:
        from .clues import SearchIndexBlind
    except Exception:  # noqa: BLE001
        return False
    return isinstance(exc, SearchIndexBlind)


def _draft_from(stored):
    """Rebuild the clue draft the store flattened into JSON.

    THE BUG THIS EXISTS FOR (17/09, live, with an audience waiting):
    `save` writes the draft as a dict, `load` handed that dict straight
    back, and `/launch` asks for `draft.text` — which a dict does not
    have. The launch refused with "a preparação não traz Clue 1" over a
    Clue 1 that was sitting right there, whole, in the database.

    Nothing caught it because both sides were tested with a fake draft
    that never went through the store: the round trip was the one path
    with no test across it. A serialiser and its parser are one thing and
    must be tested as one thing."""
    if stored is None:
        return None
    if isinstance(stored, str):
        stored = {"text": stored}
    if isinstance(stored, dict):
        try:
            from ..content.clue_engine import ClueDraft
        except Exception:  # noqa: BLE001 — a plain carrier still has .text
            return type("Draft", (), dict(stored))()
        return ClueDraft(
            text=str(stored.get("text") or ""),
            taunt=stored.get("taunt"),
            angle=stored.get("angle"),
            image_aspect=stored.get("image_aspect"),
            claims=list(stored.get("claims") or []))
    return stored          # already a draft object


class TargetPreparer:
    """Extra ports on top of the finder's:

      fetch_image(url) -> bytes | None
          The FULL artwork, ONCE, for the accepted candidate. The only
          place a whole image is downloaded.
      describe(image_bytes)                     -> str
      write_clue_one(target, description)       -> ClueDraft (raises)
    """

    def __init__(self, *, finder: TargetFinder, fetch_image, describe,
                 write_clue_one, epoch_id: str = "e1",
                 max_attempts: int = 6, key: str = "",
                 used_hmacs: Callable[[], Sequence[str]] | None = None,
                 now_iso: Callable[[], str] | None = None,
                 rng: random.Random | None = None,
                 notify: Callable[[str], None] | None = None):
        self._finder = finder
        self._fetch_image = fetch_image
        self._describe = describe
        self._write_clue_one = write_clue_one
        self._epoch = epoch_id
        self._max_attempts = max(1, int(max_attempts))
        self._key = key
        self._used_hmacs = used_hmacs or (lambda: ())
        self._now = now_iso or (lambda: "")
        self._rng = rng or random.SystemRandom()
        self._notify = notify or (lambda _t: None)

    def prepare(self, larder: Larder) -> tuple[Prepared, Larder]:
        """Take one from the larder, RE-VERIFY it (pins expire, pieces get
        sold into contracts, names stop being unique), describe the artwork
        and write Clue 1. Returns the sealed hunt and the larder with that
        candidate consumed."""
        if larder.size() == 0:
            raise PrepareRefused("despensa vazia — corre /fill primeiro")
        self._finder.load_sizes()

        # A restored backup can resurrect a target this project already
        # revealed. The hunt rows are the authority, not the blob.
        if self._key:
            try:
                spent = excluded_ids(larder.candidates,
                                     used_hmacs=list(self._used_hmacs()),
                                     key=self._key)
            except Exception:  # noqa: BLE001 — the DB is not a reason to stop
                spent = set()
            for cid in spent:
                larder.consume(cid)
            if spent:
                self._notify(f"prepare: {len(spent)} alvo(s) da despensa já "
                             "tinham sido usados numa hunt — descartados")
            if larder.size() == 0:
                raise PrepareRefused(
                    "todos os alvos da despensa já foram usados — corre /fill")
        tally = Tally()
        # OUR outages are counted apart and NEVER consume a candidate (R8).
        # Enough of them in a row and the honest answer is "we are down",
        # with the larder untouched — not a larder emptied by a 429.
        unavailable = 0
        max_unavailable = max(3, self._max_attempts)
        for attempt in range(1, self._max_attempts + 1):
            cand = larder.take(self._rng)
            if cand is None:
                raise PrepareRefused(
                    f"despensa esgotada ao fim de {attempt - 1} tentativa(s) — "
                    f"{tally.render()}; corre /fill")
            src = Source("larder", cand.chain, cand.contract)
            tally.draws += 1

            # things age: re-read and re-check before committing to it
            try:
                named = self._finder.named_token(src, cand.token_id, tally,
                                                 strict=True)
                if named is None:
                    larder.consume(cand.id())
                    continue
                read, base = named
                fresh = self._finder.verify(src, cand.token_id, read, base,
                                            tally, strict=True)
            except ReadUnavailable as e:
                unavailable += 1
                tally.unavailable += 1
                self._notify(f"prepare: leitura indisponível ({e}) — candidato "
                             "MANTIDO na despensa, tento outro")
                if unavailable >= max_unavailable:
                    raise PrepareRefused(
                        f"{unavailable} leituras seguidas falharam por nossa "
                        "causa (gateway/RPC) — despensa INTACTA. Tenta daqui "
                        "a pouco.") from None
                continue
            if fresh is None:
                larder.consume(cand.id())
                continue

            target_bytes = self._fetch_image(fresh.image)
            if not target_bytes:
                tally.image += 1
                larder.consume(cand.id())
                continue

            target = fresh.to_target(self._epoch)
            # An empty description means vision refused the bytes — they
            # sniffed as an image on the first 4 KB and turned out to be
            # something else (an SVG, a video, a throttle page). Clue 1
            # about an artwork nobody described is a clue about nothing:
            # drop the candidate, take the next.
            try:
                description = self._describe(target_bytes)
            except Exception as e:  # noqa: BLE001
                # R8 ON OURSELVES (16/09): `/prepare FALHOU (BadRequestError)`
                # told the operator nothing at all — not the size, not the
                # format, not which half of the pipeline. The MEASURED cause
                # travels here. A 4xx is about THIS payload (the candidate
                # goes); anything else is the provider being down (it stays).
                why = (f"{type(e).__name__} · {len(target_bytes):,} bytes")
                if type(e).__name__ in {"BadRequestError", "UnprocessableEntityError"}:
                    self._notify(f"prepare: a visão recusou a arte ({why}) — "
                                 "candidato descartado, tento outro")
                    tally.blind += 1
                    larder.consume(cand.id())
                    continue
                unavailable += 1
                tally.unavailable += 1
                self._notify(f"prepare: visão indisponível ({why}) — candidato "
                             "MANTIDO na despensa, tento outro")
                if unavailable >= max_unavailable:
                    raise PrepareRefused(
                        f"{unavailable} falhas seguidas por nossa causa "
                        "(visão/gateway/RPC) — despensa INTACTA. Tenta daqui "
                        "a pouco.") from None
                continue
            if not str(description or "").strip():
                tally.blind += 1
                larder.consume(cand.id())
                continue
            try:
                clue_one = self._write_clue_one(target, description)
            except Exception as e:  # noqa: BLE001
                # THE SAME SPLIT AS THE VISION CALL, and it used to live at
                # launch (5th --real-clues, 09/09): a GUARD OF OURS that
                # cannot verify is our outage — the candidate stays and we
                # try again later. Guardrail EXHAUSTION is about this target:
                # under our rules it is unwritable, so it goes, exactly like
                # a void, and the next candidate gets its turn.
                if _is_guard_unavailable(e):
                    unavailable += 1
                    tally.unavailable += 1
                    self._notify(f"prepare: guarda nossa indisponível "
                                 f"({type(e).__name__}) — candidato MANTIDO "
                                 f"na despensa, tento outro · {str(e)[:220]}")
                    if unavailable >= max_unavailable:
                        raise PrepareRefused(
                            f"{unavailable} falhas seguidas por nossa causa "
                            "(guardas/visão/gateway) — despensa INTACTA. "
                            "Tenta daqui a pouco.") from None
                    continue
                if _is_blind_index(e):
                    tally.no_index += 1
                    self._notify("prepare: o mercado não indexa esta peça nem "
                                 "pelo nome dela — guarda cega, descartado")
                else:
                    tally.unwritable += 1
                    self._notify(f"prepare: Clue 1 impossível para este alvo "
                                 f"({type(e).__name__}) — descartado, tento "
                                 "outro")
                larder.consume(cand.id())
                continue
            # The commitment is PUBLISHED IN CLUE 1, so it is born here, with
            # the clue — not at launch. Same v2 formula as ever: nothing
            # about the protocol changes because the moment moved.
            salt = generate_salt()
            commitment = compute_commitment_v2(target.id(),
                                               target.metadata_sha256, salt)
            larder.consume(cand.id())
            self._notify(f"prepare: alvo selado à {attempt}.ª tentativa · "
                         f"despensa {larder.size()}")
            return Prepared(target=target, clue_one=clue_one,
                            image_description=description,
                            attempts=attempt, salt=salt, commitment=commitment,
                            prepared_at=self._now()), larder

        # The tally is the diagnosis, so it has to name the RIGHT cause:
        # "sem-pista" (the guards refuse every draft for this target) and
        # "nome" (the name itself does not qualify) call for opposite
        # responses, and folding them together — as this did until 17/09 —
        # leaves the operator with a number and no move.
        raise PrepareRefused(
            f"{self._max_attempts} candidatos da despensa falharam a "
            f"re-verificação — {tally.render()}"
            + ("; as leituras NOSSAS falharam, tenta outra vez daqui a pouco"
               if tally.unavailable >= tally.draws / 2 else "; corre /fill"))
