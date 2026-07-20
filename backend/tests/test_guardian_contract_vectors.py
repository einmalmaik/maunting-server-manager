from __future__ import annotations

import json
from pathlib import Path

from services.guardian_runtime_compiler import canonical_payload_hash


def test_golden_vectors_canonical_payload_hash() -> None:
    # Path is relative to the test file's directory or root of repository
    # Let's find tests/fixtures/guardian_contract_vectors.json
    root = Path(__file__).resolve().parents[2]
    vectors_path = root / "tests" / "fixtures" / "guardian_contract_vectors.json"
    assert vectors_path.is_file(), f"Golden vectors file not found at {vectors_path}"

    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)

    for index, vector in enumerate(vectors):
        payload = vector["payload"]
        expected_hash = vector["payload_hash"]
        calculated_hash = canonical_payload_hash(payload)
        assert calculated_hash == expected_hash, (
            f"Hash mismatch at vector index {index}.\n"
            f"Calculated: {calculated_hash}\n"
            f"Expected:   {expected_hash}"
        )
