"""Typed, default-off binding from an R5 arm to the existing refactor seam.

This is an internal experiment contract.  It is deliberately not exposed as
CLI configuration and it never grants an authority outside the accepted R4
controller, validator, and auditor.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
from typing import Any

from agrefactor.recovery.r5_authorization import (
    R5AuthorizationMode,
    R5ResearchAuthorization,
)
from agrefactor.recovery.r5_memory_payload import (
    R5MemoryPayload,
    render_candidate_memory_snippets,
)

from .r5_profile import R5Arm, R5Profile, resolve_r5_profile


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class R5RuntimeBindingError(ValueError):
    """Raised when a profile is not completely bound to its frozen inputs."""


@dataclass(frozen=True, slots=True)
class R5RuntimeBinding:
    """Complete internal inputs for one R5 arm execution.

    ``r4_integration_factory`` is built by the campaign executor with the
    current model adapter and accepted R4 artifacts.  The product entrypoint
    only receives the resulting object through the private in-process seam.
    """

    profile: R5Profile
    authorization: R5ResearchAuthorization | None = None
    memory_payloads: tuple[R5MemoryPayload, ...] = ()
    retrieval_manifest_sha256: str | None = None
    r4_integration_factory: Callable[[Any], Any] | None = None

    def __post_init__(self) -> None:
        profile = self.profile
        if not isinstance(profile, R5Profile):
            profile = resolve_r5_profile(str(profile))
            object.__setattr__(self, "profile", profile)
        payloads = tuple(self.memory_payloads)
        if any(not isinstance(item, R5MemoryPayload) for item in payloads):
            raise R5RuntimeBindingError(
                "memory_payloads must contain R5MemoryPayload values"
            )
        if len({item.payload_sha256 for item in payloads}) != len(payloads):
            raise R5RuntimeBindingError("memory_payloads must be unique")
        object.__setattr__(self, "memory_payloads", payloads)
        if self.retrieval_manifest_sha256 is not None and not _SHA256.fullmatch(
            self.retrieval_manifest_sha256
        ):
            raise R5RuntimeBindingError(
                "retrieval_manifest_sha256 must be SHA-256"
            )

        if profile.arm in {None, R5Arm.A0, R5Arm.A1}:
            if (
                self.authorization is not None
                or payloads
                or self.retrieval_manifest_sha256 is not None
                or self.r4_integration_factory is not None
            ):
                raise R5RuntimeBindingError(
                    "A0/A1 and the default profile cannot carry mutation bindings"
                )
            return

        if self.authorization is None:
            raise R5RuntimeBindingError(
                "mutation arms require a discriminated R5 authorization"
            )
        if self.authorization.arm_id != profile.arm.value:
            raise R5RuntimeBindingError("authorization arm does not match profile")
        if not callable(self.r4_integration_factory):
            raise R5RuntimeBindingError(
                "mutation arms require an existing R4 integration factory"
            )
        if profile.arm is R5Arm.A2:
            if self.authorization.mode is not R5AuthorizationMode.ADVISOR_ONLY:
                raise R5RuntimeBindingError("A2 requires advisor_only authorization")
            if payloads or self.retrieval_manifest_sha256 is not None:
                raise R5RuntimeBindingError("A2 must provide empty memory")
        elif profile.arm is R5Arm.A3:
            if self.authorization.mode is not R5AuthorizationMode.SIMILARITY_ONLY:
                raise R5RuntimeBindingError(
                    "A3 requires similarity_only authorization"
                )
            if payloads or self.retrieval_manifest_sha256 is None:
                raise R5RuntimeBindingError(
                    "A3 requires a content-addressed retrieval manifest"
                )
            if self.retrieval_manifest_sha256 != self.authorization.retrieval_manifest_sha256:
                raise R5RuntimeBindingError(
                    "A3 retrieval manifest does not match authorization"
                )
        else:
            if self.authorization.mode is not R5AuthorizationMode.GATED_MEMORY:
                raise R5RuntimeBindingError(
                    "A4-A6 require gated_memory authorization"
                )
            if not payloads:
                raise R5RuntimeBindingError(
                    "A4-A6 require at least one gated memory payload"
                )
            expected_snapshot = self.authorization.snapshot_sha256
            expected_manifest = self.authorization.payload_manifest_sha256
            if expected_snapshot is None or expected_manifest is None:
                raise R5RuntimeBindingError(
                    "gated authorization is missing snapshot/payload bindings"
                )
            if any(item.snapshot_sha256 != expected_snapshot for item in payloads):
                raise R5RuntimeBindingError(
                    "memory payload snapshot does not match authorization"
                )
            expected_revision = self.authorization.revision_sha256
            if expected_revision is None or any(
                item.revision_sha256 != expected_revision for item in payloads
            ):
                raise R5RuntimeBindingError(
                    "memory payload revision does not match authorization"
                )
            actual_manifest = _payload_manifest_sha256(payloads)
            if actual_manifest != expected_manifest:
                raise R5RuntimeBindingError(
                    "memory payload manifest does not match authorization"
                )

    @property
    def approved_memory_snippets(self) -> tuple[str, ...]:
        if self.memory_payloads:
            return render_candidate_memory_snippets(self.memory_payloads)
        if self.profile.arm is R5Arm.A3 and self.retrieval_manifest_sha256:
            return (
                '{"retrieval_manifest_sha256":"'
                + self.retrieval_manifest_sha256
                + '","memory_mode":"similarity_only"}',
            )
        return ()

    @property
    def approved_memory_snippets_sha256(self) -> str:
        import hashlib
        import json

        encoded = json.dumps(
            list(self.approved_memory_snippets),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def payload_manifest_sha256(self) -> str | None:
        if not self.memory_payloads:
            return None
        return _payload_manifest_sha256(self.memory_payloads)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "r5-runtime-binding-v1",
            "arm": None if self.profile.arm is None else self.profile.arm.value,
            "authorization_id": (
                None
                if self.authorization is None
                else self.authorization.authorization_id
            ),
            "authorization_sha256": (
                None
                if self.authorization is None
                else self.authorization.authorization_sha256
            ),
            "memory_payload_count": len(self.memory_payloads),
            "payload_manifest_sha256": self.payload_manifest_sha256,
            "retrieval_manifest_sha256": self.retrieval_manifest_sha256,
            "r4_integration_bound": self.r4_integration_factory is not None,
            "candidate_only": True,
        }


def _payload_manifest_sha256(
    payloads: Sequence[R5MemoryPayload],
) -> str:
    values = sorted(item.payload_sha256 for item in payloads)
    import hashlib
    import json

    encoded = json.dumps(
        {"schema_version": "r5-payload-manifest-v1", "payloads": values},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["R5RuntimeBinding", "R5RuntimeBindingError"]
