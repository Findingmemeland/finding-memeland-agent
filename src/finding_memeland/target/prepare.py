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

DECOYS, AND WHERE THEY ARE NOT (Opus, 17/09, walking back part of his own
advice). Padding the image probe during verification bought nothing: the
candidate's METADATA read already goes to the gateway on its own, so the
gateway has seen it before any batch is assembled. Padding one and not the
other was theatre, and it cost ~10 gateway calls per draw where 2 suffice.
Decoys now live only where they do real work:
  · the vision read of the ACCEPTED candidate — one batch of five;
  · the sealed batch the live check reuses every clue (RPC, no gateway).
And filling the larder weeks before a hunt weakens the gateway correlation
by itself: it sees reads on a day when there is no hunt to correlate them
with.

Everything effectful is injected; the logic tests offline.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .refresh import content_id, uri_is_content_addressed
from .selector import Target, artist_of, metadata_hash, name_qualifies, normalize_name

# --------------------------------------------------------------------------- #
# Sources                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Source:
    """One shared 1/1 contract we draw from. A source only qualifies if it
    ENUMERATES — `totalSupply()` and `tokenByIndex()`. Without both there is
    no cheap uniform draw, and probing ids blind is the complexity this
    rewrite exists to delete."""

    slug: str
    chain: str
    contract: str


