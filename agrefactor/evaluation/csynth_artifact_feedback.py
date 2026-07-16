"""Build unified CSYNTH feedback from existing local artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agrefactor.evidence import (
    FeedbackOwner,
    FeedbackReport,
)

from .csynth_diagnostics import CsynthDiagnosticParser
from .csynth_feedback import CsynthFeedbackAdapter
from .csynth_feedback_composer import CsynthFeedbackComposer


class CsynthArtifactFeedbackEvaluator:
    """Read existing CSYNTH artifacts and produce one operator report.

    The evaluator reads ``csynth_invocation.json`` and, when present,
    ``csynth/solution/solution.log`` from a completed work directory.
    It does not invoke Vitis, consume budget, mutate artifacts, persist
    reports, expose data to an agent, or choose a repair action.

    ``legacy_status`` is supplied by the caller so this layer does not
    reimplement or silently change the legacy synthesis status
    contract.
    """

    evaluator_version = 1
    default_max_diagnostic_bytes = 16 * 1024 * 1024

    def __init__(
        self,
        *,
        max_diagnostic_bytes: int = (
            default_max_diagnostic_bytes
        ),
    ) -> None:
        if (
            isinstance(max_diagnostic_bytes, bool)
            or not isinstance(max_diagnostic_bytes, int)
        ):
            raise TypeError(
                "max_diagnostic_bytes must be an integer"
            )
        if max_diagnostic_bytes <= 0:
            raise ValueError(
                "max_diagnostic_bytes must be positive"
            )

        self._max_diagnostic_bytes = max_diagnostic_bytes
        self._adapter = CsynthFeedbackAdapter()
        self._parser = CsynthDiagnosticParser()
        self._composer = CsynthFeedbackComposer()

    def evaluate(
        self,
        work_dir: str | os.PathLike[str],
        *,
        report_id: str,
        legacy_status: str | None = None,
        error_msg: str = "",
        owner: FeedbackOwner | str = FeedbackOwner.UNKNOWN,
    ) -> FeedbackReport:
        """Evaluate invocation and diagnostic artifacts without writes."""

        root = self._work_dir(work_dir)
        invocation_path = root / "csynth_invocation.json"
        diagnostic_path = (
            root / "csynth" / "solution" / "solution.log"
        )

        invocation = self._read_json_object(invocation_path)
        diagnostic_text, diagnostic_loading = (
            self._read_diagnostic_text(diagnostic_path)
        )

        invocation_report = self._adapter.to_operator_report(
            invocation=invocation,
            report_id=f"{report_id}.invocation",
            legacy_status=legacy_status,
            error_msg=error_msg,
            evidence_ref=str(invocation_path),
        )
        diagnostic_report = self._parser.parse_text(
            diagnostic_text,
            report_id=f"{report_id}.diagnostic",
            evidence_ref=(
                str(diagnostic_path)
                if diagnostic_loading["exists"]
                else None
            ),
            owner=owner,
        )
        combined = self._composer.compose(
            invocation_report=invocation_report,
            diagnostic_report=diagnostic_report,
            report_id=report_id,
        )

        source_evidence = dict(combined.source_evidence)
        source_evidence["artifact_loading"] = {
            "work_dir": str(root),
            "invocation_path": str(invocation_path),
            "diagnostic_path": str(diagnostic_path),
            **diagnostic_loading,
        }

        metadata = dict(combined.metadata)
        metadata.update(
            {
                "artifact_evaluator_version": (
                    self.evaluator_version
                ),
                "work_dir": str(root),
                "invocation_path": str(invocation_path),
                "diagnostic_path": str(diagnostic_path),
                "diagnostic_exists": diagnostic_loading[
                    "exists"
                ],
                "diagnostic_size_bytes": diagnostic_loading[
                    "size_bytes"
                ],
                "diagnostic_bytes_read": diagnostic_loading[
                    "bytes_read"
                ],
                "diagnostic_truncated": diagnostic_loading[
                    "truncated"
                ],
                "legacy_status": legacy_status,
            }
        )

        return FeedbackReport(
            report_id=combined.report_id,
            source=combined.source,
            items=combined.items,
            source_evidence=source_evidence,
            metadata=metadata,
        )

    @staticmethod
    def _work_dir(
        value: str | os.PathLike[str],
    ) -> Path:
        try:
            raw = os.fspath(value)
        except TypeError as exc:
            raise TypeError(
                "work_dir must be a path-like value"
            ) from exc

        if not isinstance(raw, str):
            raise TypeError(
                "work_dir must resolve to a string path"
            )
        if not raw.strip():
            raise ValueError("work_dir must not be empty")

        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(
                f"CSYNTH work directory not found: {path}"
            )
        return path

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(
                f"CSYNTH invocation artifact not found: {path}"
            )

        try:
            value = json.loads(
                path.read_text(
                    encoding="utf-8",
                    errors="strict",
                )
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid CSYNTH invocation JSON: {path}"
            ) from exc

        if not isinstance(value, dict):
            raise TypeError(
                "CSYNTH invocation JSON root must be an object"
            )
        return value

    def _read_diagnostic_text(
        self,
        path: Path,
    ) -> tuple[str, dict[str, Any]]:
        if not path.exists():
            return "", {
                "exists": False,
                "size_bytes": None,
                "bytes_read": 0,
                "truncated": False,
                "read_mode": "missing",
            }
        if not path.is_file():
            raise TypeError(
                f"CSYNTH diagnostic path is not a file: {path}"
            )

        size = path.stat().st_size
        if size <= self._max_diagnostic_bytes:
            data = path.read_bytes()
            return data.decode(
                "utf-8",
                errors="replace",
            ), {
                "exists": True,
                "size_bytes": size,
                "bytes_read": len(data),
                "truncated": False,
                "read_mode": "full",
            }

        with path.open("rb") as stream:
            stream.seek(-self._max_diagnostic_bytes, os.SEEK_END)
            data = stream.read(self._max_diagnostic_bytes)

        return data.decode(
            "utf-8",
            errors="replace",
        ), {
            "exists": True,
            "size_bytes": size,
            "bytes_read": len(data),
            "truncated": True,
            "read_mode": "tail",
        }
