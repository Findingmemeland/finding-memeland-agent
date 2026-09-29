"""Colher da cadeia: um entre todos os NFTs alguma vez criados.

O PORQUÊ. Treze hunts saíram de três contratos, porque o /fill sorteia
DENTRO de um contrato e isso obriga a contratos grandes que enumerem. As
#12 e #13 saíram do mesmo, e havia jogadores a varrê-lo a meio da segunda.

    "o que torna o nosso nft difícil de procurar não é ser um entre milhões
     de um só contrato. é ser um entre todos os nfts alguma vez criados."
                                                        — Pedro, 23/09

Duas tentativas anteriores falharam pela mesma razão: pedimos catálogos a um
marketplace, e qualquer catálogo ORDENA. Por capitalização vêm PFPs e
edições — a parte garantida a falhar. Por data de criação vem o que foi
mintado esta semana, incluindo 27 kits de intrusão. A lente escolhia por nós.

A cadeia não ordena. Um bloco ao calhas não tem opinião.

O que estes testes fixam, por ordem de importância:

1. O DISCRIMINADOR ERC-721 vs ERC-20 é exacto, não é heurística. Errar aqui
   encheria a despensa de tokens fungíveis.
2. O CANÁRIO recusa varrer às cegas. Sem ele, uma tarefa diária passa
   semanas a devolver zero e o relatório parece saudável.
3. NENHUM NOME DE PESSOA entra. Um `.eth` é uma identidade, e uma hunt de
   meio bilião apontada à conta de alguém não é um jogo.
"""
from __future__ import annotations

import random

import pytest

from finding_memeland.target.harvest import (
    TRANSFER_TOPIC,
    HarvestBlind,
    HarvestReport,
    MintHarvester,
    looks_serial,
    mints_in_logs,
    name_is_cluable,
)
from finding_memeland.target.refresh import TokenRead

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
ZERO = "0x" + "0" * 64


def _topic(n: int) -> str:
    return "0x" + f"{n:064x}"


def _mint(contract=A, tid=7, to=99):
    return {"address": contract,
            "topics": [TRANSFER_TOPIC, ZERO, _topic(to), _topic(tid)]}


# --------------------------------------------------------------------------- #
# 1. ERC-721 vs ERC-20                                                          #
# --------------------------------------------------------------------------- #


def test_a_nft_mint_is_recognised():
    assert mints_in_logs([_mint(A, 7)]) == [(A, 7)]


def test_an_erc20_mint_is_not_a_nft():
    """O TESTE QUE IMPORTA. Mesma assinatura de evento, mesmo tópico zero —
    o que difere é o tokenId ser `indexed` no ERC-721 e o value não ser no
    ERC-20. Quatro tópicos contra três. Sem esta distinção a despensa
    enchia-se de transferências de stablecoins."""
    erc20 = {"address": A,
             "topics": [TRANSFER_TOPIC, ZERO, _topic(99)],
             "data": "0x" + f"{10**18:064x}"}
    assert mints_in_logs([erc20]) == []


def test_a_plain_transfer_is_not_a_mint():
    """Só conta quando a peça passa a existir."""
    moved = {"address": A,
             "topics": [TRANSFER_TOPIC, _topic(5), _topic(9), _topic(7)]}
    assert mints_in_logs([moved]) == []


def test_a_burn_is_not_a_mint():
    """Destinatário zero: a peça deixou de existir e não serve de alvo."""
    burn = {"address": A, "topics": [TRANSFER_TOPIC, _topic(5), ZERO, _topic(7)]}
    assert mints_in_logs([burn]) == []


def test_another_event_with_four_topics_is_ignored():
    other = {"address": A, "topics": ["0x" + "de" * 32, ZERO, _topic(9), _topic(7)]}
    assert mints_in_logs([other]) == []


def test_token_id_zero_is_a_real_token():
    """Muitas colecções começam em 0. Tratá-lo como falso perdia o primeiro
    alvo de cada contrato que o faça."""
    assert mints_in_logs([_mint(A, 0)]) == [(A, 0)]


def test_a_malformed_log_does_not_kill_the_batch():
    logs = [{"address": "lixo", "topics": [TRANSFER_TOPIC, ZERO, _topic(9), _topic(1)]},
            {"topics": []}, _mint(B, 3)]
    assert mints_in_logs(logs) == [(B, 3)]


# --------------------------------------------------------------------------- #
# 2. O canário                                                                  #
# --------------------------------------------------------------------------- #


IPFS_URI = "ipfs://bafkre" + "a" * 50
IPFS_IMG = "ipfs://bafyimg" + "b" * 50


