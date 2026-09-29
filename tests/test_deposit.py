"""Depósito: alvos nomeados entram na despensa sem passar pelo sorteio.

O PORQUÊ (23/09, decisão do Pedro). O `fill` sorteia dentro de contratos
grandes. Isso afunila o jogo: o disfarce é o alvo poder ser qualquer NFT
alguma vez mintado, e se o universo são três contratos não há disfarce —
foi o que os hunts #12 e #13 mostraram, ambos do mesmo contrato.

Uma colecção com UM único NFT é um esconderijo melhor do que o Foundation.
Para o `Source` não vale nada (rende um alvo e esgota-se); para a despensa
vale tudo, porque a despensa sempre guardou alvos concretos.

O QUE ESTES TESTES TÊM DE PROVAR, e é só isto:

1. NENHUM TESTE É RELAXADO. Um alvo depositado passa exactamente pelo mesmo
   `named_token` + `verify` que um sorteado. Se o depósito fosse uma porta
   lateral mais permissiva, seria a maneira mais fácil de deitar abaixo o
   jogo todo — e ninguém daria por ela até um reveal falhar em público.

2. A CADEIA TEM DE ESTAR ABERTA DOS DOIS LADOS. Um alvo numa cadeia sem
   provedor público lê-se bem aqui, pelo RPC com chave, e rebenta depois no
   live check, a meio da hunt, com pista publicada. O depósito é uma porta
   nova para o modo de falha que as guardas do wiring existem para impedir.

3. UMA LINHA TORTA NÃO PODE MATAR O RESTO. A lista vem de um ficheiro
   gerado; se a linha 7 estiver mal, as outras 200 têm de entrar na mesma.
"""
from __future__ import annotations

import random

from finding_memeland.target.prepare import (
    Larder,
    Source,
    TargetFinder,
    parse_ref,
)
from finding_memeland.target.refresh import TokenRead

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
IPFS = "ipfs://bafkreiaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _meta(name="Grease Pencil Gospel", image=IPFS):
    return {"name": name, "image": image, "description": "d"}


def _finder(*, read=None, image=(True, 10), eoa=True, unique=True,
            sources=None):
    calls: dict[str, list] = {"read": [], "image": [], "eoa": [], "uniq": []}

    def read_token(chain, contract, tid):
        calls["read"].append((chain, contract, tid))
        return read if read is not None else TokenRead(
            token_uri=IPFS, metadata=_meta(f"Name {tid} Of Two"))

    def probe_image(uri):
        calls["image"].append(uri)
        return image

    def owner_is_eoa(chain, contract, tid):
        calls["eoa"].append((chain, contract, tid))
        return eoa

    def name_is_unique(base, chain, contract, tid):
        calls["uniq"].append(base)
        return unique

    f = TargetFinder(
        sources=sources or [Source("foundation", "ethereum", A)],
        total_supply=lambda c, k: 1000,
        token_by_index=lambda c, k, i: i + 1,
        read_token=read_token, probe_image=probe_image,
        owner_is_eoa=owner_is_eoa, name_is_unique=name_is_unique,
        rng=random.Random(0), now_iso=lambda: "2026-09-23T00:00:00Z")
    return f, calls


OPEN = {"ethereum", "base"}
def _ok(c):
    return c in OPEN


# --------------------------------------------------------------------------- #
# 1. Nenhum teste é relaxado                                                    #
# --------------------------------------------------------------------------- #


def test_a_deposited_target_runs_every_check_a_drawn_one_runs():
    """O TESTE QUE IMPORTA. Metadata, imagem, dono, unicidade — os quatro."""
    f, calls = _finder()
    lar = Larder()
    rep = f.deposit(lar, [f"base:{B}:42"], chain_ok=_ok)
    assert rep.added == 1 and lar.size() == 1
    assert calls["read"] == [("base", B, 42)]
    assert calls["image"] and calls["eoa"] == [("base", B, 42)]
    assert calls["uniq"] == ["Name 42 Of Two"]