# MEASURED live 17/09 (scripts/check_prepare.py), not assumed:
#   foundation  ✓ totalSupply 116,168
#   superrare2  ✓ totalSupply  50,922
#   makersplace ✗ totalSupply reverts      (04/09 census had said "by maxId")
#   superrare1  ✗ tokenByIndex reverts
# 167,090 pieces. Adding a source is one line here — and it is the only
# lever that dilutes the enumeration risk of a public, crawlable collection.
SOURCES: tuple[Source, ...] = (
    Source("foundation", "ethereum", "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"),
    Source("superrare2", "ethereum", "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"),
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

    def __repr__(self) -> str:
        return f"Larder({len(self.candidates)} ready, {len(self.used)} used)"

    __str__ = __repr__


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
    found: int = 0

    def render(self) -> str:
        causes = ", ".join(f"{k} {v}" for k, v in (
            ("metadata", self.metadata), ("nome", self.name),
            ("imagem", self.image), ("tamanho", self.too_big),
            ("dono", self.owner), ("único", self.unique),
            ("repetido", self.duplicate)) if v)
        return (f"{self.found} encontrado(s) em {self.draws} sorteio(s)"
                + (f" — {causes}" if causes else ""))


class PrepareRefused(RuntimeError):
    """Nothing was written or published. COUNTS ONLY in the message."""


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
        its size, then an index inside it. Two calls."""
        if not self._sizes:
            self.load_sizes()
        slugs = list(self._sizes)
        slug = self._rng.choices(slugs, weights=[self._sizes[s] for s in slugs],
                                 k=1)[0]
        src = next(s for s in self._sources if s.slug == slug)
        try:
            tid = self._token_by_index(src.chain, src.contract,
                                       self._rng.randrange(self._sizes[slug]))
        except Exception:  # noqa: BLE001 — a hole in the index; draw again
            return None
        return (src, int(tid)) if tid is not None else None

    def named_token(self, src: Source, tid: int, tally: Tally):
        """Checks 1 and 2 — metadata resolves, image is content-addressed,
        base name has two real words. Local and cheap; kills first."""
        try:
            read = self._read_token(src.chain, src.contract, tid)
        except Exception:  # noqa: BLE001 — this draw, not the run
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
               ) -> Candidate | None:
        """Checks 3, 4 and 5 — the image really is there, the owner is an
        EOA, the name is unique. Uniqueness LAST: it is the only paid call,
        so nothing that a free check can kill ever spends one."""
        meta = read.metadata
        image_uri = str(meta.get("image") or "")
        try:
            head = self._probe_image(image_uri)
        except Exception:  # noqa: BLE001
            head = None
        if not head or not head[0]:
            tally.image += 1            # Hunt #11: a perfect URI, no bytes
            return None
        if self._max_image and head[1] and head[1] > self._max_image:
            tally.too_big += 1          # 171 MB, measured — vision cannot use it
            return None
        try:
            eoa = self._owner_is_eoa(src.chain, src.contract, tid)
        except Exception:  # noqa: BLE001
            eoa = None
        if eoa is not True:
            tally.owner += 1
            return None
        try:
            uniq = self._name_is_unique(base, src.chain, src.contract, tid)
        except Exception:  # noqa: BLE001
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
class Decoy:
    chain: str
    contract: str
    token_id: int
    image: str


@dataclass(frozen=True)
class Prepared:
    """What `/launch` publishes. Clue 1 is already written and judged; launch
    adds a live check (RPC, seconds) and the post."""

    target: Target
    decoys: tuple[Decoy, ...]
    clue_one: object
    image_description: str
    attempts: int


class TargetPreparer:
    """Extra ports on top of the finder's:

      fetch_images(urls) -> dict[url, bytes | None]
          The FULL images, ONE gateway batch: the accepted candidate plus
          its decoys. The only place a whole artwork is downloaded, and the
          only gateway read that is padded (see the module docstring).
      describe(image_bytes)                     -> str
      write_clue_one(target, description)       -> ClueDraft (raises)
    """

    def __init__(self, *, finder: TargetFinder, fetch_images, describe,
                 write_clue_one, epoch_id: str = "e1", decoys: int = 4,
                 max_attempts: int = 6, max_decoy_draws: int = 24,
                 rng: random.Random | None = None,
                 notify: Callable[[str], None] | None = None):
        self._finder = finder
        self._fetch_images = fetch_images
        self._describe = describe
        self._write_clue_one = write_clue_one
        self._epoch = epoch_id
        self._n_decoys = max(0, int(decoys))
        self._max_attempts = max(1, int(max_attempts))
        self._max_decoy_draws = max(1, int(max_decoy_draws))
        self._rng = rng or random.SystemRandom()
        self._notify = notify or (lambda _t: None)

    def _decoys_from_larder(self, larder: Larder, exclude: str) -> list[Decoy]:
        """Padding for the day-before reads — taken FROM THE LARDER, not drawn
        fresh (Fable, 17/09, and he is right).

        Two reasons, and the first is the one that matters:

        · IT KEEPS THE FILL'S MIXING. On fill day the target is one CID among
          ~150 read that day — good cover. If the prepare re-reads ONE CID,
          the intersection of "read on fill day" and "re-read on D-1" has a
          single element, and that element is the target. Fresh decoys do not
          fix it: they were never in the fill set. Larder members were.
        · THEY ARE PROVEN ALIVE. A quarter of 2021 artwork no longer serves
          its bytes, so four freshly drawn decoys are all alive only ~32% of
          the time (0.75^4) — and a batch where only the target is served is
          the 16/09 bug one floor down.

        The decoys must also go through the SAME gateway the fill used: keyed
        for one and generic for the other would leave the target as the only
        CID present in both logs."""
        others = [c for c in larder.candidates if c.id() != exclude]
        self._rng.shuffle(others)
        return [Decoy(chain=c.chain, contract=c.contract, token_id=c.token_id,
                      image=c.image) for c in others[:self._n_decoys]]

    def prepare(self, larder: Larder) -> tuple[Prepared, Larder]:
        """Take one from the larder, RE-VERIFY it (pins expire, pieces get
        sold into contracts, names stop being unique), describe the artwork
        and write Clue 1. Returns the sealed hunt and the larder with that
        candidate consumed."""
        if larder.size() == 0:
            raise PrepareRefused("despensa vazia — corre /fill primeiro")
        self._finder.load_sizes()
        tally = Tally()
        for attempt in range(1, self._max_attempts + 1):
            cand = larder.take(self._rng)
            if cand is None:
                raise PrepareRefused(
                    f"despensa esgotada ao fim de {attempt - 1} tentativa(s) — "
                    f"{tally.render()}; corre /fill")
            src = Source("larder", cand.chain, cand.contract)
            tally.draws += 1

            # things age: re-read and re-check before committing to it
            named = self._finder.named_token(src, cand.token_id, tally)
            if named is None:
                larder.consume(cand.id())
                continue
            read, base = named
            fresh = self._finder.verify(src, cand.token_id, read, base, tally)
            if fresh is None:
                larder.consume(cand.id())
                continue

            decoys = self._decoys_from_larder(larder, fresh.id())
            if len(decoys) < self._n_decoys:
                raise PrepareRefused(
                    f"a despensa tem {larder.size()} alvo(s): preciso de "
                    f"{self._n_decoys + 1} para o lote da véspera não deixar o "
                    "alvo sozinho no gateway. Corre /fill")
            urls = [fresh.image] + [d.image for d in decoys]
            self._rng.shuffle(urls)
            try:
                served = self._fetch_images(urls)
            except Exception:  # noqa: BLE001
                raise PrepareRefused(
                    "o lote de imagens falhou (gateway) — nada foi consumido")
            target_bytes = served.get(fresh.image)
            if not target_bytes:
                tally.image += 1
                larder.consume(cand.id())
                continue

            target = fresh.to_target(self._epoch)
            description = self._describe(target_bytes)
            clue_one = self._write_clue_one(target, description)
            larder.consume(cand.id())
            self._notify(f"prepare: alvo selado à {attempt}.ª tentativa · "
                         f"despensa {larder.size()}")
            return Prepared(target=target, decoys=tuple(decoys),
                            clue_one=clue_one, image_description=description,
                            attempts=attempt), larder

        raise PrepareRefused(
            f"{self._max_attempts} candidatos da despensa falharam a "
            f"re-verificação — {tally.render()}; corre /fill")