def _read(name="Grease Pencil Gospel", image=IPFS_IMG, token_uri=IPFS_URI):
    return TokenRead(token_uri=token_uri,
                     metadata={"name": name, "image": image})


def _harv(*, logs_by_block=None, canary_block=100, canary_mints=1,
          names=None, reads=None, seed=0, raise_on=()):
    """`names` troca só o nome; `reads` substitui a leitura inteira — um
    TokenRead, None (token queimado) ou uma excepção (o nosso RPC)."""
    calls = {"logs": [], "names": []}

    def get_logs(a, b):
        calls["logs"].append((a, b))
        if a in raise_on:
            raise RuntimeError("rpc down")
        return (logs_by_block or {}).get(a, [])

    def read_meta(contract, tid):
        calls["names"].append((contract, tid))
        if reads and (contract, tid) in reads:
            r = reads[(contract, tid)]
            if isinstance(r, Exception):
                raise r
            return r
        return _read(name=(names or {}).get((contract, tid),
                                            "Grease Pencil Gospel"))

    h = MintHarvester(
        chain="ethereum", latest_block=lambda: 1_000_000,
        get_logs=get_logs, read_meta=read_meta,
        canary_block=canary_block, canary_mints=canary_mints,
        rng=random.Random(seed))
    return h, calls


def test_the_canary_must_match_exactly():
    """Igualdade, não '>= 1'. Um provedor que trunca em silêncio devolve
    uma página parcial — o bloco TEM mints, só que menos — e passaria um
    teste de existência com folga, enquanto a despensa cresce devagar e
    tudo parece saudável."""
    h, _ = _harv(logs_by_block={100: [_mint(A, 1), _mint(A, 2)]}, canary_mints=2)
    assert h.canary_passes()
    h2, _ = _harv(logs_by_block={100: [_mint(A, 1)]}, canary_mints=2)
    assert not h2.canary_passes()


def test_harvest_refuses_when_the_canary_fails():
    h, calls = _harv(logs_by_block={100: []}, canary_mints=1)
    with pytest.raises(HarvestBlind):
        h.harvest(10)
    assert len(calls["logs"]) == 1, "nem tentou varrer"


def test_no_canary_configured_means_no_harvest():
    """Um canário opcional é um canário que ninguém liga."""
    h, _ = _harv(canary_block=0, canary_mints=0)
    assert not h.canary_passes()
    with pytest.raises(HarvestBlind):
        h.harvest(5)


def test_a_transport_failure_on_the_canary_counts_as_blind():
    h, _ = _harv(logs_by_block={100: [_mint()]}, raise_on=(100,))
    assert not h.canary_passes()


# --------------------------------------------------------------------------- #
# 3. O nome                                                                     #
# --------------------------------------------------------------------------- #


def test_domain_names_never_enter():
    """São identidades de pessoas, não obras."""
    for n in ["vitalik.eth", "yoso167.base.eth", "perúesclave.eth ⚠",
              "gödel.eth ⚠️", "nick.sol", "something.xyz"]:
        assert not name_is_cluable(n), n


def test_real_titles_pass():
    for n in ["Grease Pencil Gospel", "Rekt Surveillance", "The Halvening",
              "Oh my Clown!", "Dionysius of Ephesus", "last meal",
              "$243M Theft - August 19, 2024"]:
        assert name_is_cluable(n), n


def test_one_word_and_junk_do_not_pass():
    for n in ["", "   ", "Solo", "#42", "0.64% Voting Power",
              "Lv. 1 Power Gem - (7,54)"]:
        assert not name_is_cluable(n), n


def test_a_serialised_pfp_name_still_fails_on_words():
    """'Tiny Punk #9278' → base 'Tiny Punk' → duas palavras → a regra do
    nome aceita-o, e está certo. Desde 29/09 quem o apanha é o
    `looks_serial`, no colector, ANTES de gastar a chamada paga da
    unicidade — decisão do Pedro depois de 16 em 24 candidatos morrerem lá."""
    assert name_is_cluable("Tiny Punk #9278")
    assert looks_serial("Tiny Punk #9278")


def test_numbered_series_are_recognised():
    for n in ["Cool Cat #123", "#3000 - Candy Stamps", "Punk # 42",
              "Genesis No. 7", "Nº 12 Harbour", "healing lucid glow 12684",
              "Moonbird 4412"]:
        assert looks_serial(n), n


def test_titles_with_years_and_short_numbers_are_not_series():
    """O normalize_name de produção arranca anos ("Summer 2021" →
    "Summer"); aqui um ano é parte do título. E "Vol 2" pode ser uma obra."""
    for n in ["Summer 2021", "$243M Theft - August 19, 2024", "Vol 2",
              "Part 3 of the Harbour", "Grease Pencil Gospel", "The Halvening",
              "Salt Harbor 1999", "Room 42"]:
        assert not looks_serial(n), n


