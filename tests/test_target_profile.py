import unittest

from agrefactor.config import TargetProfile


class TargetProfileTests(unittest.TestCase):
    def test_create_profile(self) -> None:
        profile = TargetProfile(
            name="vitis-2023.2-default",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
            device="xcu250-figd2104-2L-e",
            clock_period_ns=5.0,
            compile_flags=("-std=c++14",),
        )

        self.assertEqual(profile.name, "vitis-2023.2-default")
        self.assertEqual(profile.toolchain, "vitis_hls")
        self.assertEqual(profile.clock_period_ns, 5.0)
        self.assertEqual(profile.compile_flags, ("-std=c++14",))

    def test_round_trip_dict(self) -> None:
        original = TargetProfile(
            name="test",
            toolchain="vitis_hls",
            toolchain_version="2023.2",
            clock_period_ns=10.0,
        )

        restored = TargetProfile.from_dict(original.to_dict())

        self.assertEqual(restored, original)

    def test_reject_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            TargetProfile(name="  ", toolchain="vitis_hls")

    def test_reject_invalid_clock(self) -> None:
        with self.assertRaises(ValueError):
            TargetProfile(
                name="test",
                toolchain="vitis_hls",
                clock_period_ns=0,
            )


if __name__ == "__main__":
    unittest.main()
