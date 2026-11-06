"""O reveal sem lixo base64 — pedido do Pedro depois da hunt #34.

O QUE ACONTECEU. O tesouro da #34 era uma posição da Uniswap V3, com tudo na
cadeia: o tokenURI é um `data:application/json;base64,…` de 13 005 caracteres
(e a imagem lá dentro, outro `data:` de 9 142). O reveal imprimiu-o inteiro na
linha "tokenURI at launch". Só o https era tratado; tudo o resto saía tal e
qual. O post de void e a nota de mutação tinham o mesmo buraco no "live
tokenURI".

MEDIDO A 06/11: o tokenURI da #34 lido na cadeia (RPC público) é o mesmo da
fixture e o commitment da Clue 1 bate com a metadata dele — o hash era
honesto, e verifica-se sem o blob: o tokenURI não faz parte do commitment, e
lê-se na fonte com tokenURI(tokenId). Nenhum aviso do Telegram nem log leva
URIs (medido no código); o lixo só saía nos três posts públicos.

DECIDIDO (Pedro): um `data:` nunca se publica em bruto; no lugar dele, uma
linha curta ("fully on-chain (data: URI)"); o nome é o da metadata,
descodificada; o link do item fica como está; o post nunca passa o limite do
X e, se passasse, corta com elegância.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from finding_memeland.target.commitment import compute_commitment_v2
from finding_memeland.target.selector import metadata_hash
from finding_memeland.target.templates import (
    DATA_URI_LINE,
    X_LONG_POST_MAX,
    TargetWinnerData,
    VoidRevealData,
    fit_x_post,
    item_link_for,
    target_winner_announcement,
    void_reveal,
    x_weighted_len,
)

FIXTURE = Path(__file__).parent / "fixtures" / "target" / "hunt34_tokenuri.txt"
HUNT34_URI = FIXTURE.read_text()
HUNT34_META = json.loads(base64.b64decode(HUNT34_URI.split(",", 1)[1]))
HUNT34_ID = "ethereum:0xc36442b4a4522e871399cd717abdd847ab11fe88:104937"
# public since the reveal: the salt, and the commitment of Clue 1
HUNT34_SALT = "6f29757e973e12836938a3498e460cd8"
HUNT34_COMMITMENT = "09fe5d0a547f1762ad31f22ce487747e0a66d9837331ac9db6b78cccdbcdb3b3"


def _no_raw_data(post: str) -> bool:
    """No data: URI anywhere — the short line aside, which only names one."""
    rest = post.replace(DATA_URI_LINE, "").lower()
    return "data:" not in rest and "base64" not in rest


def _win(**kw) -> TargetWinnerData:
    """Hunt #34's reveal, as the orchestrator builds it (the winner and the
    transfer are synthetic)."""
    args = dict(hunt_n=34, winner_handle="synthetic_winner", time_to_win="1h 36m",
                prize_amount="20,000,000", tx_link="ab" * 32,
                target_name_onchain=HUNT34_META["name"], target_id=HUNT34_ID,
                metadata_sha256=metadata_hash(HUNT34_META), salt=HUNT34_SALT,
                holder=False, non_holder_pct=10, token_uri=HUNT34_URI,
                item_link=item_link_for(HUNT34_ID), artist="")
    args.update(kw)
    return TargetWinnerData(**args)


# --------------------------------------------------------------------------- #
# Hunt #34, as it would have gone out                                           #
# --------------------------------------------------------------------------- #


def test_the_fixture_is_what_the_commitment_sealed():
    assert len(HUNT34_URI) == 13_005
    assert compute_commitment_v2(HUNT34_ID, metadata_hash(HUNT34_META),
                                 HUNT34_SALT) == HUNT34_COMMITMENT


def test_the_reveal_of_34_carries_no_data_uri():
    post = target_winner_announcement(_win())
    assert _no_raw_data(post)
    assert HUNT34_URI[30:90] not in post
    assert DATA_URI_LINE == "fully on-chain (data: URI)"          # Pedro's words
    assert ("  tokenURI at launch: fully on-chain (data: URI) — call tokenURI(tokenId) "
            "on the contract above to read it.\n") in post
    assert len(post) < 2_500                       # the old one: over 14 000


def test_it_names_the_treasure_by_the_decoded_name_and_keeps_the_link():
    post = target_winner_announcement(_win())
    assert "The treasure was “Uniswap - 0.05% - EURT/WETH - 2319.3<>2359.1”" in post
    assert post.count("opensea.io/item/ethereum/0xc36442b4a4522e871399cd717abdd847ab11fe88"
                      "/104937") == 1
    assert "\n\nsee it: opensea.io/item/" in post


def test_anyone_still_verifies_the_commitment_from_the_post_alone():
    post = target_winner_announcement(_win())
    field = {k: re.search(rf"  {k}: (\S+)", post).group(1)
             for k in ("chain", "contract", "tokenId", "metadata_sha256", "salt")}
    target_id = f"{field['chain']}:{field['contract']}:{field['tokenId']}"
    assert compute_commitment_v2(target_id, field["metadata_sha256"],
                                 field["salt"]) == HUNT34_COMMITMENT
    # …and the metadata hash against the token itself, read at the source
    assert field["metadata_sha256"] == metadata_hash(HUNT34_META)


# --------------------------------------------------------------------------- #
# Every data: URI, every post                                                   #
# --------------------------------------------------------------------------- #

DATA_URIS = [
    HUNT34_URI,
    'data:application/json;utf8,{"name":"x","image":"data:image/svg+xml;base64,AAAA"}',
    "data:application/json,%7B%22name%22%3A%22x%22%7D",
    "DATA:text/plain;base64,aGVsbG8=",
    "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
]


@pytest.mark.parametrize("uri", DATA_URIS,
                         ids=["hunt34", "json-utf8", "json-escaped", "text-upper", "svg"])
def test_a_data_uri_is_never_printed(uri):
    post = target_winner_announcement(_win(token_uri=uri))
    assert DATA_URI_LINE in post and _no_raw_data(post)


def test_an_ipfs_uri_is_printed_as_it_was():
    post = target_winner_announcement(_win(token_uri="ipfs://bafy" + "a" * 50 + "/7"))
    assert "  tokenURI at launch: ipfs://bafy" + "a" * 50 + "/7\n" in post


def test_an_https_uri_keeps_its_sentence():
    post = target_winner_announcement(_win(token_uri="https://example.com/meta/7"))
    assert "an https URL, left out so this post carries exactly one link" in post
    assert "example.com" not in post


def test_the_mutation_note_never_prints_a_live_data_uri():
    post = target_winner_announcement(_win(mutated_after_claim=True,
                                           live_token_uri=HUNT34_URI,
                                           live_hash_status="resolved",
                                           live_metadata_sha256="cd" * 32))
    assert f"; live tokenURI {DATA_URI_LINE})." in post and _no_raw_data(post)


@pytest.mark.parametrize("cause", ["mutated", "burned", "unclaimed"])
def test_the_void_post_never_prints_a_data_uri(cause):
    post = void_reveal(VoidRevealData(
        hunt_n=34, cause=cause, target_name_onchain=HUNT34_META["name"],
        target_id=HUNT34_ID, metadata_sha256=metadata_hash(HUNT34_META),
        salt=HUNT34_SALT, token_uri=HUNT34_URI, live_token_uri=HUNT34_URI,
        live_hash_status="resolved", live_metadata_sha256="cd" * 32))
    assert _no_raw_data(post)
    assert f"  tokenURI at launch: {DATA_URI_LINE}" in post
    if cause != "unclaimed":
        assert (f"  live tokenURI: {DATA_URI_LINE} — call tokenURI(tokenId) on the "
                "contract above\n") in post


def test_the_void_post_keeps_an_ipfs_live_uri():
    post = void_reveal(VoidRevealData(
        hunt_n=34, cause="mutated", target_name_onchain="Salt Harbor",
        target_id=HUNT34_ID, metadata_sha256="ab" * 32, salt=HUNT34_SALT,
        token_uri="ipfs://bafyold", live_token_uri="ipfs://bafynew"))
    assert "  live tokenURI: ipfs://bafynew\n" in post


# --------------------------------------------------------------------------- #
# X's limit                                                                     #
# --------------------------------------------------------------------------- #


def test_the_limit_is_the_one_the_code_already_uses_for_long_posts():
    assert X_LONG_POST_MAX == 25_000
    assert x_weighted_len("abc — “x”") == 9
    assert x_weighted_len("日本") == 4 and x_weighted_len("\U0001F438") == 2


def test_no_reveal_can_pass_the_limit_whatever_the_metadata_says():
    """Every field at its worst: the post stays far under the limit, and the
    name and the credit are cut at a word, with "…"."""
    name, credit = "Very Long Name " * 400, "Someone Else " * 400
    worst = _win(target_name_onchain=name, artist=credit,
                 token_uri="ipfs://bafy" + "a" * 20_000, winner_handle="x" * 15,
                 mutated_after_claim=True, live_token_uri="ipfs://" + "b" * 20_000,
                 live_hash_status="resolved", live_metadata_sha256="cd" * 32)
    post = target_winner_announcement(worst)
    assert x_weighted_len(post) < 5_000
    shown = re.search(r"The treasure was “([^”]*)”, by (.*?) — an NFT", post)
    for text, full, limit in ((shown.group(1), name, 300), (shown.group(2), credit, 120)):
        assert len(text) <= limit and text.endswith("…")
        assert full.startswith(text[:-1]) and full[len(text) - 1] == " "   # at a word
    assert post.endswith("The next hunt can begin at any time.")
    cjk = target_winner_announcement(_win(target_name_onchain="宝物 " * 2_000,
                                          artist="\U0001F438" * 2_000))
    assert x_weighted_len(cjk) < 5_000


def test_the_void_post_has_the_same_ceiling():
    post = void_reveal(VoidRevealData(
        hunt_n=34, cause="mutated", target_name_onchain="Name " * 3_000,
        target_id=HUNT34_ID, metadata_sha256="ab" * 32, salt=HUNT34_SALT,
        token_uri="ar://" + "c" * 20_000, live_token_uri="ipfs://" + "d" * 20_000,
        artist="Credit " * 3_000))
    assert x_weighted_len(post) < 5_000


def test_a_post_over_the_limit_is_cut_at_a_line_and_keeps_its_ending():
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(400)) + \
        "\n\nsee it: opensea.io/item/ethereum/0xabc/1\n\nTo the rest of you."
    cut = fit_x_post(text, limit=2_000)
    assert x_weighted_len(cut) <= 2_000
    assert cut.endswith("\n…\n\nsee it: opensea.io/item/ethereum/0xabc/1\n\n"
                        "To the rest of you.")
    assert cut.startswith("line 0 ") and "\nline 1 " in cut
    assert all(line.startswith(("line ", "see it: ")) or line in ("…", "", "To the rest of you.")
               for line in cut.split("\n"))
    assert fit_x_post("short") == "short"


def test_both_posts_go_through_the_limit_even_on_a_field_no_cap_expects():
    """The last resort is wired: a field the caps do not touch (no real one
    is long) still cannot push either post over the limit."""
    post = target_winner_announcement(_win(tx_link="ab" * 20_000))
    assert x_weighted_len(post) <= X_LONG_POST_MAX
    assert post.endswith("\n\nsee it: opensea.io/item/ethereum/"
                         "0xc36442b4a4522e871399cd717abdd847ab11fe88/104937\n\n"
                         "To the rest of you: keep your eyes open. The next hunt can "
                         "begin at any time.")
    void = void_reveal(VoidRevealData(
        hunt_n=34, cause="unclaimed", target_name_onchain="Salt Harbor",
        target_id=HUNT34_ID, metadata_sha256="ab" * 32, salt="s" * 40_000))
    assert x_weighted_len(void) <= X_LONG_POST_MAX
