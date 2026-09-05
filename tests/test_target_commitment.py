"""Commitment v2 — protocol shape, verification, and the enumeration-oracle
property that motivated salting the metadata hash."""

from __future__ import annotations

import hashlib

from finding_memeland.target.commitment import (
    compute_commitment_v2,
    generate_salt,
    verify_commitment_v2,
)

TID = "base:0xabc:7"
MH = hashlib.sha256(b"{}").hexdigest()


def test_round_trip():
    salt = generate_salt()
    h = compute_commitment_v2(TID, MH, salt)
    assert verify_commitment_v2(TID, MH, salt, h)
    assert verify_commitment_v2(TID, MH, salt, h.upper() + " \n"
                                .strip() or h)   # tolerant of case/space


def test_protocol_is_exact_concatenation_sha256():
    """The recipe the litepaper publishes must be reproducible by anyone
    with sha256 alone — freeze it here."""
    salt = "00ff"
    expect = hashlib.sha256(f"{TID}{MH}{salt}".encode()).hexdigest()
    assert compute_commitment_v2(TID, MH, salt) == expect


def test_any_component_change_breaks_verification():
    salt = generate_salt()
    h = compute_commitment_v2(TID, MH, salt)
    assert not verify_commitment_v2("base:0xabc:8", MH, salt, h)
    assert not verify_commitment_v2(TID, hashlib.sha256(b"x").hexdigest(),
                                    salt, h)
    assert not verify_commitment_v2(TID, MH, "othersalt", h)


def test_no_enumeration_oracle_without_the_salt():
    """An attacker holding Clue 1 (the commitment) and a candidate's
    target_id + metadata hash — everything public — must not be able to
    test the candidate. Distinct salts give distinct commitments for the
    SAME public inputs, so the published value is untestable pre-reveal."""
    h1 = compute_commitment_v2(TID, MH, generate_salt())
    h2 = compute_commitment_v2(TID, MH, generate_salt())
    assert h1 != h2


def test_salts_are_high_entropy_hex():
    s = generate_salt()
    assert len(s) == 32 and int(s, 16) >= 0
    assert generate_salt() != s
