"""/probe — MEDIR uma fonte antes de a adoptar. Não guarda nada.

O PORQUÊ (06/10). Os mints ao calhas em Base não dão alvos: 0 guardados em
101 candidatos, cinco corridas — metade com homónimos, a outra metade
invisível à pesquisa do OpenSea. O Pedro quer alvos em Base "pelo menos de
vez em quando", sem escolher à mão ("no human picks"), e o único sítio onde
a arte 1/1 de Base vive são os contratos de criador da Manifold: ~6 000
ERC-721 (censo de 06/10, por amostra).

Antes de os adoptar mede-se quanto rendem — e a medição tem de responder a
duas perguntas de uma vez, porque 56 de 57 desses contratos guardam o
tokenURI em Arweave, que hoje a despensa não aceita:

    como hoje       quantos passariam as cinco verificações tal como estão
    com Arweave     quantos passariam SE o Arweave fosse aceite

A segunda coluna não muda nada do que se aceita: é o mesmo código das cinco
verificações, com um finder próprio que lê Arweave (só de arweave.net) — e
que só este comando usa.

O SORTEIO É UNIFORME E NÃO ESCOLHE. O deployer da Manifold numera as
criações (o nonce dele sobe um por contrato criado), por isso sorteia-se um
NÚMERO DE CRIAÇÃO e encontra-se o bloco por bissecção sobre o nonce em
blocos antigos. Sem lista, sem estado, sem catálogo que ordene por nós.

O QUE NUNCA SAI DAQUI: um contrato, um tokenId, um nome. Só contagens e
causas — o relatório vai para o Telegram.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from .adapters import arweave_url
from .harvest import looks_serial, name_is_cluable
from .prepare import Source, Tally
from .refresh import TokenRead, uri_is_content_addressed, uri_kind

# O deployer CREATE2 da Manifold em Base ("Contract Deployment Factory" na
# documentação deles; medido a 26/08 e reconfirmado a 06/10: 288 bytes de
# código, 12 262 criações). Endereço PÚBLICO de uma plataforma — não é um
# candidato.
MANIFOLD_BASE_DEPLOYER = "0xf3cd1e9326d1965935b287b1ee75c7183359a88a"

# OwnershipTransferred(address,address): o contrato recém-criado emite-o com
# o deployer como novo dono (medido a 06/10) — é assim que se sabe QUE
# contrato uma transacção criou sem precisar de traces.
_OWNERSHIP_TRANSFERRED = ("0x8be0079c531659141344cd1fd0a4f28419497f97"
                          "22a3daafe3b4186f6b6457e0")
_SUPPORTS_ERC721 = "0x01ffc9a780ac58cd" + "0" * 56
_MAX_TOKEN_ID = 1 << 20

PROBE_SOURCES = ("manifold",)
PROBE_USAGE = ("usage: /probe <fonte> [1..300] — fontes: "
               + ", ".join(PROBE_SOURCES) + " (só mede; não guarda nada)")


class ProbeBlind(RuntimeError):
    """A sonda não consegue VER a fonte (RPC sem arquivo, deployer sem
    criações). R8: isso não diz nada sobre a fonte — não se mede às cegas."""


def parse_probe_args(arg: str, *, default_n: int = 60) -> tuple[str, int]:
    """"/probe manifold [n]" → (fonte, n). ValueError com o uso no resto.
    A fonte é um NOME, nunca um endereço: um contrato no Telegram fica no
    histórico."""
    source, n = None, None
    for tok in (arg or "").split():
        if tok.isdigit() and n is None:
            n = int(tok)
        elif tok.lower() in PROBE_SOURCES and source is None:
            source = tok.lower()
        else:
            raise ValueError(PROBE_USAGE)
    n = default_n if n is None else n
    if source is None or not 1 <= n <= 300:
        raise ValueError(PROBE_USAGE)
    return source, n


# --------------------------------------------------------------------------- #
# O sorteio                                                                     #
# --------------------------------------------------------------------------- #


class DeployerSampler:
    """Os contratos que um deployer criou, pelo NÚMERO da criação.

    `call(method, params)` é um nó JSON-RPC com ARQUIVO (levanta em falha).
    O nonce de um contrato começa em 1 e sobe um por criação (CREATE e
    CREATE2), logo a criação n.º k é o primeiro bloco em que o nonce passa
    de k."""

    def __init__(self, *, call: Callable[[str, list], object], deployer: str):
        self._call = call
        self._deployer = deployer.lower()
        self._pad = "0x" + "0" * 24 + self._deployer[2:]

    def head(self) -> int:
        return int(str(self._call("eth_blockNumber", [])), 16)

    def _nonce(self, block: int) -> int:
        return int(str(self._call("eth_getTransactionCount",
                                  [self._deployer, hex(block)])), 16)

    def total(self, head: int) -> int:
        """Quantas criações até `head`. Levanta ProbeBlind se o nó não lê
        estado antigo — sem isso a bissecção não existe."""
        total = self._nonce(head) - 1
        if total < 1:
            raise ProbeBlind("o deployer não tem criações neste nó")
        try:
            old = self._nonce(max(1, head // 2))
        except Exception as e:  # noqa: BLE001
            raise ProbeBlind(f"o RPC não lê estado antigo ({type(e).__name__}) — "
                             "precisa de arquivo") from None
        if old > total + 1:
            raise ProbeBlind("o nonce do deployer não é monótono neste nó")
        return total

    def contract_of(self, k: int, head: int) -> str | None:
        """O endereço criado na criação n.º `k` (1 = a primeira). None quando
        a criação não deixou o evento esperado — outro tipo de contrato."""
        lo, hi = 1, head
        while lo < hi:
            mid = (lo + hi) // 2
            if self._nonce(mid) > k:
                hi = mid
            else:
                lo = mid + 1
        block = self._call("eth_getBlockByNumber", [hex(lo), True])
        txs = [t["hash"] for t in (block or {}).get("transactions", [])
               if str(t.get("to") or "").lower() == self._deployer]
        created: list[str] = []
        for h in txs:
            receipt = self._call("eth_getTransactionReceipt", [h]) or {}
            if receipt.get("status") != "0x1":
                continue
            for log in receipt.get("logs", []):
                topics = log.get("topics") or []
                if (len(topics) > 2 and topics[0] == _OWNERSHIP_TRANSFERRED
                        and str(topics[2]).lower() == self._pad):
                    created.append(str(log.get("address", "")).lower())
        # várias criações no mesmo bloco: a k-ésima é a (k − nonce anterior)
        idx = k - self._nonce(lo - 1)
        return created[idx] if 0 <= idx < len(created) else None


# --------------------------------------------------------------------------- #
# O relatório                                                                   #
# --------------------------------------------------------------------------- #

# A ordem em que as causas aparecem: a do funil (colheita → depósito).
_GROUPS = ("queimados", "indisponível-NOSSO", "defeito-DELES",
           "tokenURI fora de IPFS", "imagem fora de IPFS", "sem nome",
           "numerados", "nome recusado", "metadata", "nome", "nome-longo",
           "sem-identidade", "exposto", "imagem",
           "tamanho", "dono", "único", "outro")


@dataclass
class ProbeColumn:
    """Uma coluna do relatório: quantos passariam, e de que morreram os
    outros — (grupo, tipo) → n. Só contagens."""

    passed: int = 0
    causes: dict = field(default_factory=dict)

    def add(self, cause: tuple[str, str | None] | None) -> None:
        if cause is None:
            self.passed += 1
        else:
            self.causes[cause] = self.causes.get(cause, 0) + 1

    def render(self) -> str:
        parts = []
        # A CAUSE THAT IS COUNTED IS A CAUSE THAT IS SHOWN (10/10). The order
        # is the funnel's; a group this list does not know goes at the end —
        # never out. Three causes added on 09/10 (nome-longo, sem-identidade,
        # exposto) were counted and silently left out of the line, so the
        # numbers stopped adding up to the pieces tested.
        extra = sorted({g for g, _k in self.causes} - set(_GROUPS))
        for group in (*_GROUPS, *extra):
            kinds = {k: n for (g, k), n in self.causes.items() if g == group}
            if not kinds:
                continue
            total = sum(kinds.values())
            named = {k: n for k, n in kinds.items() if k}
            detail = ", ".join(f"{k} {n}" for k, n in sorted(named.items()))
            parts.append(f"{group} {total}" + (f" ({detail})" if detail else ""))
        return ", ".join(parts)


@dataclass
class ProbeReport:
    source: str = ""
    chain: str = ""
    universe: int = 0            # criações do deployer (todos os tipos)
    asked: int = 0               # contratos sorteados
    lost: dict = field(default_factory=dict)   # NOSSO: o RPC falhou — por tipo
    no_event: int = 0            # criação sem o evento: outro tipo de contrato
    not_erc721: int = 0
    empty: int = 0               # sem token 1: vazio, ou queimado
    tested: int = 0              # peças que chegaram às verificações
    uri_kinds: dict = field(default_factory=dict)
    today: ProbeColumn = field(default_factory=ProbeColumn)
    with_arweave: ProbeColumn = field(default_factory=ProbeColumn)

    def render(self) -> str:
        head = [f"{self.asked} contrato(s) sorteado(s) de {self.universe} criações"]
        for label, n in (("não-ERC-721", self.not_erc721),
                         ("sem peças", self.empty),
                         ("criação de outro tipo", self.no_event)):
            if n:
                head.append(f"{label} {n}")
        if self.lost:
            kinds = ", ".join(f"{k} {n}" for k, n in sorted(self.lost.items()))
            head.append(f"não medidos-NOSSO {sum(self.lost.values())} ({kinds})")
        head.append(f"{self.tested} peça(s) testada(s)")
        if self.uri_kinds:
            head.append("tokenURI: " + ", ".join(
                f"{k} {n}" for k, n in sorted(self.uri_kinds.items())))
        lines = [f"{self.source}/{self.chain}: " + " · ".join(head)]
        for label, col in (("como hoje", self.today),
                           ("com Arweave", self.with_arweave)):
            causes = col.render()
            lines.append(f"{label}: passariam {col.passed} de {self.tested}"
                         + (f" — {causes}" if causes else ""))
        return "\n".join(lines)


def _first(kinds: dict) -> str | None:
    return next(iter(kinds), None)


def cause_of(tally: Tally) -> tuple[str, str | None]:
    """De que morreu UM candidato, lido do Tally das cinco verificações —
    com os mesmos nomes que o relatório do depósito usa."""
    if tally.unavailable:
        return "indisponível-NOSSO", _first(tally.unavailable_kinds)
    if tally.bad_meta:
        return "defeito-DELES", _first(tally.bad_meta)
    if tally.not_ca:
        return "imagem fora de IPFS", _first(tally.not_ca)
    if tally.metadata:
        return "metadata", None
    if tally.name:
        return "nome", None
    if tally.long_name:
        return "nome-longo", None
    if tally.no_identity:
        return "sem-identidade", None
    if tally.exposed:
        return "exposto", None
    if tally.image:
        return "imagem", _first(tally.image_kinds)
    if tally.too_big:
        return "tamanho", None
    if tally.owner:
        return "dono", _first(tally.owner_kinds)
    if tally.unique:
        return "único", _first(tally.unique_kinds)
    return "outro", None


def _kind(e: BaseException) -> str:
    """Onde falhou, pelas palavras do adaptador; sem elas, o TIPO do erro —
    nunca a mensagem, que pode citar um URL com chave."""
    kind = getattr(e, "kind", None)
    return kind if kind and kind != "outro" else type(e).__name__


def _failure(e: BaseException) -> tuple[str, str]:
    return ("defeito-DELES" if getattr(e, "theirs", False)
            else "indisponível-NOSSO"), _kind(e)


def _uri_label(uri: str) -> str:
    if uri_is_content_addressed(uri):
        return "data" if uri.strip().lower().startswith("data:") else "ipfs"
    return uri_kind(uri)


# --------------------------------------------------------------------------- #
# A sonda                                                                       #
# --------------------------------------------------------------------------- #


class ContractProbe:
    """Sorteia contratos de um deployer, uma peça em cada um, e passa-a
    pelas cinco verificações — sem guardar nada.

    Portas (todas injectadas; levantam em falha de transporte):
      sampler                       DeployerSampler
      eth_call(contract, data)      -> hex   (revert levanta com `.revert`)
      token_uri(contract, token_id) -> str | None   (None = não existe)
      read_token(contract, token_id)-> TokenRead | None  (o leitor da
                                       despensa: metadata None se o tokenURI
                                       não for endereçado por conteúdo)
      arweave_json(url)             -> dict   (só arweave.net)
      finder                        um TargetFinder que ACEITA imagens em
                                    Arweave — o mesmo código das cinco
                                    verificações, e só este comando o usa
      accepts_today(uri)            -> bool   o que a DESPENSA aceita hoje
                                    como imagem endereçada por conteúdo. Por
                                    omissão IPFS / data / CID; com os
                                    gateways de Arweave configurados (09/10)
                                    é a regra da despensa, e a coluna "como
                                    hoje" passa a dizer o mesmo que a outra.
    """

    def __init__(self, *, source: str, chain: str, sampler: DeployerSampler,
                 eth_call, token_uri, read_token, arweave_json, finder,
                 rng: random.Random | None = None, min_words: int = 2,
                 accepts_today=None):
        self.source = source
        self.chain = chain
        self._sampler = sampler
        self._eth_call = eth_call
        self._token_uri = token_uri
        self._read = read_token
        self._arweave_json = arweave_json
        self._accepts_today = accepts_today or uri_is_content_addressed
        self._finder = finder
        self._rng = rng or random.SystemRandom()
        self._min_words = int(min_words)

    def run(self, n: int, *, every: int = 10,
            notify: Callable[[str], None] | None = None) -> ProbeReport:
        note = notify or (lambda _t: None)
        rep = ProbeReport(source=self.source, chain=self.chain)
        head = self._sampler.head()
        rep.universe = self._sampler.total(head)          # ProbeBlind se cego
        for i in range(1, max(1, n) + 1):
            if every and i % every == 0:
                note(f"probe {self.source}: {i}/{n} contratos · "
                     f"{rep.tested} peça(s) testada(s)")
            rep.asked += 1
            k = self._rng.randrange(1, rep.universe + 1)
            try:
                contract = self._sampler.contract_of(k, head)
                if contract is None:
                    rep.no_event += 1
                    continue
                if not self._is_erc721(contract):
                    rep.not_erc721 += 1
                    continue
                last = self._last_token(contract)
            except Exception as e:  # noqa: BLE001 — NOSSO: nada concluído
                rep.lost[_kind(e)] = rep.lost.get(_kind(e), 0) + 1
                continue
            if last == 0:
                rep.empty += 1
                continue
            self._check(contract, self._rng.randrange(1, last + 1), rep)
        return rep

    # -- o contrato -------------------------------------------------------- #

    def _is_erc721(self, contract: str) -> bool:
        try:
            data = self._eth_call(contract, _SUPPORTS_ERC721)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "revert", False):
                return False                       # não fala ERC-165
            raise
        body = str(data or "").strip()
        return len(body) >= 66 and int(body, 16) == 1

    def _exists(self, contract: str, token_id: int) -> bool:
        return self._token_uri(contract, token_id) is not None

    def _last_token(self, contract: str) -> int:
        """O maior id que existe, assumindo ids densos a partir de 1 (é assim
        que o contrato de criador da Manifold numera). 0 = sem token 1."""
        if not self._exists(contract, 1):
            return 0
        lo, hi = 1, 2
        while hi <= _MAX_TOKEN_ID and self._exists(contract, hi):
            lo, hi = hi, hi * 2
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._exists(contract, mid):
                lo = mid
            else:
                hi = mid
        return lo

    # -- a peça ------------------------------------------------------------ #

    def _check(self, contract: str, token_id: int, rep: ProbeReport) -> None:
        rep.tested += 1

        def both(cause):
            rep.today.add(cause)
            rep.with_arweave.add(cause)

        try:
            read = self._read(contract, token_id)
        except Exception as e:  # noqa: BLE001
            both(_failure(e))
            return
        if read is None:
            both(("queimados", None))
            return
        uri = read.token_uri
        label = _uri_label(uri)
        rep.uri_kinds[label] = rep.uri_kinds.get(label, 0) + 1
        meta = read.metadata
        today = None                    # o que trava esta peça HOJE, se algo
        if not isinstance(meta, dict):
            today = ("tokenURI fora de IPFS", uri_kind(uri))
            url = arweave_url(uri)
            if url is None:
                both(today)
                return
            try:
                meta = self._arweave_json(url)
            except Exception as e:  # noqa: BLE001
                rep.today.add(today)
                rep.with_arweave.add(_failure(e))
                return

        image = str(meta.get("image") or "")
        if not self._accepts_today(image):
            cause = ("imagem fora de IPFS", uri_kind(image))
            if arweave_url(image) is None:
                rep.today.add(today or cause)
                rep.with_arweave.add(cause)
                return
            today = today or cause

        # os filtros de nome da colheita, pela mesma ordem
        name = meta.get("name")
        if not name:
            cause = ("sem nome", None)
        elif looks_serial(str(name)):
            cause = ("numerados", None)
        elif not name_is_cluable(str(name), min_words=self._min_words):
            cause = ("nome recusado", None)
        else:
            # …e as verificações do depósito, pelo MESMO código
            tally = Tally()
            src = Source("probe", self.chain, contract)
            named = self._finder.named_token(
                src, token_id, tally, known=TokenRead(token_uri=uri, metadata=meta))
            cand = (self._finder.verify(src, token_id, named[0], named[1], tally)
                    if named is not None else None)
            cause = None if cand is not None else cause_of(tally)
        rep.with_arweave.add(cause)
        rep.today.add(today or cause)
