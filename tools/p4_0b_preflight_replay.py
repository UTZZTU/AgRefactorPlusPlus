#!/usr/bin/env python3
"""Deterministic cand-2 ownership replay for Pre-Stage-4 P4-0B."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agrefactor.evaluation import (
    FeedbackRouter,
    TestbenchPreflight,
    TestbenchPreflightFeedbackAdapter,
    TestbenchPreflightFeedbackViewAdapter,
)
from agrefactor.evidence import (
    TestbenchFailureOwner,
    TestbenchPreflightReasonCode,
)


ORIGINAL = (
    'extern "C" int original_top(int x) {\n'
    '    return x + 1;\n'
    '}\n'
)
CANDIDATE_CAND_2 = (
    'extern "C" int candidate_top(int x) {\n'
    '    return x + ;\n'
    '}\n'
)
TESTBENCH = (
    'extern "C" int original_top(int);\n'
    'extern "C" int candidate_top(int);\n'
    'int main() {\n'
    '    return original_top(4) != candidate_top(4);\n'
    '}\n'
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    temporary = None
    if args.work_dir is None:
        temporary = tempfile.TemporaryDirectory(
            prefix="p4_0b_cand2_replay_"
        )
        work_dir = Path(temporary.name)
    else:
        work_dir = args.work_dir.expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

    result = TestbenchPreflight().compile_and_link(
        work_dir=work_dir,
        testbench_code=TESTBENCH,
        original_code=ORIGINAL,
        candidate_code=CANDIDATE_CAND_2,
        original_top_function="original_top",
        candidate_top_function="candidate_top",
    )
    operator = TestbenchPreflightFeedbackAdapter().to_operator_report(
        result,
        report_id="p4-0b.cand-2.operator",
    )
    agent = TestbenchPreflightFeedbackViewAdapter().to_agent_report(
        operator,
        report_id="p4-0b.cand-2.agent",
    )
    decision = FeedbackRouter().route(
        agent,
        decision_id="p4-0b.cand-2.decision",
    )

    substages = [item.substage.value for item in result.substeps]
    passed = (
        result.failure_owner is TestbenchFailureOwner.CANDIDATE
        and result.reason_code
        is TestbenchPreflightReasonCode.CANDIDATE_COMPILE_FAILED
        and result.failed_component is not None
        and result.failed_component.value == "candidate"
        and result.next_action == "repair_candidate"
        and decision.action.value == "repair_candidate"
        and substages == [
            "testbench_compile",
            "reference_compile",
            "candidate_compile",
        ]
        and "link" not in substages
    )
    payload = {
        "schema_version": 1,
        "replay_id": "s38-nested-stencil-r1-safe-optimize.cand-2",
        "replay_kind": "deterministic_equivalent_failure_shape",
        "historical_source_recovered": False,
        "candidate_id": "cand-2",
        "status": result.status.value,
        "failure_owner": result.failure_owner.value,
        "reason_code": result.reason_code.value,
        "reason_codes": [
            item.value for item in result.reason_codes
        ],
        "failed_component": (
            None
            if result.failed_component is None
            else result.failed_component.value
        ),
        "next_action": result.next_action,
        "route_action": decision.action.value,
        "launched_substages": substages,
        "later_validation_started": False,
        "link_started": "link" in substages,
        "passed": passed,
    }

    output = args.output
    if output is not None:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if temporary is not None:
        temporary.cleanup()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