def test_metadata_that_is_not_content_addressed_is_refused():
    """Sem isto a peça podia mutar a meio da hunt e partir o commitment."""
    f, _ = _finder(read=TokenRead(
        token_uri="https://example.com/1.json",
        metadata=_meta(image="https://example.com/1.png")))
    lar = Larder()
    rep = f.deposit(lar, [f"base:{B}:42"], chain_ok=_ok)
    assert rep.added == 0 and lar.size() == 0
    # Contado pelo TIPO de URI da imagem, e NÃO como 'metadata' (29/09):
    # misturados, "a imagem está num servidor HTTP" e "o RPC falhou" eram o
    # mesmo número, e a primeira colheita real não se conseguia ler.
    assert rep.rejected.not_ca == {"http": 1}
    assert rep.rejected.metadata == 0


def test_our_rpc_failing_is_not_blamed_on_the_candidate():
    """R8 no depósito: uma leitura que rebenta é NOSSA. Antes contava como
    'metadata' e dizia ao operador que o NFT não prestava."""
    f, _ = _finder()

    def boom(chain, contract, tid):
        raise ConnectionError("rpc down")
    f._read_token = boom
    rep = f.deposit(Larder(), [f"base:{B}:1"], chain_ok=_ok)
    assert rep.added == 0
    assert rep.rejected.unavailable == 1 and rep.rejected.metadata == 0


def test_every_gateway_failing_is_ours_not_a_dead_pin():
    """O probe só LEVANTA quando todos os gateways rebentaram — isso é
    nosso. Um pin morto é o gateway a RESPONDER sem bytes. Antes, os dois
    eram 'imagem'."""
    f, _ = _finder()

    def boom(uri):
        raise ConnectionError("all gateways down")
    f._probe_image = boom
    rep = f.deposit(Larder(), [f"base:{B}:1"], chain_ok=_ok)
    assert rep.rejected.unavailable == 1 and rep.rejected.image == 0


def test_the_deposit_report_shows_the_split_causes():
    f, _ = _finder(read=TokenRead(
        token_uri=IPFS, metadata=_meta(image="https://arweave.net/xyz")))
    rep = f.deposit(Larder(), [f"base:{B}:1"], chain_ok=_ok)
    assert "imagem-fora-de-IPFS (arweave 1)" in rep.render()


def test_a_one_word_name_is_refused():
    f, _ = _finder(read=TokenRead(token_uri=IPFS, metadata=_meta(name="Solo")))
    lar = Larder()
    assert f.deposit(lar, [f"base:{B}:9"], chain_ok=_ok).rejected.name == 1


def test_an_image_that_does_not_answer_is_refused():
    """Hunt #11: URI perfeito, zero bytes."""
    f, _ = _finder(image=None)
    lar = Larder()
    assert f.deposit(lar, [f"base:{B}:9"], chain_ok=_ok).rejected.image == 1


def test_a_contract_owner_is_refused():
    f, _ = _finder(eoa=False)
    lar = Larder()
    assert f.deposit(lar, [f"base:{B}:9"], chain_ok=_ok).rejected.owner == 1


def test_a_repeated_name_is_refused():
    f, _ = _finder(unique=False)
    lar = Larder()
    assert f.deposit(lar, [f"base:{B}:9"], chain_ok=_ok).rejected.unique == 1


def test_uniqueness_is_asked_last_because_it_is_the_only_paid_call():
    """Um alvo que morre no dono não pode gastar uma chamada ao mercado. Com
    429 candidatos na lista, a ordem é a diferença entre uma corrida e uma
    factura."""
    f, calls = _finder(eoa=False)
    f.deposit(Larder(), [f"base:{B}:{i}" for i in range(20)], chain_ok=_ok)
    assert calls["uniq"] == []


# --------------------------------------------------------------------------- #
# 2. A cadeia tem de estar aberta dos dois lados                                #
# --------------------------------------------------------------------------- #


def test_a_chain_with_no_provider_is_refused_and_named():
    """O modo de falha que isto impede: o alvo entrava, a hunt lançava, e o
    live check rebentava a meio com a pista já publicada."""
    f, calls = _finder()
    lar = Larder()
    rep = f.deposit(lar, [f"zora:{B}:1"], chain_ok=_ok)
    assert rep.added == 0 and rep.chain_closed == 1
    assert rep.closed_chains == {"zora"}
    assert calls["read"] == [], "nem sequer chegou a ler — barato e certo"
    assert "zora" in rep.render(), "o operador tem de saber QUAL cadeia"


