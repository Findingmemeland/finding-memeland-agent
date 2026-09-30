"""Colher alvos da cadeia — todos os NFTs alguma vez mintados.

O PORQUÊ (23/09, decisão do Pedro). Durante treze hunts o universo foram
três contratos partilhados de Ethereum, porque o `/fill` SORTEIA DENTRO DE
UM CONTRATO e isso exige contratos grandes que enumerem. As hunts #12 e #13
saíram do mesmo contrato, e a meio da segunda já havia jogadores a varrê-lo.

A frase do Pedro que decide o desenho:

    "o que torna o nosso nft difícil de procurar não é ser um entre milhões
     de um só contrato. é ser um entre todos os nfts alguma vez criados."

Uma colecção com UM único NFT é, por isso, um esconderijo melhor do que o
Foundation. Para o `Source` não vale nada — rende um alvo e esgota-se. Para
a despensa vale tudo.

DUAS TENTATIVAS FALHADAS ANTES DESTA, e ficam escritas porque a terceira só
faz sentido contra elas:

  · Pedir colecções ao marketplace, ordenadas por capitalização. Traz
    exactamente a parte da distribuição garantida a falhar — PFPs e edições,
    dez mil peças com o mesmo nome — e gasta a quota toda lá. Em Ethereum
    deu UM contrato utilizável em 300 colecções.
  · Ordenar por data de criação. Traz o que foi mintado esta semana: 27
    kits de intrusão, colecções de dois tokens, contratos vazios.

O problema comum é a LENTE. Qualquer catálogo ordena, e a ordenação escolhe
por nós. A cadeia não ordena nada.

O QUE ISTO FAZ. Um bloco ao calhas, os mints desse bloco, o nome de cada um.
Sem colecções, sem capitalização, sem quota — é o nosso RPC. Um bloco de
2021 e um de ontem têm a mesma probabilidade, e um contrato com um NFT
aparece exactamente uma vez, que é o que merece.

E apaga a exigência do ERC721Enumerable de vez: não sorteamos dentro de um
contrato, lemos o que foi mintado. A esmagadora maioria dos contratos NFT
não implementa enumeração — passam todos a ser elegíveis.

O QUE ISTO NÃO FAZ. Não verifica nada. Não fala com o mercado. Os cinco
testes — metadata endereçada por conteúdo, nome com duas palavras, imagem
legível, dono EOA, nome único no mercado inteiro — correm no DEPÓSITO
(`TargetFinder.deposit`), sobre as referências que isto devolve. Um alvo
colhido aqui é um candidato a candidato.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .refresh import uri_is_content_addressed, uri_kind
from .selector import name_qualifies, normalize_name

# keccak256("Transfer(address,address,uint256)") — o mesmo que o holdings.py
# usa para provar continuidade de saldo. Aqui serve para o contrário: achar
# o instante em que uma peça passou a existir.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64

# Onde começar a sortear blocos, por cadeia. É uma OPTIMIZAÇÃO DE CUSTO, não
# uma regra de correcção: um bloco anterior à existência de NFTs devolve zero
# mints e custa uma chamada, mais nada. Em Ethereum os primeiros NFTs com o
# Transfer no formato ERC-721 aparecem no fim de 2017 (bloco ~4,6M); sortear
# de 1 gastaria um quinto das chamadas em blocos garantidamente vazios. Em
# Base a cadeia inteira é da era dos NFTs.
HARVEST_SPAN_START = {"ethereum": 4_600_000, "base": 1}
# Nunca sortear os blocos mais recentes: um bloco ainda sujeito a reorg pode
# mudar de conteúdo, e um alvo que desaparece da cadeia depois de depositado
# é um alvo que morre no /prepare — ou pior, depois.
HARVEST_SAFE_DEPTH = 1_000

# Um contrato ou uma cadeia que o jogo não conhece não pode ser colhido: as
# pistas, o claim e o resolver de links só sabem destas.
HARVEST_CHAINS = ("ethereum", "base")


def parse_canary(spec: str) -> tuple[int, int]:
    """"bloco:mints" → (bloco, mints). (0, 0) se vazio ou torto.

    Devolver (0, 0) em vez de levantar é deliberado e seguro, porque o
    `MintHarvester` trata (0, 0) como "sem canário" e RECUSA varrer. Uma
    configuração torta não pode transformar-se numa colheita cega."""
    try:
        block, mints = (int(x) for x in str(spec or "").strip().split(":"))
    except ValueError:
        return 0, 0
    if block <= 0 or mints <= 0:
        return 0, 0
    return block, mints


def _as_int(topic: str) -> int:
    return int(topic, 16)


def mints_in_logs(logs: Sequence[dict]) -> list[tuple[str, int]]:
    """Os mints de ERC-721 nestes logs: [(contrato, token_id)].

    O DISCRIMINADOR É O NÚMERO DE TÓPICOS, e é exacto, não é heurística.
    O evento Transfer tem a mesma assinatura em ERC-20 e ERC-721, mas no
    ERC-721 o terceiro argumento (tokenId) é `indexed` e no ERC-20 (value)
    não é. Logo: quatro tópicos = NFT, três tópicos = token fungível. Um
    filtro por valor ou por heurística de contrato erraria; este não.

    Mint = remetente é o endereço zero. Queimas (destinatário zero) não
    contam: a peça deixou de existir e não serve de alvo.
    """
    out: list[tuple[str, int]] = []
    for log in logs:
        topics = log.get("topics") or []
        if len(topics) != 4:
            continue                      # ERC-20, ou outro evento
        if str(topics[0]).lower() != TRANSFER_TOPIC:
            continue
        if _as_int(str(topics[1])) != 0:
            continue                      # não é mint
        if _as_int(str(topics[2])) == 0:
            continue                      # mint para o zero: ignorar
        contract = str(log.get("address") or "").lower()
        if not (contract.startswith("0x") and len(contract) == 42):
            continue
        try:
            out.append((contract, _as_int(str(topics[3]))))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------- #
# O nome                                                                        #
# --------------------------------------------------------------------------- #
#
# A REGRA É A DO PEDRO, E É UMA SÓ: o nome tem de dar para uma pista. Tudo o
# resto — tamanho do contrato, plataforma, cadeia — é indiferente.
#
# O teste de DUAS PALAVRAS usa o normalize_name/name_qualifies de PRODUÇÃO,
# de propósito. O colector do marketplace usava uma tira de serial mais
# agressiva, e isso foi útil lá (era preciso agrupar peças dentro de uma
# colecção). Aqui seria um erro: filtrar por regras diferentes das do
# depósito faz-nos ou colher coisas que morrem a seguir, ou deitar fora
# coisas que o depósito aceitaria. A única regra honesta é a mesma.
#
# O que se acrescenta é o que produção não cobre e a colheita de 23/09
# mostrou existir: nomes que passam as duas palavras e mesmo assim não são
# títulos de obra.
_DOMAINISH = re.compile(r"\.(eth|sol|xyz|com|io|crypto|nft|dao|x)$", re.I)
_PERCENTISH = re.compile(r"^\s*\d+([.,]\d+)?\s*%")
_COORDISH = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)")
_TRAILING_JUNK = re.compile(r"[\s​-‏⁠]*[⚠️✅❗‼⁉️🔺]*\s*$")


# Série numerada (29/09, decisão do Pedro). A segunda colheita real mostrou
# o padrão: dos 24 candidatos que passaram o nome, 16 morreram no teste de
# unicidade do mercado — "Cool Cat #123" tem duas palavras, mas há milhares
# de "Cool Cat". Deixá-los chegar lá gastava a única chamada paga em
# candidatos que já se sabia que morriam. A unicidade continua a ser o juiz
# final; isto só lhe poupa os casos óbvios.
#
# Mais estreito do que o `normalize_name` de produção DE PROPÓSITO: esse
# arranca qualquer número no fim, incluindo anos ("Summer 2021" → "Summer"),
# e aqui um ano é parte do título, não um número de série.
_HASH_SERIAL = re.compile(r"#\s*\d+")
_NO_SERIAL = re.compile(r"\bn[oº]\.?\s*\d+", re.IGNORECASE)
_TAIL_NUMBER = re.compile(r"(?<![\d.,])(\d{3,})\s*$")


def looks_serial(raw: str) -> bool:
    """O nome parece uma peça de uma série numerada?

    Sim: "#123" em qualquer sítio, "No. 7", ou um número de 3+ algarismos
    no fim que não seja um ano (1900–2099). Não: "Vol 2", "Part 3" — um
    número curto no fim pode ser o título de uma obra — nem "Summer 2021"
    ou "August 19, 2024".

    Decisão do Pedro, com o custo dito na altura: perde algumas obras 1/1
    que tenham "#1" no nome."""
    s = (raw or "").strip()
    if _HASH_SERIAL.search(s) or _NO_SERIAL.search(s):
        return True
    m = _TAIL_NUMBER.search(s)
    if m:
        n = int(m.group(1))
        return not (len(m.group(1)) == 4 and 1900 <= n <= 2099)
    return False


def name_is_cluable(raw: str, *, min_words: int = 2) -> bool:
    """O nome dá para escrever uma pista sobre ele?

    Os domínios saem por uma razão que não é estética: `vitalik.eth` e
    `yoso167.base.eth` são NOMES DE UTILIZADOR — identidades de pessoas. Uma
    hunt de meio bilião apontada à conta de alguém é outra coisa que não um
    jogo. E o aviso de imitação que os marketplaces lhes colam ao fim (⚠)
    fazia-os escapar a um teste que exigisse terminar em `.eth`: dois
    passaram assim, na colheita de 23/09. Por isso limpa-se a cauda primeiro.
    """
    s = _TRAILING_JUNK.sub("", (raw or "").strip()).strip()
    if not s:
        return False
    if _DOMAINISH.search(s) or _PERCENTISH.match(s) or _COORDISH.search(s):
        return False
    return name_qualifies(normalize_name(s), min_words=min_words)


# --------------------------------------------------------------------------- #
# O varrimento                                                                  #
# --------------------------------------------------------------------------- #


class HarvestBlind(RuntimeError):
    """O canário falhou: não sabemos ler mints nesta cadeia agora.

    R8/R2. Um `get_logs` que devolve lista vazia para todos os blocos — nó
    sem arquivo, filtro errado, cadeia errada, provedor a truncar em
    silêncio — é INDISTINGUÍVEL de 'varremos e não havia mints'. Sem
    canário, uma tarefa diária passa semanas a não encontrar nada e o
    relatório mostra blocos varridos a subir alegremente. É o mesmo modo de
    falha que o EraDiscovery já tinha apanhado, e a mesma resposta."""


@dataclass
class HarvestReport:
    """O que a colheita viu, POR CAUSA.

    Até 29/09 havia um só "ilegíveis", e na primeira corrida real ele valia
    1297 em 2675 mints sem que se soubesse o que era: RPC em baixo (nosso),
    token queimado, metadata num servidor HTTP, metadata em Arweave. Quatro
    coisas com quatro respostas diferentes — uma delas (o Arweave) é uma
    decisão de produto pendente que só se toma com este número à frente."""

    blocks: int = 0
    mints: int = 0
    named: int = 0
    kept: int = 0
    rejected_name: int = 0
    series: int = 0              # nome numerado ("#123") — peça de uma série
    drops: int = 0               # contratos que mintaram muitas peças no bloco
    unavailable: int = 0         # NOSSO: o RPC ou o gateway rebentou
    # 29/09: o mesmo número, POR ONDE falhou (rpc / gateway / gateway-não-json
    # / outro). E os defeitos do próprio NFT que antes caíam aqui — metadata
    # que não é um objecto, tokenURI ilegível, `data:` que não descodifica —
    # saem para `bad_meta`: não são nossos e não se contam como nossos.
    unavailable_kinds: dict = field(default_factory=dict)
    bad_meta: dict = field(default_factory=dict)
    gone: int = 0                # tokenURI reverte: queimado ou inexistente
    no_name: int = 0             # metadata sem campo "name"
    uri_not_ca: dict = field(default_factory=dict)    # tokenURI fora de IPFS
    image_not_ca: dict = field(default_factory=dict)  # imagem fora de IPFS
    contracts: set = field(default_factory=set)
    # A leitura de cada ref que FICOU (30/09), para o depósito não a pedir
    # outra vez: nas colheitas de 29/09, 14 candidatos de Base caíram na
    # releitura de metadata que o mesmo gateway tinha servido minutos antes.
    # Tem nomes — NUNCA se mostra (repr=False, fora do render).
    reads: dict = field(default_factory=dict, repr=False)

    @staticmethod
    def _kinds(d: dict) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(d.items()))

    def render(self) -> str:
        parts = [f"{self.kept} alvo(s) de {len(self.contracts)} contrato(s)",
                 f"{self.blocks} bloco(s), {self.mints} mint(s)"]
        for label, n in (("nome recusado", self.rejected_name),
                         ("numerados", self.series),
                         ("drops", self.drops),
                         ("sem nome", self.no_name),
                         ("queimados", self.gone),
                         ("indisponível-NOSSO", self.unavailable)):
            if n:
                parts.append(f"{label} {n}")
        if self.unavailable and self.unavailable_kinds:
            parts[-1] = (f"indisponível-NOSSO {self.unavailable} "
                         f"({self._kinds(self.unavailable_kinds)})")
        if self.bad_meta:
            # "defeito-DELES", não "metadata-inválida" (29/09): o grupo passou
            # a ter o pin morto, que não é metadata inválida — é metadata que
            # um gateway disse não ter.
            parts.append(f"defeito-DELES {sum(self.bad_meta.values())} "
                         f"({self._kinds(self.bad_meta)})")
        if self.uri_not_ca:
            parts.append(f"tokenURI fora de IPFS ({self._kinds(self.uri_not_ca)})")
        if self.image_not_ca:
            parts.append(f"imagem fora de IPFS ({self._kinds(self.image_not_ca)})")
        return " · ".join(parts)


class MintHarvester:
    """Varre blocos ao calhas e devolve referências `chain:contrato:tokenId`.

    Portas injectadas, todas levantando em falha de transporte:
      latest_block()                    -> int
      get_logs(from_block, to_block)    -> [log]   (já filtrado por Transfer)
      read_meta(contract, token_id)     -> TokenRead | None
          (None = tokenURI reverte; metadata None = URI fora de IPFS)

    `canary_block` e `canary_mints` são MEDIDOS quando o bloco é fixado,
    nunca estimados, e a igualdade é exacta — um provedor que trunca a
    resposta em silêncio devolve uma página parcial e passaria um teste de
    ">= 1" com folga."""

    def __init__(self, *, chain: str, latest_block, get_logs, read_meta,
                 canary_block: int = 0, canary_mints: int = 0,
                 span_start: int = 1,
                 rng: random.Random | None = None,
                 min_words: int = 2):
        if not chain:
            raise ValueError("MintHarvester precisa da cadeia que varre (R1)")
        self._chain = chain
        self._latest = latest_block
        self._get_logs = get_logs
        self._read_meta = read_meta
        self._canary = int(canary_block)
        self._canary_mints = int(canary_mints)
        self._span_start = max(1, int(span_start))
        self._rng = rng or random.SystemRandom()
        self._min_words = int(min_words)

    def canary_passes(self) -> bool:
        """Sem bloco fixado não há canário — e então não há varrimento.

        Deliberadamente NÃO devolve True quando o canário não está
        configurado. Um canário opcional é um canário que ninguém liga, e
        passa a existir só no nome."""
        if not (self._canary and self._canary_mints):
            return False
        try:
            logs = self._get_logs(self._canary, self._canary)
        except Exception:  # noqa: BLE001 — transporte conta como cego
            return False
        return len(mints_in_logs(logs)) == self._canary_mints

    def harvest(self, n_blocks: int, *, span: tuple[int, int] | None = None,
                max_per_contract: int = 2,
                max_reads_per_contract: int = 3,
                max_mints_per_block: int = 40,
                drop_threshold: int = 5,
                every: int = 20,
                notify: Callable[[str], None] | None = None,
                ) -> tuple[list[str], HarvestReport]:
        """`n_blocks` blocos ao calhas dentro de `span` (por omissão, do
        `span_start` da cadeia até ao bloco seguro mais recente).

        `max_per_contract` existe porque um único bloco pode conter um drop
        de 500 peças do mesmo contrato, e deixá-las entrar todas repunha o
        problema que isto vem resolver: a despensa dominada por um contrato.
        Dois por contrato por corrida chega.

        `max_reads_per_contract` É O LIMITE QUE FALTAVA (29/09). O de cima
        conta os que FICAM; um contrato cujos nomes não servem nunca lá
        chegava, e o colector lia-lhe as 500 peças, uma a uma, cada leitura
        até 25 s num gateway lento. A primeira corrida real passou mais de
        uma hora sem acabar. Três tentativas por contrato chegam para saber
        se ele tem nomes que prestem; depois disso é gasto puro.

        `max_mints_per_block` é a segunda cinta: nenhum bloco sozinho pode
        custar mais do que isto, por muitos contratos que tenha.

        `every`: uma linha de progresso a cada tantos blocos, como o /fill.
        Uma corrida longa sem notícias é indistinguível de uma pendurada.

        `drop_threshold` (29/09, decisão do Pedro): um contrato que minta
        MAIS do que isto no mesmo bloco é um drop — edição, PFP, série — e
        é saltado sem uma única leitura. Um artista 1/1 minta uma peça de
        cada vez. O custo, dito: um artista que minte 6 obras 1/1 numa só
        transacção perde-se. O número é um parâmetro, e o relatório conta
        os drops saltados para se ver se está bem posto."""
        note = notify or (lambda _t: None)
        if not self.canary_passes():
            raise HarvestBlind(
                f"canário falhou em {self._chain} (bloco {self._canary}, "
                f"{self._canary_mints} mints esperados) — não varro às cegas")

        latest = self._latest()
        lo, hi = span or (self._span_start, latest)
        hi = min(hi, latest)
        if lo >= hi:
            raise HarvestBlind(f"intervalo de blocos vazio: [{lo}, {hi}]")

        rep = HarvestReport()
        seen: dict[str, int] = {}          # quantos FICARAM, por contrato
        tried: dict[str, int] = {}         # quantos foram LIDOS, por contrato
        refs: list[str] = []
        for i in range(1, max(1, n_blocks) + 1):
            if every and i % every == 0:
                note(f"harvest {self._chain}: {i}/{n_blocks} blocos · "
                     f"{rep.kept} alvo(s) de {len(rep.contracts)} contrato(s)")
            block = self._rng.randrange(lo, hi)
            try:
                logs = self._get_logs(block, block)
            except Exception:  # noqa: BLE001 — bloco perdido, não é veredicto
                continue
            rep.blocks += 1
            found = mints_in_logs(logs)
            rep.mints += len(found)        # o que a cadeia TEM, não o que lemos
            per_contract: dict[str, int] = {}
            for contract, _tid in found:
                per_contract[contract] = per_contract.get(contract, 0) + 1
            drops = {c for c, n in per_contract.items() if n > drop_threshold}
            rep.drops += len(drops)
            for contract, tid in found[:max_mints_per_block]:
                if contract in drops:
                    continue               # drop: nem uma leitura
                if seen.get(contract, 0) >= max_per_contract:
                    continue
                if tried.get(contract, 0) >= max_reads_per_contract:
                    continue
                tried[contract] = tried.get(contract, 0) + 1
                try:
                    read = self._read_meta(contract, tid)
                except Exception as e:  # noqa: BLE001 — NOSSO, salvo se disser que não
                    kind = getattr(e, "kind", None) or "outro"
                    if getattr(e, "theirs", False):
                        rep.bad_meta[kind] = rep.bad_meta.get(kind, 0) + 1
                    else:
                        rep.unavailable += 1
                        rep.unavailable_kinds[kind] = rep.unavailable_kinds.get(kind, 0) + 1
                    continue
                if read is None:
                    rep.gone += 1
                    continue
                meta = read.metadata
                if not isinstance(meta, dict):
                    # O leitor só resolve URIs endereçados por conteúdo;
                    # os outros voltam com metadata None. Conta-se o tipo.
                    k = uri_kind(read.token_uri)
                    rep.uri_not_ca[k] = rep.uri_not_ca.get(k, 0) + 1
                    continue
                # A IMAGEM também tem de estar em IPFS (29/09). O depósito
                # exige-o, e sem esta verificação aqui a primeira corrida
                # passou-lhe 143 candidatos que ele recusou todos — cada um
                # com leituras gastas dos dois lados. Não custa rede: é só
                # olhar para a string que já temos.
                image = str(meta.get("image") or "")
                if not uri_is_content_addressed(image):
                    k = uri_kind(image)
                    rep.image_not_ca[k] = rep.image_not_ca.get(k, 0) + 1
                    continue
                name = meta.get("name")
                if not name:
                    rep.no_name += 1
                    continue
                name = str(name)
                rep.named += 1
                if looks_serial(name):
                    rep.series += 1
                    continue
                if not name_is_cluable(name, min_words=self._min_words):
                    rep.rejected_name += 1
                    continue
                seen[contract] = seen.get(contract, 0) + 1
                rep.contracts.add(contract)
                rep.kept += 1
                ref = f"{self._chain}:{contract}:{tid}"
                refs.append(ref)
                rep.reads[ref] = read
        note(f"harvest: {rep.render()}")
        return refs, rep
