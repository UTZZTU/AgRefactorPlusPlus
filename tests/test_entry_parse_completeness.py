import unittest

from agrefactor.cpp_interface import extract_top_interface, inspect_top_entry


class EntryParseCompletenessTests(unittest.TestCase):
    def test_fatal_translation_unit_retains_evidence_without_confirming_entry(self):
        source = '#include "missing_dependency.hpp"\nint work(int x) {return x;}\n'
        facts = inspect_top_entry(source, "work")
        self.assertEqual(facts["status"], "unknown")
        self.assertFalse(facts["translation_unit_complete"])
        self.assertTrue(any(item["severity"] >= 4 for item in facts["diagnostics"]))
        self.assertIsNone(extract_top_interface(source, "work"))

    def test_recoverable_body_error_does_not_discard_valid_signature(self):
        source = 'int work(int x) {return missing_body_symbol;}\n'
        interface = extract_top_interface(source, "work")
        self.assertIsNotNone(interface)
        self.assertEqual(interface.parameters[0].canonical_type, "int")


if __name__ == "__main__":
    unittest.main()