# --------------------------------------------------------------------------- #
# O varrimento                                                                  #
# --------------------------------------------------------------------------- #


def test_refs_come_out_in_the_deposit_format():
    """A saída do colector é a entrada do depósito — se os formatos se
    separarem, descobre-se com uma lista inteira recusada.

    O rng é controlado de propósito. A primeira versão deste teste mandava
    sortear 400 blocos num intervalo de um milhão e esperava acertar nos
    dois que têm mints: uma hipótese em 2500. Um teste que depende da sorte
    do seed não prova nada e falha um dia ao calhas."""
    from finding_memeland.target.prepare import parse_ref

    h, _ = _harv(logs_by_block={100: [_mint()], 500: [_mint(B, 42)]},
                 canary_mints=1)

    class _R:
        def __init__(self):
            self.blocks = iter((500, 100))

        def randrange(self, lo, hi):
            return next(self.blocks)
    h._rng = _R()

    refs, rep = h.harvest(2)
    assert len(refs) == 2, rep.render()
    for r in refs:
        assert parse_ref(r) is not None, r


def _block7(h):
    class _R:
        def randrange(self, lo, hi):
            return 7
    h._rng = _R()


def test_a_drop_is_skipped_without_a_single_read():
    """Decisão do Pedro (29/09): um contrato que minta mais do que o limite
    no mesmo bloco é uma edição, um PFP, uma série — e um artista 1/1 minta
    uma peça de cada vez. Saltado antes de gastar uma leitura."""
    drop = {100: [_mint(A, 1)], 7: [_mint(B, i) for i in range(500)]}
    h, calls = _harv(logs_by_block=drop, canary_mints=1)
    _block7(h)
    refs, rep = h.harvest(3)
    assert refs == [] and calls["names"] == []
    assert rep.drops == 3                   # um drop por bloco visto
    assert rep.mints == 1500                # a cadeia TEM-nos; só não lemos


def test_a_small_batch_under_the_threshold_is_still_read():
    """Cinco no mesmo bloco não é um drop: pode ser um artista a mintar uma
    pequena série de obras distintas. O limite é MAIS do que 5."""
    h, calls = _harv(logs_by_block={100: [_mint(A, 1)],
                                    7: [_mint(B, i) for i in range(5)]},
                     canary_mints=1)
    _block7(h)
    _refs, rep = h.harvest(1)
    assert rep.drops == 0 and calls["names"]


def test_one_contract_never_gives_more_than_two_per_run():
    """Mesmo abaixo do limite de drop, um contrato não enche a despensa."""
    h, _ = _harv(logs_by_block={100: [_mint(A, 1)],
                                7: [_mint(B, i) for i in range(5)]},
                 names={(B, i): f"Distinct Title {chr(65 + i)}" for i in range(5)},
                 canary_mints=1)
    _block7(h)
    refs, rep = h.harvest(3, max_per_contract=2)
    assert len(refs) == 2 and rep.kept == 2


def test_useless_names_cost_three_reads_per_contract_not_all_of_them():
    """O DEFEITO DA PRIMEIRA CORRIDA REAL (29/09). O limite antigo contava
    só os que FICAVAM; um contrato cujos nomes não prestam nunca lá chegava,
    e o colector lia-lhe as peças todas, cada uma até 25 s. Três leituras
    por contrato chegam para saber."""
    h, calls = _harv(logs_by_block={100: [_mint(A, 1)],
                                    7: [_mint(B, i) for i in range(5)]},
                     canary_mints=1, names={(B, i): "Solo" for i in range(5)})
    _block7(h)
    h.harvest(1)
    assert len(calls["names"]) == 3


def test_a_numbered_name_is_skipped_before_the_paid_check():
    """16 dos 24 candidatos da segunda corrida morreram na unicidade do
    mercado — a única chamada paga. Um nome numerado não chega lá."""
    refs, rep = _one_mint(_read(name="Cool Cat #123"))
    assert refs == [] and rep.series == 1


def test_no_single_block_can_cost_more_than_the_cap():
    """Um bloco com 500 contratos diferentes não pode custar 500 leituras."""
    block = [_mint("0x" + f"{i:040x}", 1) for i in range(1, 501)]
    h, calls = _harv(logs_by_block={100: [_mint()], 7: block}, canary_mints=1)

    class _R:
        def randrange(self, lo, hi):
            return 7
    h._rng = _R()
    h.harvest(1, max_mints_per_block=40)
    assert len(calls["names"]) == 40


