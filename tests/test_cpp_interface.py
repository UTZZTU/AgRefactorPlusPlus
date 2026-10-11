from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from agrefactor.cpp_interface import extract_global_variables, extract_top_interface, extract_top_type_contract, inspect_top_entry


class CppInterfaceTests(unittest.TestCase):
    def test_incomplete_record_keeps_compiler_declaration_provenance(self):
        contract = extract_top_type_contract(
            "struct Packet;\nvoid top(Packet *state);", "top",
            source_path="generated_public.cpp",
        )
        self.assertEqual(contract["entry_status"], "confirmed")
        self.assertIn("record_layout_unavailable:Packet", contract["unresolved"])
        record = contract["type_provenance"][0]
        self.assertEqual(record["name"], "Packet")
        self.assertIsNone(record["definition"])
        declaration = record["declaration"]
        self.assertEqual(declaration["source_path"], "generated_public.cpp")
        self.assertEqual(declaration["line"], 1)
        self.assertTrue(declaration["usr"])
        self.assertTrue(declaration["is_main_file"])
        self.assertFalse(declaration["is_definition"])
        self.assertGreater(declaration["source_end"], declaration["source_start"])

    def test_record_definition_provenance_is_separate_from_layout_identity(self):
        definition = "struct Packet { unsigned count; double values[3]; };"
        with tempfile.TemporaryDirectory() as temporary:
            header = Path(temporary) / "public_packet.h"
            header.write_text(definition, encoding="utf-8")
            from_header = extract_top_type_contract(
                '#include "public_packet.h"\nvoid top(Packet *state);', "top",
                source_path=Path(temporary) / "original.cpp",
            )
            from_main = extract_top_type_contract(
                "\n\n" + definition + "\nvoid top(Packet *state);", "top",
                source_path=Path(temporary) / "generated_public.cpp",
            )
            self.assertEqual(from_header["status"], "confirmed")
            self.assertEqual(from_main["status"], "confirmed")
            header_definition = from_header["type_provenance"][0]["definition"]
            main_definition = from_main["type_provenance"][0]["definition"]
            self.assertEqual(header_definition["source_path"], str(header))
            self.assertEqual(header_definition["line"], 1)
            self.assertFalse(header_definition["is_main_file"])
            self.assertTrue(header_definition["is_definition"])
            self.assertEqual(main_definition["line"], 3)
            self.assertTrue(main_definition["is_main_file"])
            self.assertEqual(header_definition["usr"], main_definition["usr"])
            self.assertEqual(from_header["types"], from_main["types"])
            self.assertEqual(from_header["fingerprint"], from_main["fingerprint"])
            self.assertNotEqual(from_header["type_provenance"], from_main["type_provenance"])

    def test_template_declaration_and_instantiated_calls_keep_compiler_identity(self):
        source = """
template<int Extent, class Element>
void transform(Element input[Extent][Extent], Element (&output)[Extent][Extent]) {}
template<int Extent> void adapter() {
    double input[Extent][Extent], output[Extent][Extent];
    transform<Extent, double>(input, output);
}
void observe() { adapter<3>(); adapter<5>(); }
"""
        interface = extract_top_interface(source, "transform")
        self.assertIsNotNone(interface)
        self.assertEqual([item["name"] for item in interface.template_parameters], ["Extent", "Element"])
        self.assertEqual(len(interface.parameters), 2)
        self.assertIn("(&)", interface.parameters[1].canonical_type)
        calls = inspect_top_entry(source, "observe")["reachable_calls"]
        instances = [item for item in calls if item.get("template_usr") == interface.usr]
        self.assertEqual(len(instances), 2)
        self.assertEqual(len({item["usr"] for item in instances}), 2)
        self.assertTrue(all(item["linker_symbol"] and item["source_path"] and item["source_end"] > item["source_start"] for item in instances))
        self.assertTrue(all("double (&)[" in item["canonical_function_type"] for item in instances))

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
