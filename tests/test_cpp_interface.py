from __future__ import annotations

import unittest

from agrefactor.cpp_interface import extract_global_variables, extract_top_interface


class CppInterfaceTests(unittest.TestCase):
    def test_resolves_typedef_and_definition_after_declaration(self) -> None:
        source = """
typedef int row[4];
void top(row *values, int count);
static void helper() {}
void top(row *values, int count) { values[0][0] = count; }
"""
        interface = extract_top_interface(source, "top")
        self.assertIsNotNone(interface)
        assert interface is not None
        self.assertEqual(
            tuple(item.name for item in interface.parameters),
            ("values", "count"),
        )
        self.assertTrue(interface.parameters[0].pointer_like)
        self.assertFalse(interface.parameters[1].pointer_like)
        self.assertIn("int", interface.parameters[0].canonical_type)
        self.assertIn("void top(row *values, int count)", interface.source_declaration or "")

    def test_preserves_source_language_linkage_for_abi_comparison(self) -> None:
        c_decl = extract_top_interface(
            'extern "C" int top(int value);',
            "top",
            require_definition=False,
        )
        cpp_decl = extract_top_interface(
            "int top(int value);",
            "top",
            require_definition=False,
        )
        self.assertIsNotNone(c_decl)
        self.assertIsNotNone(cpp_decl)
        assert c_decl is not None and cpp_decl is not None
        self.assertEqual(c_decl.language_linkage, "c")
        self.assertEqual(cpp_decl.language_linkage, "cpp")
        from agrefactor.cpp_interface import interfaces_equivalent
        self.assertFalse(interfaces_equivalent(c_decl, cpp_decl))


    def test_reads_a_standalone_declaration_when_requested(self) -> None:
        interface = extract_top_interface(
            "int top(const int *input, int output[8]);",
            "top",
            require_definition=False,
        )
        self.assertIsNotNone(interface)
        assert interface is not None
        self.assertEqual(
            tuple(item.name for item in interface.parameters),
            ("input", "output"),
        )
        self.assertTrue(all(item.pointer_like for item in interface.parameters))

    def test_complex_valid_declaration_is_not_lexically_rejected(self) -> None:
        source = """
using sample = const unsigned long;
auto top(sample (&input)[8], unsigned count) -> void {
    (void)input;
    (void)count;
}
"""
        interface = extract_top_interface(source, "top")
        self.assertIsNotNone(interface)
        assert interface is not None
        self.assertEqual(interface.parameters[0].name, "input")
        self.assertTrue(interface.parameters[0].pointer_like)

    def test_parse_failure_is_unknown_instead_of_an_exception(self) -> None:
        self.assertIsNone(extract_top_interface("not valid C++", "top"))

    def test_canonical_result_type_resolves_void_alias(self) -> None:
        interface = extract_top_interface(
            "typedef void Ret; Ret top(int *values);",
            "top",
            require_definition=False,
        )
        self.assertIsNotNone(interface)
        assert interface is not None
        self.assertEqual(interface.result_type, "Ret")
        self.assertEqual(interface.canonical_result_type, "void")

    def test_fixed_array_output_is_mutable_but_const_input_is_not(self) -> None:
        for input_type, output_type in (
            ("const int input[8]", "int output[8]"),
            ("const int input[2][4]", "int output[2][4]"),
            ("const int (&input)[8]", "int (&output)[8]"),
            ("const int *input", "int *output"),
        ):
            with self.subTest(input_type=input_type, output_type=output_type):
                interface = extract_top_interface(
                    f"void top({input_type}, {output_type}) {{}}",
                    "top",
                )
                self.assertIsNotNone(interface)
                assert interface is not None
                self.assertFalse(interface.parameters[0].mutable_output)
                self.assertTrue(interface.parameters[1].mutable_output)


    def test_global_variables_come_from_ast_not_comments_or_strings(self) -> None:
        variables = extract_global_variables(
            "// extern int guessed[8];\n"
            'const char *note = "extern int guessed[8];";\n'
            "extern int actual[8];\n"
        )
        self.assertIsNotNone(variables)
        assert variables is not None
        depth_names = {
            variable.name for variable in variables if variable.pointer_like
        }
        self.assertEqual(depth_names, {"note", "actual"})
        self.assertNotIn("guessed", depth_names)


if __name__ == "__main__":
    unittest.main()
