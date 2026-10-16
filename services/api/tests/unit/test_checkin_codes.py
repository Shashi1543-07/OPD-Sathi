from uuid import uuid4

import pytest

from app.domain.checkin import checkin_code, checkin_code_digest, is_checkin_code

KEY = b"k" * 32


def test_codes_are_stable_per_version_and_distinct_across_points_and_hospitals() -> None:
    hospital, point = uuid4(), uuid4()
    first = checkin_code(key=KEY, hospital_id=hospital, point_id=point, version=1)
    # The same QR can be shown again: nothing random is stored.
    assert first == checkin_code(key=KEY, hospital_id=hospital, point_id=point, version=1)
    assert is_checkin_code(first)
    replaced = checkin_code(key=KEY, hospital_id=hospital, point_id=point, version=2)
    other_point = checkin_code(key=KEY, hospital_id=hospital, point_id=uuid4(), version=1)
    other_hospital = checkin_code(key=KEY, hospital_id=uuid4(), point_id=point, version=1)
    other_key = checkin_code(key=b"x" * 32, hospital_id=hospital, point_id=point, version=1)
    assert len({first, replaced, other_point, other_hospital, other_key}) == 5


def test_digest_is_a_fixed_length_lookup_value_and_inputs_are_validated() -> None:
    code = checkin_code(key=KEY, hospital_id=uuid4(), point_id=uuid4(), version=1)
    digest = checkin_code_digest(code)
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert digest != code
    assert not is_checkin_code("short")
    assert not is_checkin_code(code + "x")
    assert not is_checkin_code("!" * 43)
    with pytest.raises(ValueError):
        checkin_code(key=b"short", hospital_id=uuid4(), point_id=uuid4(), version=1)
    with pytest.raises(ValueError):
        checkin_code(key=KEY, hospital_id=uuid4(), point_id=uuid4(), version=0)
