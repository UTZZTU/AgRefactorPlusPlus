import unittest

from flow.tools.csynth import make_vitis_tcl


class CsynthTclTests(unittest.TestCase):
    def test_default_profile_preserves_legacy_target(self) -> None:
        tcl = make_vitis_tcl("top", ["kernel.cpp"])

        self.assertIn('set_top "top"', tcl)
        self.assertIn(
            'add_files "kernel.cpp" -cflags "-D XILINX"',
            tcl,
        )
        self.assertIn(
            'set_part "xcu200-fsgd2104-2-e"',
            tcl,
        )
        self.assertIn(
            "create_clock -period 5.0 -name default",
            tcl,
        )

    def test_partial_profile_changes_device_clock_and_flags(self) -> None:
        tcl = make_vitis_tcl(
            "top",
            ["kernel.cpp"],
            {
                "device": "xcu250-figd2104-2L-e",
                "clock_frequency_mhz": 250,
                "append_compile_flags": ["-I include"],
            },
        )

        self.assertIn(
            'set_part "xcu250-figd2104-2L-e"',
            tcl,
        )
        self.assertIn(
            "create_clock -period 4.0 -name default",
            tcl,
        )
        self.assertIn(
            '-cflags "-D XILINX -I include"',
            tcl,
        )

    def test_compile_flags_can_replace_defaults(self) -> None:
        tcl = make_vitis_tcl(
            "top",
            ["kernel.cpp"],
            {"compile_flags": ["-std=c++14"]},
        )

        self.assertIn('-cflags "-std=c++14"', tcl)
        self.assertNotIn("-D XILINX", tcl)

    def test_empty_compile_flags_omit_cflags_option(self) -> None:
        tcl = make_vitis_tcl(
            "top",
            ["kernel.cpp"],
            {"compile_flags": []},
        )

        add_line = next(
            line
            for line in tcl.splitlines()
            if line.startswith("add_files ")
        )
        self.assertEqual(add_line, 'add_files "kernel.cpp"')

    def test_quotes_tcl_substitution_characters(self) -> None:
        tcl = make_vitis_tcl(
            "top",
            ["dir/kernel$slot[0].cpp"],
            {
                "compile_flags": [
                    '-DNAME="$VALUE"',
                ],
            },
        )

        self.assertIn(
            'add_files "dir/kernel\\$slot\\[0\\].cpp"',
            tcl,
        )
        self.assertIn(
            '-cflags "-DNAME=\\"\\$VALUE\\""',
            tcl,
        )

    def test_rejects_missing_device(self) -> None:
        with self.assertRaises(ValueError):
            make_vitis_tcl(
                "top",
                ["kernel.cpp"],
                {"device": None},
            )

    def test_rejects_unsupported_toolchain(self) -> None:
        with self.assertRaises(ValueError):
            make_vitis_tcl(
                "top",
                ["kernel.cpp"],
                {"toolchain": "other_hls"},
            )

    def test_rejects_empty_file_list(self) -> None:
        with self.assertRaises(ValueError):
            make_vitis_tcl("top", [])

    def test_rejects_newline_in_tcl_values(self) -> None:
        with self.assertRaises(ValueError):
            make_vitis_tcl(
                "top\nbad",
                ["kernel.cpp"],
            )


if __name__ == "__main__":
    unittest.main()
