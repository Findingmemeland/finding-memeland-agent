"""/harvest <fonte> — colher dos contratos de criador de uma plataforma.

O PORQUÊ (10/10, passo B do Arweave). Os mints ao calhas em Base não dão
alvos (0 guardados em 101 candidatos, cinco corridas) e o Pedro quer alvos em
Base "pelo menos de vez em quando", sem escolher à mão. O sítio onde a arte
1/1 de Base vive são os contratos de criador da Manifold. Duas sondas
mediram-nos (06–07/10): 210 contratos sorteados, 58 peças testadas, 10
passariam as cinco verificações com Arweave aceite. O Arweave foi aceite
(passo A); isto é a sonda a GUARDAR.

O SORTEIO É O DA SONDA, e não escolhe: um número de criação do deployer ao
calhas (uniforme — um artista com uma peça pesa o mesmo que um com
duzentas), o contrato dessa criação, uma peça ao calhas dentro dele. Sem
lista, sem catálogo, sem estado entre corridas. Cada contrato sai no máximo
uma vez por corrida, e um contrato que já tem na despensa o máximo de alvos
por contrato (regra de 28/09: dois) é saltado antes de se lhe ler uma peça.

DEPOIS DO SORTEIO NADA É DESTA FONTE: os filtros de nome são os da colheita
por blocos (`harvest.sift`, o mesmo código) e o depósito é o de sempre —
as cinco verificações e as guardas, unicidade por último.

UM CANÁRIO, porque "0 alvos" tem de querer dizer que não havia: antes de
sortear, a criação-canário do deployer tem de se conseguir ler até ao
contrato. É um NÚMERO de criação, medido uma vez — nunca um endereço. Um
sorteio que não vê as criações diz que está cego; não devolve zero.

O QUE NUNCA SAI DAQUI: um contrato, um tokenId, um nome. Só contagens e
causas.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from .harvest import HarvestBlind, HarvestReport, sift
from .probe import CreatorContracts, DeployerSampler, ProbeBlind, _kind
from .refresh import uri_is_content_addressed

# A criação-canário do deployer da Manifold em Base: a primeira. Medida a
# 10/10 pelo RPC público de Base — lê-se até a um contrato. Um número, nunca
# um endereço.
MANIFOLD_BASE_CANARY_CREATION = 1


class CallCounter:
    """Conta as chamadas feitas por uma porta — para o relatório dizer quanto
    RPC o sorteio custou, medido e não estimado."""

    def __init__(self, fn: Callable):
        self._fn = fn
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1
        return self._fn(*args, **kwargs)


@dataclass
class CreatorHarvestReport:
    """O que a colheita de uma fonte viu, POR CAUSA. Só contagens.

    A cabeça é a do sorteio (contratos); as causas das peças lidas são as do
    `HarvestReport` da colheita por blocos, com os mesmos nomes."""

    source: str = ""
    chain: str = ""
    universe: int = 0            # criações do deployer (todos os tipos)
    asked: int = 0               # contratos sorteados
    no_event: int = 0            # criação sem o evento: outro tipo de contrato
    not_erc721: int = 0
    empty: int = 0               # sem token 1: vazio, ou queimado
    full: int = 0                # já tem na despensa o máximo por contrato
    lost: dict = field(default_factory=dict)   # NOSSO: o RPC falhou — por tipo
    read: int = 0                # peças que chegaram a ser lidas
    rpc_calls: int = 0           # chamadas RPC do sorteio, medidas
    pieces: HarvestReport = field(default_factory=HarvestReport)

    @property
    def reads(self) -> dict:
        """As leituras das peças que ficaram — para o depósito. Têm nomes:
        NUNCA se mostram."""
        return self.pieces.reads

    def render(self) -> str:
        head = [f"{self.asked} contrato(s) sorteado(s) de {self.universe} criações"]
        for label, n in (("não-ERC-721", self.not_erc721),
                         ("sem peças", self.empty),
                         ("criação de outro tipo", self.no_event),
                         ("contrato-cheio", self.full)):
            if n:
                head.append(f"{label} {n}")
        if self.lost:
            kinds = ", ".join(f"{k} {n}" for k, n in sorted(self.lost.items()))
            head.append(f"não medidos-NOSSO {sum(self.lost.values())} ({kinds})")
        head.append(f"{self.read} peça(s) lida(s)")
        head.append(f"{self.pieces.kept} alvo(s) de "
                    f"{len(self.pieces.contracts)} contrato(s)")
        return (f"{self.source}/{self.chain}: "
                + " · ".join(head + self.pieces.causes()))


class CreatorHarvester:
    """Sorteia contratos de um deployer, uma peça em cada um, e devolve as
    que passam os filtros de nome — `(refs, relatório)`, como o colector de
    blocos. Não deposita: quem chama deposita, pelo caminho de sempre.

    Portas (todas injectadas; levantam em falha de transporte):
      sampler                         DeployerSampler
      eth_call(contract, data)        -> hex   (revert levanta com `.revert`)
      token_uri(contract, token_id)   -> str | None   (None = não existe)
      read_meta(contract, token_id)   -> TokenRead | None   (o leitor da
                                         despensa)
      accepts_image(uri)              -> bool   (a regra da despensa)
      counters                        os CallCounter cujas chamadas são "o
                                      RPC do sorteio" — só para o relatório
    """

    def __init__(self, *, source: str, chain: str, sampler: DeployerSampler,
                 eth_call, token_uri, read_meta, accepts_image=None,
                 canary_creation: int = 1,
                 counters: tuple[CallCounter, ...] = (),
                 rng: random.Random | None = None, min_words: int = 2):
        if not chain:
            raise ValueError("CreatorHarvester precisa da cadeia da fonte (R1)")
        self.source = source
        self.chain = chain
        self._sampler = sampler
        self._contracts = CreatorContracts(eth_call=eth_call, token_uri=token_uri)
        self._read_meta = read_meta
        self._accepts_image = accepts_image or uri_is_content_addressed
        self._canary = int(canary_creation)
        self._counters = tuple(counters)
        self._rng = rng or random.SystemRandom()
        self._min_words = int(min_words)

    def _calls(self) -> int:
        return sum(c.count for c in self._counters)

    def harvest(self, n: int, *, full: Callable[[str], bool] | None = None,
                every: int = 10,
                notify: Callable[[str], None] | None = None,
                ) -> tuple[list[str], CreatorHarvestReport]:
        """`n` contratos ao calhas, sem repetir. `full(contract)` diz se o
        contrato já tem na despensa o máximo de alvos — pergunta-se ANTES de
        lhe procurar uma peça, que é onde está o custo.

        Levanta HarvestBlind quando o sorteio não vê: RPC sem arquivo,
        deployer sem criações, ou a criação-canário que não se lê. Qualquer
        outra falha do RPC ao abrir levanta tal e qual — quem chama diz
        "não medida"."""
        note = notify or (lambda _t: None)
        rep = CreatorHarvestReport(source=self.source, chain=self.chain)
        started = self._calls()
        try:
            head = self._sampler.head()
            rep.universe = self._sampler.total(head)
        except ProbeBlind as e:
            raise HarvestBlind(str(e)) from None
        if self._canary and self._sampler.contract_of(
                min(self._canary, rep.universe), head) is None:
            raise HarvestBlind(
                "o sorteio não vê as criações do deployer (a criação-canário "
                "não se lê até ao contrato)")

        refs: list[str] = []
        picks = self._rng.sample(range(1, rep.universe + 1),
                                 min(max(1, n), rep.universe))
        for i, k in enumerate(picks, 1):
            if every and i % every == 0:
                note(f"harvest {self.source}: {i}/{len(picks)} contratos · "
                     f"{rep.read} peça(s) lida(s) · {rep.pieces.kept} alvo(s)")
            rep.asked += 1
            try:
                contract = self._sampler.contract_of(k, head)
                if contract is None:
                    rep.no_event += 1
                    continue
                if not self._contracts.is_erc721(contract):
                    rep.not_erc721 += 1
                    continue
                if full is not None and full(contract):
                    rep.full += 1
                    continue
                last = self._contracts.last_token(contract)
            except Exception as e:  # noqa: BLE001 — NOSSO: nada concluído
                rep.lost[_kind(e)] = rep.lost.get(_kind(e), 0) + 1
                continue
            if last == 0:
                rep.empty += 1
                continue
            rep.read += 1
            ref = sift(rep.pieces, chain=self.chain, contract=contract,
                       tid=self._rng.randrange(1, last + 1),
                       read_meta=self._read_meta,
                       accepts_image=self._accepts_image,
                       min_words=self._min_words)
            if ref is None:
                continue
            rep.pieces.contracts.add(contract)
            rep.pieces.kept += 1
            refs.append(ref)
        rep.rpc_calls = self._calls() - started
        note(f"harvest: {rep.render()}")
        return refs, rep
