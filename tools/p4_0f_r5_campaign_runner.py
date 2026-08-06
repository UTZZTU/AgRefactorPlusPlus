#!/usr/bin/env python3
"""Run one R5-C campaign manifest with durable heartbeat evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agrefactor.campaign import (  # noqa: E402
    CampaignInvariantError,
    CampaignRunner,
    load_campaign_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute a serial, fail-soft AgRefactor campaign. "
            "Commands are argv arrays and always run with shell=False."
        )
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="New or empty directory for campaign evidence.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_campaign_manifest(args.manifest)
        runner = CampaignRunner(
            manifest,
            artifact_root=args.artifact_root,
        )
        result = runner.run()
    except CampaignInvariantError as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "invariant_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3
    except KeyboardInterrupt:
        print("P4_0F_R5_C_CAMPAIGN_INTERRUPTED", file=sys.stderr)
        return 130

    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    print(f"CAMPAIGN_ARTIFACT_ROOT={result.artifact_root}")
    if result.status == "passed":
        print("P4_0F_R5_C_CAMPAIGN_PASSED")
        return 0
    print("P4_0F_R5_C_CAMPAIGN_COMPLETED_WITH_FAILURES")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
