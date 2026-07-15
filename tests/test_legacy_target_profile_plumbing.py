import inspect
import unittest

from flow.new import hls_refactor_with_rag


class LegacyTargetProfilePlumbingTests(unittest.TestCase):
    def test_flow_new_accepts_target_profile(self) -> None:
        signature = inspect.signature(hls_refactor_with_rag)

        self.assertIn("target_profile", signature.parameters)
        self.assertIsNone(
            signature.parameters["target_profile"].default
        )


if __name__ == "__main__":
    unittest.main()
