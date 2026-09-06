"""Target templates — the public face of v2: brand line, claim format,
commitment v2 label, split id, verifiable win AND void, one cashtag, no
URLs in clues, no old-game vocabulary."""

from __future__ import annotations

import hashlib
import re

from finding_memeland.target.commitment import (
    compute_commitment_v2,
    verify_commitment_v2,
)
from finding_memeland.target.templates import (
    POST_REPLY_FORMAT,
    POST_REPLY_UNRESOLVED_LINK,
    POST_REPLY_WRONG_DOOR_TARGET,
    TARGET_CLUE_ONE_EXPLAINER,
    TargetWinnerData,
    VoidRevealData,
    commitment_block,
    target_clue_followup,
    target_clue_one,
    target_winner_announcement,
    void_reveal,
)

TARGET_ID = "ethereum:0x3b3ee1931dc30c1957379fac9aba94d1c48a5405:41234"
META_HASH = hashlib.sha256(b"meta").hexdigest()
SALT = "ab" * 16
COMMIT = compute_commitment_v2(TARGET_ID, META_HASH, SALT)
URL_RE = re.compile(r"https?://")


def cashtags(text: str) -> int:
    return len(re.findall(r"\$[A-Za-z]+", text))


# --------------------------------------------------------------------------- #
# Clue 1 and follow-ups                                                        #
# --------------------------------------------------------------------------- #


def test_clue_one_brand_line_format_and_commitment_label():
    post = target_clue_one(11, "patience is a coin nobody spends", "100,000,000",
                           COMMIT)
    assert "Hunt #11 is live." in post
    assert "the prize is always on Base. the treasure can be anywhere onchain." in post
    assert "chain:contract:tokenId" in post and "marketplace link" in post
    assert f"commitment v2: {COMMIT}" in post
    assert "integrity:" not in post                       # v1 label never
    assert "Check pinned for rules." in post
    assert cashtags(post) == 1                            # X: one cashtag
    assert not URL_RE.search(post)


def test_clue_one_never_uses_old_game_vocabulary():
    post = target_clue_one(11, "clue", "1", COMMIT)
    low = post.lower()
    for bad in ("code in its bio", "code in its description", "persona",
                "account", "fake", "keeps the relic"):
        assert bad not in low, bad
    assert "made by someone else" in low and "stays with its owner" in low


def test_clue_one_holder_line_optional():
    assert "non-holders win 10%" in target_clue_one(1, "c", "1", COMMIT)
    assert "non-holders" not in target_clue_one(1, "c", "1", COMMIT,
                                                non_holder_pct=None)


def test_followup_carries_the_claim_hint_and_no_url():
    post = target_clue_followup(3, "a quiet clue", "still nothing? \U0001F438")
    assert post.startswith("3rd Clue:")
    assert "chain:contract:tokenId" in post
    assert not URL_RE.search(post)


# --------------------------------------------------------------------------- #
# Commitment block — a reader can recompute from the rendered text            #
# --------------------------------------------------------------------------- #


def recompute_from_block(text: str) -> str:
    chain = re.search(r"chain: (\S+)", text).group(1)
    contract = re.search(r"contract: (\S+)", text).group(1)
    token = re.search(r"tokenId: (\S+)", text).group(1)
    meta = re.search(r"  metadata_sha256: (\S+)", text).group(1)
    salt = re.search(r"salt: (\S+)", text).group(1)
    return compute_commitment_v2(f"{chain}:{contract}:{token}", meta, salt)


def test_commitment_block_is_split_and_recomputes():
    block = commitment_block(target_id=TARGET_ID, metadata_sha256=META_HASH,
                             salt=SALT)
    assert TARGET_ID not in block                         # nunca junto (X link)
    assert "  chain: ethereum\n" in block
    assert recompute_from_block(block) == COMMIT
    assert "keys sorted" in block                         # como recomputar o meta hash


# --------------------------------------------------------------------------- #
# Winner announcement                                                          #
# --------------------------------------------------------------------------- #


