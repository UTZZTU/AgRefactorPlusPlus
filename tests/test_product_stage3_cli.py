from __future__ import annotations

import unittest

from agrefactor.cli import build_parser


class ProductStage3CliTests(unittest.TestCase):
    def test_optimize_cli_exposes_reference_contract(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "optimize",
                "candidate.cpp",
                "--top",
                "candidate_top",
                "--reference-source",
                "original.cpp",
                "--reference-top",
                "original_top",
                "--public-test",
                "public.cpp",
                "--hidden-test",
                "hidden.cpp",
                "--model",
                "deepseek-v4-flash",
            ]
        )
        self.assertEqual(args.command, "optimize")
        self.assertEqual(args.reference_source.name, "original.cpp")
        self.assertEqual(args.reference_top, "original_top")
        self.assertEqual(args.optimizer_profile, "safe-v1")
        self.assertEqual(args.optimization_objective, "latency")

    def test_full_cli_does_not_require_reference_source(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["full", "kernel.cpp", "--top", "kernel", "--model", "deepseek-v4-flash"]
        )
        self.assertEqual(args.command, "full")
        self.assertIsNone(args.reference_source)
        self.assertEqual(args.optimizer_profile, "safe-v1")
        self.assertEqual(args.optimization_objective, "latency")


if __name__ == "__main__":
    unittest.main()
