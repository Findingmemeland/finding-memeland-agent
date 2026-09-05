"""Claim parsing/validation — triples, links, fail-closed matching, secrecy."""

from __future__ import annotations

from finding_memeland.target.claim import (
    ClaimJudge,
    TargetRef,
    claim_shaped,
    extract_target_refs,
    parse_explicit,
    parse_link,
)

ADDR = "0x3B3ee1931Dc30C1957379FAC9aba94D1C48a5405"
TARGET_ID = f"ethereum:{ADDR.lower()}:42"


# --------------------------- explicit triples ------------------------------ #


def test_explicit_triple_canonicalises_chain_case_and_contract():
    refs = parse_explicit(f"acho que é ETH:{ADDR}:42 !")
    assert refs == (TargetRef("ethereum", ADDR.lower(), 42),)
    assert refs[0].id() == TARGET_ID


def test_explicit_triple_unknown_chain_is_ignored():
    assert parse_explicit(f"solami:{ADDR}:42") == ()


def test_chainless_paste_is_shaped_but_never_a_ref():
    text = f"{ADDR}:42"
    assert extract_target_refs(text).refs == ()
    assert claim_shaped(text)


# ------------------------------- links ------------------------------------- #


def test_opensea_assets_and_item_urls():
    for path in ("assets", "item"):
        ref = parse_link(f"https://opensea.io/{path}/ethereum/{ADDR}/42")
        assert ref == TargetRef("ethereum", ADDR.lower(), 42)


def test_opensea_base_chain_url():
    ref = parse_link(f"https://opensea.io/assets/base/{ADDR}/7")
    assert ref.chain == "base"


def test_rarible_defaults_to_ethereum_and_takes_chain_segment():
    assert parse_link(f"https://rarible.com/token/{ADDR}:42").chain == "ethereum"
    assert parse_link(f"https://rarible.com/token/polygon/{ADDR}:42").chain == "polygon"


def test_zora_collect_url():
    ref = parse_link(f"https://zora.co/collect/eth:{ADDR}/42")
    assert ref == TargetRef("ethereum", ADDR.lower(), 42)


def test_token_id_in_query_string():
    ref = parse_link(f"https://etherscan.io/token/{ADDR}?tokenId=42#inventory")
    assert ref == TargetRef("ethereum", ADDR.lower(), 42)


def test_scan_domains_imply_chain():
    assert parse_link(f"https://etherscan.io/nft/{ADDR}/42").chain == "ethereum"
    assert parse_link(f"https://basescan.org/nft/{ADDR}/42").chain == "base"


def test_link_with_contract_but_no_chain_goes_unresolved():
    url = f"https://example.org/x/{ADDR}/42"
    ext = extract_target_refs(f"olha {url}")
    assert ext.refs == ()
    assert ext.unresolved_links == (url,)


def test_slug_only_marketplace_link_goes_unresolved():
    url = "https://foundation.app/@artist/salt-harbor/1"
    ext = extract_target_refs(url)
    assert ext.refs == ()
    assert ext.unresolved_links == (url,)


def test_trailing_punctuation_stripped_and_no_double_count():
    text = f"é isto: https://rarible.com/token/{ADDR}:42."
    ext = extract_target_refs(text)
    assert len(ext.refs) == 1 and ext.refs[0].id() == TARGET_ID


def test_plain_chatter_is_not_claim_shaped():
    assert not claim_shaped("gm, quando é a próxima pista?")
    assert not claim_shaped("https://example.org/blog/42")


# ------------------------------ judging ------------------------------------ #


def test_judge_matches_explicit_and_link_forms():
    judge = ClaimJudge(target_id=TARGET_ID)
    assert judge.judge(f"ethereum:{ADDR}:42").matched
    assert judge.judge(f"https://opensea.io/assets/ethereum/{ADDR}/42").matched


def test_judge_wrong_chain_or_token_does_not_match():
    judge = ClaimJudge(target_id=TARGET_ID)
    assert not judge.judge(f"base:{ADDR}:42").matched
    assert not judge.judge(f"ethereum:{ADDR}:43").matched


def test_judge_resolver_turns_slug_link_into_match():
    judge = ClaimJudge(target_id=TARGET_ID)
    url = "https://superrare.com/artwork-v2/salt-harbor-42"

    def resolver(u):
        assert u == url
        return TargetRef("ethereum", ADDR.lower(), 42)

    v = judge.judge(url, resolve_link=resolver)
    assert v.matched and v.unresolved == 0


def test_judge_resolver_failure_is_fail_closed():
    judge = ClaimJudge(target_id=TARGET_ID)
    url = "https://superrare.com/artwork-v2/salt-harbor-42"

    def broken(u):
        raise RuntimeError("api em baixo")

    v = judge.judge(url, resolve_link=broken)
    assert not v.matched and v.unresolved == 1
    v2 = judge.judge(url)                      # sem resolver injectado
    assert not v2.matched and v2.unresolved == 1


def test_verdict_and_judge_never_leak_target():
    judge = ClaimJudge(target_id=TARGET_ID)
    v = judge.judge(f"base:{ADDR}:1")
    for s in (repr(judge), str(judge), v.render(), repr(v)):
        assert ADDR.lower() not in s.lower()
        assert TARGET_ID not in s


def test_token_id_leading_zeros_canonicalise():
    judge = ClaimJudge(target_id=TARGET_ID)
    assert judge.judge(f"ethereum:{ADDR}:042").matched
