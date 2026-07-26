
"""Shared bounded-repair defaults and validation."""

from __future__ import annotations

DEFAULT_TESTBENCH_REPAIR_ATTEMPTS = 3
DEFAULT_CANDIDATE_REPAIR_ATTEMPTS = 3
MIN_REPAIR_ATTEMPTS = 1
REPAIR_ATTEMPT_SAFETY_CEILING = 20


def validate_repair_attempts(
    value: int,
    *,
    field_name: str = "repair_attempts",
    allow_zero: bool = False,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    minimum = 0 if allow_zero else MIN_REPAIR_ATTEMPTS
    if value < minimum:
        if allow_zero:
            raise ValueError(f"{field_name} must be non-negative")
        raise ValueError(
            f"{field_name} must be at least {MIN_REPAIR_ATTEMPTS}"
        )
    if value > REPAIR_ATTEMPT_SAFETY_CEILING:
        raise ValueError(
            f"{field_name} exceeds safety ceiling "
            f"{REPAIR_ATTEMPT_SAFETY_CEILING}"
        )
    return value