def test_a_long_run_reports_progress():
    """Uma hora sem notícias é indistinguível de uma corrida pendurada."""
    h, _ = _harv(logs_by_block={100: [_mint()]}, canary_mints=1)
    lines = []

    class _R:
        def randrange(self, lo, hi):
            return 999
    h._rng = _R()
    h.harvest(60, every=20, notify=lines.append)
    progress = [x for x in lines if "/60 blocos" in x]
    assert len(progress) == 3


def _one_mint(read):
    """Um bloco com um só mint de B:3, cuja leitura é `read`."""
    h, _ = _harv(logs_by_block={100: [_mint()], 7: [_mint(B, 3)]},
                 reads={(B, 3): read}, canary_mints=1)

    class _R:
        def randrange(self, lo, hi):
            return 7
    h._rng = _R()
    return h.harvest(1)


# O "ilegíveis" da primeira corrida real eram 1297 mints em 2675 sem causa
# nenhuma. Cada uma destas é uma causa diferente com uma resposta diferente.


def test_our_rpc_failing_is_ours_never_the_nft_s():
    """R8: 'não conseguimos ler' nunca é 'não presta'."""
    refs, rep = _one_mint(ConnectionError("rpc down"))
    assert refs == [] and rep.unavailable == 1
    assert rep.gone == 0 and not rep.uri_not_ca


def test_a_burned_token_is_counted_as_gone():
    refs, rep = _one_mint(None)
    assert refs == [] and rep.gone == 1 and rep.unavailable == 0


def test_a_token_uri_outside_ipfs_is_counted_by_kind():
    """Arweave separado de HTTP: é o número que decide se o Arweave passa
    a ser aceite — decisão do Pedro, pendente desta medição."""
    refs, rep = _one_mint(TokenRead(token_uri="ar://abcdef", metadata=None))
    assert refs == [] and rep.uri_not_ca == {"arweave": 1}
    refs, rep = _one_mint(TokenRead(token_uri="https://api.x.io/1", metadata=None))
    assert rep.uri_not_ca == {"http": 1}


def test_an_image_outside_ipfs_is_refused_here_not_in_the_deposit():
    """O DEFEITO DA PRIMEIRA CORRIDA: o colector só olhava para o nome, e
    passou ao depósito 143 candidatos que ele recusou todos. A imagem vê-se
    na string que já temos — sem rede."""
    refs, rep = _one_mint(_read(image="https://cdn.example.com/1.png"))
    assert refs == [] and rep.image_not_ca == {"http": 1}
    refs, rep = _one_mint(_read(image="https://arweave.net/abc"))
    assert rep.image_not_ca == {"arweave": 1}


def test_metadata_without_a_name_is_counted_apart():
    refs, rep = _one_mint(_read(name=None))
    assert refs == [] and rep.no_name == 1 and rep.rejected_name == 0


def test_a_good_token_still_goes_through():
    refs, rep = _one_mint(_read())
    assert refs == [f"ethereum:{B}:3"] and rep.kept == 1


def test_the_report_names_every_cause_it_saw():
    """O relatório é o que chega ao Telegram. Se uma causa aconteceu e não
    aparece, voltamos a ter um 'ilegíveis' mudo."""
    rep = HarvestReport(unavailable=2, gone=3, no_name=1,
                        uri_not_ca={"arweave": 4, "http": 5},
                        image_not_ca={"http": 6})
    out = rep.render()
    for bit in ("indisponível-NOSSO 2", "queimados 3", "sem nome 1",
                "arweave 4", "http 5", "imagem fora de IPFS (http 6)"):
        assert bit in out, bit


def test_a_lost_block_does_not_abort_the_run():
    h, _ = _harv(logs_by_block={100: [_mint()]}, canary_mints=1, raise_on=(7,))

    class _R:
        def __init__(self):
            self.n = 0

        def randrange(self, lo, hi):
            self.n += 1
            return 7 if self.n == 1 else 100
    h._rng = _R()
    refs, rep = h.harvest(2)
    assert rep.blocks == 1, "o bloco perdido não conta como varrido"
    assert len(refs) == 1


def test_the_chain_is_carried_into_every_ref():
    """Sem a cadeia, a referência é ambígua: o mesmo contrato pode existir
    em Base e em Ethereum e são alvos diferentes."""
    h, _ = _harv(logs_by_block={100: [_mint()]}, canary_mints=1)

    class _R:
        def randrange(self, lo, hi):
            return 100
    h._rng = _R()
    refs, _ = h.harvest(1)
    assert all(r.startswith("ethereum:") for r in refs)
