from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import os
import subprocess
from unittest.mock import patch

from agrefactor.cpp_interface import (
    extract_cpp_comments,
    extract_top_interface,
    extract_top_type_contract,
    inspect_top_entry,
)


class CppContractFactsTests(unittest.TestCase):
    def test_comment_tokens_exclude_literals_and_keep_utf8_crlf_offsets(self):
        source = (
            '// 中文\r\nconst char *a="// BEGIN";\r\n'
            'const char *b=R"tag(/* BEGIN */ // END)tag";\r\n'
            '/* block example // BEGIN */\r\n// BEGIN\r\n'
            '// explanation \\\r\n// END\r\n'
        )
        facts = extract_cpp_comments(source)
        self.assertEqual(facts['status'], 'confirmed', facts)
        comments = facts['comments']
        self.assertEqual(len(comments), 4)
        self.assertEqual(comments[2]['text'], '// BEGIN')
        self.assertIn('// END', comments[3]['text'])
        for comment in comments:
            self.assertEqual(source[comment['start']:comment['end']], comment['text'])
            self.assertEqual(
                source.encode('utf-8')[comment['byte_start']:comment['byte_end']].decode('utf-8'),
                comment['text'],
            )

    def test_lexical_comments_available_despite_ast_errors(self):
        facts = extract_cpp_comments('not C++ at all;\n// MARKER\n')
        self.assertEqual(facts['status'], 'confirmed')
        self.assertEqual(facts['comments'][0]['text'], '// MARKER')

    def test_standard_headers_use_qualification_compiler_context(self):
        source = '#include <cstdio>\n#include <vector>\nint helper(){return 0;} int top(){return helper();}'
        facts = inspect_top_entry(source, 'top')
        self.assertTrue(facts['translation_unit_complete'], facts)
        self.assertEqual(facts['diagnostics'], [])
        self.assertEqual(facts['reachable_calls'][0]['name'], 'helper')
        self.assertEqual(facts['parse_context']['include_search_status'], 'confirmed')

    def test_unavailable_compiler_context_is_explicit(self):
        facts = extract_top_type_contract(
            'struct Data { int value; }; void top(Data *p);', 'top',
            compiler='/definitely/missing/cxx',
        )
        self.assertEqual(facts['status'], 'unknown', facts)
        self.assertEqual(facts['parse_context']['include_search_status'], 'unknown')
        self.assertTrue(any(item.get('source') == 'compiler_include_probe' for item in facts['diagnostics']))

    def test_real_ast_errors_preserved_and_type_contract_incomplete(self):
        source = 'struct Packet { Missing value; }; void top(Packet *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'unknown', facts)
        self.assertFalse(facts['translation_unit_complete'])
        self.assertTrue(any("Missing" in item['message'] for item in facts['diagnostics']))
        self.assertIsNone(facts['fingerprint'])

    def test_scalar_contract_is_empty_and_compatible(self):
        facts = extract_top_type_contract('int top(int value);', 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertEqual(facts['types'], [])
        self.assertFalse(facts['has_user_types'])

    def test_typedef_nested_enum_arrays_and_layout_are_frozen(self):
        source = '''
enum Mode { first=2, second=5 };
typedef int Row[3];
struct Inner { Mode mode; Row row; };
typedef struct Packet { Inner values[2]; const float *input; } Alias;
void top(Alias *p);
struct Irrelevant { int ignored; };
'''
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        by_name = {item['name']:item for item in facts['types']}
        self.assertNotIn('Irrelevant', by_name)
        self.assertIn('Alias', by_name)
        self.assertIn('Row', by_name)
        self.assertIn('Inner', by_name)
        self.assertEqual(by_name['Inner']['fields'][1]['canonical_type'], 'int[3]')
        self.assertEqual(by_name['Packet']['fields'][0]['layers'][0]['size'], 2)
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Alias *p);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_same_name_layout_drift_is_independently_visible(self):
        original = 'struct Packet { int rows[3]; float score; }; void top(Packet *p);'
        baseline = extract_top_type_contract(original, 'top')
        for replacement in ('float rows[3]; float score;', 'int rows[4]; float score;', 'float score; int rows[3];'):
            changed = extract_top_type_contract('struct Packet {' + replacement + '}; void top(Packet *p);', 'top')
            self.assertEqual(changed['status'], 'confirmed', changed)
            self.assertNotEqual(changed['fingerprint'], baseline['fingerprint'])

    def test_user_dependency_header_context_is_replayable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'data.h').write_text('struct Data { int rows[4]; };\n', encoding='utf-8')
            source = '#include "data.h"\nvoid top(Data *p);'
            facts = extract_top_type_contract(source, 'top', include_dirs=(directory,))
            self.assertEqual(facts['status'], 'confirmed', facts)
            replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *p);', 'top', include_dirs=(directory,))
            self.assertEqual(replay['status'], 'confirmed', replay)
            self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_packing_alignment_context_preserves_layout(self):
        source = '#pragma pack(push, 1)\nstruct Data { char flag; int count; };\n#pragma pack(pop)\nvoid top(Data *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        data = facts['types'][0]
        self.assertEqual(data['size_bytes'], 5)
        self.assertEqual(data['align_bytes'], 1)
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *p);', 'top')
        self.assertEqual(replay['fingerprint'], facts['fingerprint'], replay)

    def test_namespace_and_alignment_attribute_context_replays(self):
        source = 'namespace sample { using Scalar = int; struct alignas(16) Data { Scalar count; }; }\nvoid top(sample::Data *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(sample::Data *p);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_unused_headers_and_pragmas_in_strings_are_not_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'irrelevant.h').write_text('void unrelated();\n', encoding='utf-8')
            source = '#include "irrelevant.h"\nconst char *x = R"tag(\n#pragma pack(1)\n)tag";\nstruct Data { char flag; int count; }; void top(Data *p);'
            facts = extract_top_type_contract(source, 'top', include_dirs=(directory,))
            self.assertEqual(facts['status'], 'confirmed', facts)
            self.assertNotIn('irrelevant.h', facts['declaration_context'])
            self.assertNotIn('pack', facts['declaration_context'])

    def test_system_type_dependency_reuses_header_without_redeclaration(self):
        source = '#include <cstdio>\n#include <vector>\nstruct Data { std::vector<int> values; }; void top(Data *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertNotIn('namespace std', facts['declaration_context'])
        self.assertIn('#include <vector>', facts['declaration_context'])
        self.assertNotIn('#include <cstdio>', facts['declaration_context'])
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *p);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_macro_dependencies_only_from_required_declarations(self):
        source = '#define BASE 2\n#define WIDTH (BASE+1)\n#define TEST_INPUT 99\nstruct Data { int rows[WIDTH]; }; void top(Data *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertIn('#define WIDTH', facts['declaration_context'])
        self.assertIn('#define BASE', facts['declaration_context'])
        self.assertNotIn('TEST_INPUT', facts['declaration_context'])
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *p);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_macro_dependencies_from_entry_array_declaration_are_replayable(self):
        source = '''
#define HIST_C0 32
#define HIST_C1 64
#define HIST_TOTAL (HIST_C0 * HIST_C1)
#define UNUSED_INPUT 99
struct Data { int value; };
int top(int histogram[HIST_TOTAL], Data *data);
'''
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertIn('#define HIST_TOTAL', facts['declaration_context'])
        self.assertIn('#define HIST_C0', facts['declaration_context'])
        self.assertIn('#define HIST_C1', facts['declaration_context'])
        self.assertNotIn('UNUSED_INPUT', facts['declaration_context'])
        replay = extract_top_type_contract(
            facts['declaration_context'] +
            '\nint top(int histogram[HIST_TOTAL], Data *data);',
            'top',
        )
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_equivalent_entry_redeclarations_are_collapsed(self):
        source = '''
#define WIDTH 4
struct Data { int value; };
int top(int values[WIDTH], Data *data);
int top(int values[WIDTH], Data *data);
'''
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertEqual(facts.get('equivalent_redeclarations_collapsed'), 2)
        self.assertEqual(facts['unresolved'], [])

    def test_conflicting_entry_overloads_remain_ambiguous(self):
        facts = extract_top_type_contract(
            'int top(int *value);\nfloat top(float *value);', 'top',
        )
        self.assertEqual(facts['status'], 'unknown', facts)
        self.assertEqual(facts['entry_status'], 'ambiguous', facts)
        self.assertTrue(any(item.get('source') == 'entry_contract' for item in facts['diagnostics']))
        self.assertIn('entry_type_facts_unavailable', facts['unresolved'])

    def test_missing_entry_keeps_specific_diagnostic(self):
        facts = extract_top_type_contract('int other(int value);', 'top')
        self.assertEqual(facts['status'], 'unknown', facts)
        self.assertEqual(facts['entry_status'], 'missing', facts)
        self.assertTrue(any('was not found' in item.get('message', '') for item in facts['diagnostics']))

    def test_redeclaration_and_definition_use_same_usr_and_definition_facts(self):
        source = 'int helper(int n) { return n; } int top(int value); int top(int renamed) { return helper(renamed); }'
        facts = inspect_top_entry(source, 'top', require_definition=False)
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertEqual(facts['equivalent_redeclarations_collapsed'], 2)
        self.assertEqual(len({entry['usr'] for entry in facts['entries']}), 1)
        self.assertEqual(facts['reachable_calls'][0]['name'], 'helper')
        interface = extract_top_interface(source, 'top', require_definition=False)
        self.assertEqual(interface.parameters[0].name, 'renamed')

    def test_array_and_pointer_redeclarations_share_compiler_interface(self):
        facts = extract_top_type_contract('void top(int values[3]); void top(int *different_name);', 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertEqual(facts['equivalent_redeclarations_collapsed'], 2)

    def test_same_spelling_in_different_namespaces_is_not_same_entry(self):
        source = 'namespace first { void top(int *value); } namespace second { void top(int *value); }'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'unknown', facts)
        self.assertEqual(facts['entry_status'], 'ambiguous', facts)
        self.assertEqual(len({entry['usr'] for entry in facts['entries']}), 2)
        self.assertIsNone(extract_top_interface(source, 'top', require_definition=False))

    def test_duplicate_definitions_and_return_conflict_remain_unresolved(self):
        for source in (
            'int top(int n) { return n; } int top(int n) { return n; }',
            'int top(int n); float top(int n);',
        ):
            facts = extract_top_type_contract(source, 'top')
            self.assertEqual(facts['status'], 'unknown', facts)
            self.assertFalse(facts['translation_unit_complete'])
            self.assertTrue(any(item['severity'] >= 3 for item in facts['diagnostics']))
            self.assertIsNone(extract_top_interface(source, 'top', require_definition=False))

    def test_entry_body_macros_and_punctuation_in_comments_are_not_context(self):
        source = '#define WIDTH 4\n#define BODY_INPUT 99\nint top(/* ; { */ int values[WIDTH]) { return values[BODY_INPUT % WIDTH]; }'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertIn('#define WIDTH', facts['declaration_context'])
        self.assertNotIn('BODY_INPUT', facts['declaration_context'])
        replay = extract_top_type_contract(facts['declaration_context'] + '\nint top(int values[WIDTH]);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)

    def test_entry_macro_dependencies_from_compile_flags_and_headers_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sizes.hpp').write_text('#define HEIGHT 3\n', encoding='utf-8')
            source = '#include "sizes.hpp"\nvoid top(int matrix[WIDTH][HEIGHT]);'
            context = {'include_dirs': (directory,), 'compile_flags': ('-DWIDTH=4',)}
            facts = extract_top_type_contract(source, 'top', **context)
            self.assertEqual(facts['status'], 'confirmed', facts)
            self.assertIn('#include "sizes.hpp"', facts['declaration_context'])
            replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(int matrix[WIDTH][HEIGHT]);', 'top', **context)
            self.assertEqual(replay['status'], 'confirmed', replay)

    def test_macro_definition_uses_actual_expansion_before_later_redefinition(self):
        source = '#define BASE 2\n#define WIDTH (BASE+1)\nstruct Data { int values[WIDTH]; }; void top(Data *data);\n#undef BASE\n#define BASE 9\n#undef WIDTH\n#define WIDTH 20\n'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertIn('#define BASE 2', facts['declaration_context'])
        self.assertNotIn('#define BASE 9', facts['declaration_context'])
        self.assertNotIn('#define WIDTH 20', facts['declaration_context'])
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *data);', 'top')
        self.assertEqual(replay['fingerprint'], facts['fingerprint'], replay)

    def test_conflicting_required_macro_definitions_are_explicitly_incomplete(self):
        source = '#define WIDTH 2\nstruct Data { int values[WIDTH]; };\n#undef WIDTH\n#define WIDTH 9\nvoid top(Data *data, int values[WIDTH]);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'unknown', facts)
        self.assertIn('macro_definition_conflict:WIDTH', facts['unresolved'])
        self.assertIsNone(facts['fingerprint'])

    def test_function_macro_parameters_literals_and_comments_are_not_dependencies(self):
        source = '#define INPUT 3\n#define IGNORED 99\n#define WIDTH(INPUT) (INPUT + 1 /* IGNORED */)\nstruct Data { int values[WIDTH(2)]; }; void top(Data *data);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertNotIn('#define INPUT', facts['declaration_context'])
        self.assertNotIn('#define IGNORED', facts['declaration_context'])
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *data);', 'top')
        self.assertEqual(replay['fingerprint'], facts['fingerprint'], replay)

    def test_enum_and_const_dimension_context_replays(self):
        source = 'typedef enum { WIDTH=3, HEIGHT=2 } Dimensions; const int LIMIT=5; struct Data { int rows[WIDTH][HEIGHT]; int items[LIMIT]; }; void top(Data *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        replay = extract_top_type_contract(facts['declaration_context'] + '\nvoid top(Data *p);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_system_scalar_typedef_is_not_a_user_type(self):
        facts = extract_top_type_contract('#include <cstdint>\nvoid top(uint32_t *data);', 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        self.assertFalse(facts['has_user_types'])
        self.assertEqual(facts['types'], [])
        self.assertIn('#include <cstdint>', facts['declaration_context'])

    def test_vendor_header_context_is_reused_without_skipping_user_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / 'vendor'
            include = vendor / 'include'
            include.mkdir(parents=True)
            (include / 'packets.hpp').write_text(
                'namespace official { template<class T> struct Cell { T value; }; }\n',
                encoding='utf-8',
            )
            (root / 'local.hpp').write_text(
                '#include <packets.hpp>\nstruct Data { official::Cell<int> item; };\n',
                encoding='utf-8',
            )
            source = '#include "local.hpp"\nvoid top(Data *p);'
            path = root / 'testbench.cpp'
            path.write_text(source, encoding='utf-8')
            with patch.dict(os.environ, {'XILINX_HLS': str(vendor)}):
                compiled = subprocess.run(
                    ['g++', '-O2', '-flto', '-I', str(root), '-I', str(include),
                     '-c', str(path), '-o', str(root / 'testbench.o')],
                    capture_output=True, text=True,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                facts = extract_top_type_contract(
                    source, 'top', source_path=path, include_dirs=(str(root),),
                )
                self.assertEqual(facts['status'], 'confirmed', facts)
                self.assertEqual(facts['required_type_names'], ['Data'])
                self.assertIn('#include "local.hpp"', facts['declaration_context'])
                self.assertNotIn('template<class T>', facts['declaration_context'])
                replay = extract_top_type_contract(
                    facts['declaration_context'] + '\nvoid top(Data *p);', 'top',
                    source_path=path, include_dirs=(str(root),),
                )
                self.assertEqual(replay['status'], 'confirmed', replay)
                self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_installed_vendor_template_uses_normal_header_and_compiler_context(self):
        vendor = os.getenv('XILINX_HLS')
        if not vendor or not (Path(vendor) / 'include' / 'ap_int.h').is_file():
            self.skipTest('vendor headers unavailable')
        source = '#include <ap_int.h>\nstruct Data { ap_int<8> item; };\nvoid top(Data *p);'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'testbench.cpp'
            path.write_text(source, encoding='utf-8')
            compiled = subprocess.run(
                ['g++', '-O2', '-flto', '-I', str(Path(vendor) / 'include'),
                 '-c', str(path), '-o', str(root / 'testbench.o')],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            facts = extract_top_type_contract(source, 'top', source_path=path)
            self.assertEqual(facts['status'], 'confirmed', facts)
            self.assertEqual(facts['required_type_names'], ['Data'])
            self.assertIn('#include <ap_int.h>', facts['declaration_context'])
            self.assertNotIn('struct ap_int', facts['declaration_context'])
            replay = extract_top_type_contract(
                facts['declaration_context'] + '\nvoid top(Data *p);', 'top',
                source_path=path,
            )
            self.assertEqual(replay['status'], 'confirmed', replay)
            self.assertEqual(replay['fingerprint'], facts['fingerprint'])

    def test_anonymous_nested_field_types_have_stable_independent_facts(self):
        source = 'struct Data { struct { int value; } inner; union { char tag; int count; } items[2]; }; void top(Data *p);'
        facts = extract_top_type_contract(source, 'top')
        self.assertEqual(facts['status'], 'confirmed', facts)
        names = {item['name'] for item in facts['types']}
        self.assertEqual(names, {'Data', 'Data::inner', 'Data::items'})
        replay = extract_top_type_contract('\n\n' + facts['declaration_context'] + '\nvoid top(Data *p);', 'top')
        self.assertEqual(replay['status'], 'confirmed', replay)
        self.assertEqual(replay['fingerprint'], facts['fingerprint'], (replay, facts))

    def test_equivalent_typedef_spelling_preserves_semantic_layout(self):
        public = extract_top_type_contract('struct Data { int value; }; void top(Data *p);', 'top')
        changed = extract_top_type_contract('typedef int Scalar; struct Data { Scalar value; }; typedef Data Alias; void top(Alias *p);', 'top')
        self.assertEqual(changed['status'], 'confirmed', changed)
        self.assertNotEqual(public['types'], changed['types'])
        self.assertEqual(public['fingerprint'], changed['fingerprint'])

    def test_actual_compiler_default_standard_and_explicit_override(self):
        source = '#include <optional>\nint top(std::optional<int> value) { return value.value_or(0); }'
        default = inspect_top_entry(source, 'top')
        self.assertTrue(default['translation_unit_complete'], default)
        self.assertEqual(default['parse_context']['language_standard'], 'gnu++17')
        older = inspect_top_entry(source, 'top', compile_flags=('-std=c++14',))
        self.assertFalse(older['translation_unit_complete'], older)
        self.assertEqual(older['parse_context']['language_standard'], 'c++14')
        self.assertTrue(older['diagnostics'])


if __name__ == '__main__':
    unittest.main()
