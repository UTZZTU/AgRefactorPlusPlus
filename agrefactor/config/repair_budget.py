# Shared repair-attempt defaults and safety limits.

from __future__ import annotations


MIN_REPAIR_ATTEMPTS = 1
DEFAULT_TESTBENCH_REPAIR_ATTEMPTS = 3
DEFAULT_CANDIDATE_REPAIR_ATTEMPTS = 3
REPAIR_ATTEMPT_SAFETY_CEILING = 10


def validate_repair_attempts(
    value: int,
    *,
    field_name: str,
    allow_zero: bool = False,
) -> int:
    """Validate one bounded repair-attempt count and return it unchanged."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    minimum = 0 if allow_zero else MIN_REPAIR_ATTEMPTS
    if value < minimum:
        if allow_zero:
            raise ValueError(
                f"{field_name} must be between 0 and "
                f"{REPAIR_ATTEMPT_SAFETY_CEILING}"
            )
        raise ValueError(
            f"{field_name} must be between {MIN_REPAIR_ATTEMPTS} and "
            f"{REPAIR_ATTEMPT_SAFETY_CEILING}"
        )
    if value > REPAIR_ATTEMPT_SAFETY_CEILING:
        raise ValueError(
            f"{field_name} exceeds repair-attempt safety ceiling "
            f"{REPAIR_ATTEMPT_SAFETY_CEILING}"
        )
    return value