def test_a_closed_chain_does_not_stop_the_open_ones():
    f, _ = _finder()
    lar = Larder()
    rep = f.deposit(lar, [f"zora:{B}:1", f"base:{B}:2", f"solana:{B}:3"],
                    chain_ok=_ok)
    assert rep.added == 1 and rep.chain_closed == 2
    assert rep.closed_chains == {"zora", "solana"}


def test_without_chain_ok_only_the_chains_sources_already_reads_pass():
    """O valor por omissão é o seguro: sem guarda injectada, só entra o que
    o SOURCES já lê hoje. Um default permissivo aqui seria uma armadilha
    para quem chamasse isto sem ler a docstring."""
    f, _ = _finder(sources=[Source("foundation", "ethereum", A)])
    lar = Larder()
    rep = f.deposit(lar, [f"base:{B}:1", f"ethereum:{A}:2"])
    assert rep.added == 1 and rep.chain_closed == 1
    assert rep.closed_chains == {"base"}


# --------------------------------------------------------------------------- #
# 3. Uma linha torta não mata o resto                                           #
# --------------------------------------------------------------------------- #


def test_a_malformed_line_is_counted_and_skipped():
    f, _ = _finder()
    lar = Larder()
    rep = f.deposit(lar, ["lixo", "", f"base:{B}:1", "base:0x123:4",
                          f"base:{B}:nao-e-numero"], chain_ok=_ok)
    assert rep.asked == 5
    assert rep.added == 1
    assert rep.bad_ref == 4     # "lixo", "", contrato curto, id não-numérico


def test_a_target_already_in_the_larder_is_not_added_twice():
    f, _ = _finder()
    lar = Larder()
    f.deposit(lar, [f"base:{B}:7"], chain_ok=_ok)
    rep = f.deposit(lar, [f"base:{B}:7"], chain_ok=_ok)
    assert rep.added == 0 and rep.duplicate == 1 and lar.size() == 1


def test_a_target_already_used_in_a_hunt_is_refused():
    """A despensa lembra-se do que já saiu. Um depósito não pode ressuscitar
    um alvo já revelado."""
    f, _ = _finder()
    lar = Larder(used=[f"base:{B}:7"])
    rep = f.deposit(lar, [f"base:{B}:7"], chain_ok=_ok)
    assert rep.added == 0 and rep.duplicate == 1


def test_the_deposit_never_needs_the_sources_rpcs():
    """Depositar não sorteia, portanto não chama totalSupply. Isto é o que
    permite depositar alvos de uma cadeia que não tem fonte nenhuma no
    SOURCES — que é o ponto todo."""
    def boom(chain, contract):
        raise AssertionError("o depósito não pode pedir totalSupply")

    f, _ = _finder()
    f._total_supply = boom
    lar = Larder()
    assert f.deposit(lar, [f"base:{B}:1"], chain_ok=_ok).added == 1


# --------------------------------------------------------------------------- #
# O parser                                                                      #
# --------------------------------------------------------------------------- #


def test_the_ref_parser_accepts_what_the_collector_writes():
    assert parse_ref(f"base:{B}:42") == ("base", B, 42)


def test_a_checksummed_address_pasted_from_an_explorer_is_accepted():
    """Um endereço copiado do BaseScan vem em checksum — maiúsculas e
    minúsculas misturadas, às vezes com o `0X` em maiúscula. Recusá-lo
    seria rejeitar um alvo legítimo por uma diferença que não existe para
    a cadeia. Sai sempre em minúsculas, que é a forma que a despensa usa."""
    assert parse_ref(f"  ethereum : {A.upper()} : 7  ") == ("ethereum", A, 7)
    mixed = "0xB932a70A57673d89f4acfFBE830e8ed7f75Fb9e0"
    got = parse_ref(f"Ethereum:{mixed}:11385")
    assert got == ("ethereum", mixed.lower(), 11385)


def test_enormous_token_ids_are_accepted():
    """Os basenames de Base têm ids de 78 dígitos. Um parser que assumisse
    uint64 rejeitava alvos legítimos por uma razão que não é do jogo."""
    big = "5" * 78
    got = parse_ref(f"base:{B}:{big}")
    assert got is not None and got[2] == int(big)


def test_the_parser_refuses_what_is_not_a_reference():
    for bad in ["", "lixo", f"base:{B}", f"{B}:42", "base:0x123:4",
                f"base:{B}:-1", f"base:{B}:1.5", f"base:{B}:0x10"]:
        assert parse_ref(bad) is None, bad
