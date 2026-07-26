
"""Testbench-generation product profiles and bounded controls."""

from __future__ import annotations

from enum import Enum


class TestGenerationProfile(str, Enum):
    """Explicitly separate bounded lightweight and coverage-enhanced runs."""

    LIGHTWEIGHT = "lightweight"
    COVERAGE_ENHANCED = "coverage-enhanced"


MIN_TEST_GENERATION_COUNT = 1
TEST_GENERATION_COUNT_SAFETY_CEILING = 20

DEFAULT_PUBLIC_COVERAGE_ROUNDS = 3
DEFAULT_HIDDEN_COVERAGE_ROUNDS = 6
DEFAULT_PUBLIC_GENERATION_TRAJECTORIES = 3
DEFAULT_HIDDEN_GENERATION_TRAJECTORIES = 3

# Compatibility export for older internal callers. New product surfaces use
# independent Public and Hidden trajectory fields.
DEFAULT_TEST_GENERATION_TRAJECTORIES = (
    DEFAULT_PUBLIC_GENERATION_TRAJECTORIES
)


def validate_test_generation_count(
    value: int,
    *,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < MIN_TEST_GENERATION_COUNT:
        raise ValueError(
            f"{field_name} must be at least "
            f"{MIN_TEST_GENERATION_COUNT}"
        )
    if value > TEST_GENERATION_COUNT_SAFETY_CEILING:
        raise ValueError(
            f"{field_name} exceeds safety ceiling "
            f"{TEST_GENERATION_COUNT_SAFETY_CEILING}"
        )
    return value


def resolve_test_generation_profile(
    value: str | TestGenerationProfile | None,
) -> TestGenerationProfile:
    if value is None:
        return TestGenerationProfile.LIGHTWEIGHT
    if isinstance(value, TestGenerationProfile):
        return value
    if not isinstance(value, str):
        raise TypeError(
            "test_generation_profile must be None, a string, or "
            "TestGenerationProfile"
        )
    cleaned = value.strip().casefold()
    try:
        return TestGenerationProfile(cleaned)
    except ValueError as exc:
        choices = ", ".join(item.value for item in TestGenerationProfile)
        raise ValueError(
            f"unsupported test generation profile {value!r}; "
            f"expected one of: {choices}"
        ) from exc
