
"""Product defaults and safety ceilings for per-tool timeouts."""

from __future__ import annotations

DEFAULT_CSIM_TIMEOUT_S = 120
DEFAULT_CSYNTH_TIMEOUT_S = 600
CSIM_TIMEOUT_SAFETY_CEILING = 600
CSYNTH_TIMEOUT_SAFETY_CEILING = 3600


def _validate_timeout(
    value: int,
    *,
    field_name: str,
    ceiling: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1 second")
    if value > ceiling:
        raise ValueError(
            f"{field_name} exceeds safety ceiling {ceiling} seconds"
        )
    return value


def validate_csim_timeout_s(value: int) -> int:
    return _validate_timeout(
        value,
        field_name="csim_timeout_s",
        ceiling=CSIM_TIMEOUT_SAFETY_CEILING,
    )


def validate_csynth_timeout_s(value: int) -> int:
    return _validate_timeout(
        value,
        field_name="csynth_timeout_s",
        ceiling=CSYNTH_TIMEOUT_SAFETY_CEILING,
    )
