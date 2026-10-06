from uuid import uuid4

import pytest

from app.api.v1.queue import _staff_queue_department
from app.core.errors import ApiProblemError


def test_admin_can_choose_any_department_or_all_departments() -> None:
    requested = uuid4()
    assert _staff_queue_department("admin", None, requested) == requested
    assert _staff_queue_department("admin", None, None) is None


def test_staff_queue_is_limited_to_the_assigned_department() -> None:
    assigned = uuid4()
    assert _staff_queue_department("doctor", assigned, None) == assigned
    assert _staff_queue_department("nurse", assigned, assigned) == assigned


def test_staff_cannot_open_another_department_or_unassigned_queue() -> None:
    assigned = uuid4()
    with pytest.raises(ApiProblemError) as cross_department:
        _staff_queue_department("doctor", assigned, uuid4())
    assert cross_department.value.status == 403

    with pytest.raises(ApiProblemError) as unassigned:
        _staff_queue_department("nurse", None, None)
    assert unassigned.value.status == 403
