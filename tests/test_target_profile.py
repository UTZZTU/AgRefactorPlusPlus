import unittest

from agrefactor.config import (
    DEFAULT_TARGET_PROFILE_NAME,
    TargetProfile,
    default_target_profile,
    resolve_target_profile,
)


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

    def test_reject_string_compile_flags(self) -> None:
        with self.assertRaises(TypeError):
            TargetProfile(
                name="test",
                toolchain="vitis_hls",
                compile_flags="-D XILINX",
            )

    def test_direct_constructor_uses_project_clock_default(self) -> None:
        profile = TargetProfile(
            name="direct-default",
            toolchain="vitis_hls",
        )

        self.assertEqual(profile.clock_period_ns, 5.0)

    def test_from_dict_uses_project_clock_default(self) -> None:
        profile = TargetProfile.from_dict(
            {
                "name": "mapping-default",
                "toolchain": "vitis_hls",
            }
        )

        self.assertEqual(profile.clock_period_ns, 5.0)

    def test_default_profile_preserves_legacy_values(self) -> None:
        profile = default_target_profile()

        self.assertEqual(profile.name, DEFAULT_TARGET_PROFILE_NAME)
        self.assertEqual(profile.toolchain_version, "2023.2")
        self.assertEqual(profile.device, "xcu200-fsgd2104-2-e")
        self.assertEqual(profile.clock_period_ns, 5.0)
        self.assertEqual(profile.compile_flags, ("-D XILINX",))

    def test_resolve_none_uses_default(self) -> None:
        self.assertEqual(
            resolve_target_profile(None),
            default_target_profile(),
        )

    def test_resolve_named_default_alias(self) -> None:
        self.assertEqual(
            resolve_target_profile("default"),
            default_target_profile(),
        )

    def test_partial_override_converts_frequency_and_appends_flags(
        self,
    ) -> None:
        profile = resolve_target_profile(
            {
                "clock_frequency_mhz": 250,
                "append_compile_flags": ["-I include"],
            }
        )

        self.assertEqual(profile.clock_period_ns, 4.0)
        self.assertEqual(
            profile.compile_flags,
            ("-D XILINX", "-I include"),
        )
        self.assertEqual(profile.device, "xcu200-fsgd2104-2-e")

    def test_compile_flags_can_replace_defaults(self) -> None:
        profile = resolve_target_profile(
            {"compile_flags": ["-std=c++14"]}
        )

        self.assertEqual(profile.compile_flags, ("-std=c++14",))

    def test_reject_conflicting_clock_inputs(self) -> None:
        with self.assertRaises(ValueError):
            resolve_target_profile(
                {
                    "clock_period_ns": 5.0,
                    "clock_frequency_mhz": 250,
                }
            )

    def test_reject_unknown_override_field(self) -> None:
        with self.assertRaises(ValueError):
            resolve_target_profile({"clok_period_ns": 5.0})


if __name__ == "__main__":
    unittest.main()