def winner(**kw):
    base = dict(hunt_n=11, winner_handle="@finder", time_to_win="3 days 2 hours",
                prize_amount="100,000,000", tx_link="https://basescan.org/tx/0xabc",
                target_name_onchain="Salt Harbor #3", target_id=TARGET_ID,
                metadata_sha256=META_HASH, salt=SALT)
    base.update(kw)
    return TargetWinnerData(**base)


def test_winner_announcement_reveals_and_verifies():
    post = target_winner_announcement(winner())
    assert "Congratulations @finder" in post
    assert "Salt Harbor #3" in post
    assert "stays with its owner" in post and "never touched it" in post
    assert verify_commitment_v2(TARGET_ID, META_HASH, SALT, COMMIT)
    assert recompute_from_block(post) == COMMIT
    assert cashtags(post) == 1
    assert post.count("http") == 1                        # só o tx link
    low = post.lower()
    assert "persona" not in low and "profile" not in low and "relic" not in low
    assert "turn notifications on" not in low             # engagement-bait
    assert "note:" not in post                            # sem mutação, sem nota


def test_winner_announcement_non_holder_note():
    post = target_winner_announcement(winner(holder=False, non_holder_pct=10))
    assert "non-holders win 10%" in post


def test_winner_announcement_mutation_after_claim_is_noted_not_punished():
    live = hashlib.sha256(b"changed").hexdigest()
    post = target_winner_announcement(winner(mutated_after_claim=True,
                                             live_metadata_sha256=live))
    assert "changed AFTER the winning claim" in post
    assert live in post and "prize paid in full" in post
    assert recompute_from_block(post) == COMMIT           # o comprometido, não o live
    burned = target_winner_announcement(winner(mutated_after_claim=True))
    assert "unresolvable" in burned


# --------------------------------------------------------------------------- #
# Void — as verifiable as a win                                                #
# --------------------------------------------------------------------------- #


def void(**kw):
    base = dict(hunt_n=11, cause="mutated", target_name_onchain="Salt Harbor #3",
                target_id=TARGET_ID, metadata_sha256=META_HASH, salt=SALT,
                live_metadata_sha256=hashlib.sha256(b"x").hexdigest())
    base.update(kw)
    return VoidRevealData(**base)


def test_void_reveal_publishes_every_ingredient():
    post = void_reveal(void())
    assert "Hunt #11 is void" in post and "changed by its owner" in post
    assert "back to the vault" in post and "Nobody loses anything" in post
    assert recompute_from_block(post) == COMMIT
    assert "live metadata_sha256: " + hashlib.sha256(b"x").hexdigest() in post
    assert not URL_RE.search(post)


def test_void_reveal_burned_and_unclaimed_variants():
    burned = void_reveal(void(cause="burned", live_metadata_sha256=None))
    assert "burned mid-hunt" in burned and "unresolvable" in burned
    unclaimed = void_reveal(void(cause="unclaimed"))
    assert "nobody found it" in unclaimed
    assert "live metadata_sha256" not in unclaimed         # não houve falha live
    assert recompute_from_block(unclaimed) == COMMIT


def test_void_reveal_relaunch_line():
    assert "new treasure, starts shortly" in void_reveal(void(relaunching=True))
    assert "can begin at any time" in void_reveal(void(relaunching=False))


# --------------------------------------------------------------------------- #
# System replies                                                               #
# --------------------------------------------------------------------------- #


def test_system_replies_have_no_urls_and_teach_the_format():
    for r in (POST_REPLY_FORMAT, POST_REPLY_UNRESOLVED_LINK,
              POST_REPLY_WRONG_DOOR_TARGET):
        assert not URL_RE.search(r)
        assert "chain:contract:tokenId" in r
    assert "needs the chain" in POST_REPLY_FORMAT
    assert "Base" not in TARGET_CLUE_ONE_EXPLAINER.split("\n")[0]  # 1ª linha sem cadeia
