from finding_memeland.content.integrity import (
    compute_integrity_hash,
    generate_claim_code,
    verify_integrity_hash,
)


def test_hash_is_deterministic_and_verifiable():
    h = compute_integrity_hash("12345", "ABCDEFGH", "salt-xyz")
    assert verify_integrity_hash("12345", "ABCDEFGH", "salt-xyz", h)
    assert len(h) == 64  # SHA-256 hex


def test_hash_changes_with_any_ingredient():
    base = compute_integrity_hash("12345", "ABCDEFGH", "salt-xyz")
    assert compute_integrity_hash("99999", "ABCDEFGH", "salt-xyz") != base
    assert compute_integrity_hash("12345", "ZZZZZZZZ", "salt-xyz") != base
    assert compute_integrity_hash("12345", "ABCDEFGH", "other") != base


def test_verify_rejects_wrong_hash():
    assert not verify_integrity_hash("12345", "ABCDEFGH", "salt-xyz", "0" * 64)


def test_claim_code_avoids_ambiguous_chars():
    code = generate_claim_code(12)
    assert len(code) == 12
    assert not (set(code) & {"O", "0", "I", "1"})
