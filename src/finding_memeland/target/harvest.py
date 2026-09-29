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
    blocks: int = 0
    mints: int = 0
    named: int = 0
    kept: int = 0
    unreadable: int = 0          # tokenURI/metadata não respondeu
    rejected_name: int = 0
    contracts: set = field(default_factory=set)

    def render(self) -> str:
        return (f"{self.kept} alvo(s) de {self.contracts.__len__()} contrato(s) "
                f"· {self.blocks} bloco(s), {self.mints} mint(s) · "
                f"nome recusado {self.rejected_name} · ilegíveis {self.unreadable}")


class MintHarvester:
    """Varre blocos ao calhas e devolve referências `chain:contrato:tokenId`.

    Portas injectadas, todas levantando em falha de transporte:
      latest_block()                    -> int
      get_logs(from_block, to_block)    -> [log]   (já filtrado por Transfer)
      read_name(contract, token_id)     -> str | None

    `canary_block` e `canary_mints` são MEDIDOS quando o bloco é fixado,
    nunca estimados, e a igualdade é exacta — um provedor que trunca a
    resposta em silêncio devolve uma página parcial e passaria um teste de
    ">= 1" com folga."""

    def __init__(self, *, chain: str, latest_block, get_logs, read_name,
                 canary_block: int = 0, canary_mints: int = 0,
                 span_start: int = 1,
                 rng: random.Random | None = None,
                 min_words: int = 2):
        if not chain:
            raise ValueError("MintHarvester precisa da cadeia que varre (R1)")
        self._chain = chain
        self._latest = latest_block
        self._get_logs = get_logs
        self._read_name = read_name
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
        Uma corrida longa sem notícias é indistinguível de uma pendurada."""
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
            for contract, tid in found[:max_mints_per_block]:
                if seen.get(contract, 0) >= max_per_contract:
                    continue
                if tried.get(contract, 0) >= max_reads_per_contract:
                    continue
                tried[contract] = tried.get(contract, 0) + 1
                try:
                    name = self._read_name(contract, tid)
                except Exception:  # noqa: BLE001
                    rep.unreadable += 1
                    continue
                if not name:
                    rep.unreadable += 1
                    continue
                rep.named += 1
                if not name_is_cluable(name, min_words=self._min_words):
                    rep.rejected_name += 1
                    continue
                seen[contract] = seen.get(contract, 0) + 1
                rep.contracts.add(contract)
                rep.kept += 1
                refs.append(f"{self._chain}:{contract}:{tid}")
        note(f"harvest: {rep.render()}")
        return refs, rep
