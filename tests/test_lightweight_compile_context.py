from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agrefactor.cpp_interface import extract_top_interface, inspect_top_entry
from agrefactor.reference_source import isolate_reference_program_entry
from agrefactor.evaluation.testbench_preflight import TestbenchPreflight
from flow.tools import input_domain
import subprocess
from agrefactor.recovery.policy import RecoveryLimits
from flow.tools import tb_coverage, tb_hidden_eval, tb_optimizer, testbench


class LightweightCompileContextTests(unittest.TestCase):
    def test_reference_qualification_requires_actual_entry_execution(self):
        source = 'int origin(int x){return x+1;}'
        for driver, expected in [
            ('int origin(int); int trial(int); int main(){return origin(2)!=trial(2);}', 'ok'),
            ('int origin(int); int trial(int); int main(){return 0;}', 'qualification_failed'),
        ]:
            result = tb_coverage.check_original_execution(
                source, driver, 'int trial(int);', 'trial', required_original_entry='origin',
            )
            self.assertEqual(result['status'], expected, result)
            self.assertEqual(result['original_entry_executed'], expected == 'ok')
            if expected == 'qualification_failed':
                self.assertTrue(result['original_entry_observed'])
                self.assertEqual(result['failure_owner'], 'testbench')
                self.assertEqual(result['next_action'], 'repair_testbench')

    def test_qualified_original_and_unknown_mismatch_preserve_unknown(self):
        failure = {'status': 'run_failed', 'failure_owner': 'unknown', 'next_action': 'review_unknown', 'run_stderr': 'different values'}
        with patch.object(tb_optimizer, 'check_original_execution', return_value={'status': 'ok'}), \
             patch.object(tb_optimizer, 'measure_coverage', return_value=failure) as measure:
            result = tb_optimizer._measure_qualified_coverage(
                'int origin(int x){return x;}', 'int origin_hls(int); int main(){return 0;}', 'int origin_hls(int){return 9;}', 'origin',
            )
        self.assertEqual(measure.call_args.kwargs['original_name'], 'origin')
        self.assertEqual(result['failure_owner'], 'unknown')
        self.assertEqual(result['next_action'], 'review_unknown')

    def test_nan_comparison_failure_is_not_assumed_to_be_a_stub_error(self):
        source = '#include <cmath>\nvoid origin(float *out){*out=std::nanf("");}'
        driver = '''#include <cmath>
void origin(float *); void origin_hls(float *);
int main(){float a=0,b=0;origin(&a);origin_hls(&b);return a==b ? 0 : 1;}'''
        stub = '#include <cmath>\nvoid origin_hls(float *out){*out=std::nanf("");}'
        result = tb_optimizer._measure_qualified_coverage(
            source, driver, stub, 'origin',
        )
        self.assertEqual(result['status'], 'run_failed', result)
        self.assertEqual(result['failure_owner'], 'unknown', result)
        self.assertEqual(result['next_action'], 'review_unknown', result)

    def test_entry_facts_distinguish_missing_member_template_and_unknown(self):
        sources = [
            ('void different() {}', 'missing', None),
            ('class Owner { public: int execute() {return 1;} };', 'confirmed', 'member'),
            ('template<int L> void execute(int (&values)[L]) {}', 'confirmed', 'template'),
            ('#include "absent.hpp"\nvoid execute() {}', 'unknown', None),
        ]
        for source, status, kind in sources:
            facts = inspect_top_entry(source, 'execute')
            self.assertEqual(facts['status'], status, facts)
            if kind:
                self.assertEqual(facts['entries'][0]['kind'], kind)

    def test_internal_linkage_bridge_runs_in_original_unit(self):
        source = 'namespace { int private_entry(int value) {return value*3;} }'
        reference = isolate_reference_program_entry(source, top_function='private_entry')
        driver = 'int private_entry(int); int trial(int); int main(){return private_entry(7)!=trial(7);}'
        with tempfile.TemporaryDirectory() as root:
            result = TestbenchPreflight().compile_and_link(
                work_dir=root, testbench_code=driver, original_code=reference,
                candidate_code='int trial(int value){return value*3;}',
                original_top_function='private_entry', candidate_top_function='trial',
            )
            self.assertEqual(result.status.value, 'passed', result.stderr)
            self.assertEqual(subprocess.run([str(Path(root)/'testbench_preflight')]).returncode, 0)

    def test_template_binding_is_deduced_with_array_references(self):
        source = 'template<int L, typename E> void transform(E (*input)[L], E (&output)[L][L]) {output[0][0]=input[0][0]+2;}'
        driver = 'void transform(double (*input)[4], double (&output)[4][4]); int main(){double a[4][4]={}, b[4][4]={}; transform(a,b);return b[0][0]!=2;}'
        reference = isolate_reference_program_entry(source, top_function='transform', testbench_code=driver)
        interface = extract_top_interface(reference, 'transform')
        self.assertIsNotNone(interface)
        self.assertIn('double (&)[4][4]', interface.parameters[1].canonical_type)
        with tempfile.TemporaryDirectory() as root:
            Path(root,'reference.cpp').write_text(reference)
            Path(root,'driver.cpp').write_text(driver)
            compiled = subprocess.run(['g++', 'reference.cpp', 'driver.cpp', '-o', 'check'], cwd=root, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(subprocess.run([str(Path(root)/'check')]).returncode, 0)

    def test_ambiguous_template_overloads_keep_template_bridge(self):
        source = (
            'template<class T> int execute(T value) { return value + 1; } '
            'template<int N> int execute(int value) { return value + N; }'
        )
        driver = 'int execute(int); int main(){return execute(2)!=3;}'
        reference = isolate_reference_program_entry(
            source, top_function='execute', testbench_code=driver,
        )
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'reference.cpp').write_text(reference)
            Path(root, 'driver.cpp').write_text(driver)
            compiled = subprocess.run(
                ['g++', 'reference.cpp', 'driver.cpp', '-o', 'check'],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(subprocess.run([str(Path(root) / 'check')]).returncode, 0)

    def test_ambiguous_template_entry_is_detected_for_coverage_bridge(self):
        source = (
            'template<class T> void origin(T &value) { value += 2; } '
            'template<int N> void origin(int &value) { value += N; }'
        )
        facts = inspect_top_entry(source, 'origin')
        self.assertEqual(facts['status'], 'ambiguous', facts)
        self.assertTrue(any(item['kind'] == 'template' for item in facts['entries']))

    def test_original_only_qualification_instantiates_template_in_same_unit(self):
        source = 'template<int L> void origin(int (&values)[L]) { values[0] += 2; }'
        driver = '''void origin(int (&values)[4]);
void trial(int (&values)[4]);
        int main(){int golden[4]={};int candidate[4]={};origin(golden);trial(candidate);return 0;}'''
        result = tb_coverage.check_original_execution(
            source,
            driver,
            'void trial(int (&values)[4]);',
            'trial',
            original_name='origin',
            required_original_entry='origin',
        )
        self.assertEqual(result['status'], 'ok', result)
        self.assertTrue(result['original_entry_executed'], result)

    def test_template_bridge_runtime_extent_failure_routes_to_testbench(self):
        """A runtime scalar cannot be guessed as a template extent.

        The compiler diagnostic is the generic evidence used to request a
        Testbench repair; the flow must not hard-code a template argument or
        attribute this bridge mismatch to the Original implementation.
        """
        source = (
            'template<int L, typename E> '
            'void transform(E (*input)[L], E (&output)[L][L]) '
            '{ output[0][0] = input[0][0] + 2; }'
        )
        driver = (
            'void transform(int extent, double input[4][4], '
            'double output[4][4]); '
            'void trial(int extent, double input[4][4], '
            'double output[4][4]); '
            'int main(){ double a[4][4] = {}; double b[4][4] = {}; '
            'transform(4, a, b); trial(4, a, b); return 0; }'
        )
        result = tb_coverage.check_original_execution(
            source,
            driver,
            'void trial(int extent, double input[4][4], '
            'double output[4][4]);',
            'trial',
            original_name='transform',
            required_original_entry='transform',
        )
        self.assertEqual(result['status'], 'compile_failed', result)
        self.assertEqual(result['failure_owner'], 'testbench', result)
        self.assertEqual(result['next_action'], 'repair_testbench', result)
        self.assertIn('template argument deduction', result['compile_stderr'])

    def test_template_coverage_runs_original_and_testbench_in_one_unit(self):
        source = 'template<int L> void origin(int (&values)[L]) { values[0] += 2; }'
        driver = '''void origin(int (&values)[4]);
void trial(int (&values)[4]);
int main(){int golden[4]={};int candidate[4]={};origin(golden);trial(candidate);return golden[0]!=candidate[0];}'''
        candidate = 'void trial(int (&values)[4]) { values[0] += 2; }'
        result = tb_coverage.measure_coverage(
            source,
            driver,
            '#include "testbench.cpp"\n' + candidate,
            original_name='origin',
        )
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['run_returncode'], 0, result)

    def test_staged_preflight_instantiates_template_original_with_testbench(self):
        source = 'template<int L> void origin(int (&values)[L]) { values[0] += 2; }'
        reference = isolate_reference_program_entry(source, top_function='origin')
        driver = '''template<int L> void origin(int (&values)[L]);
void trial(int (&values)[4]);
int main(){int golden[4]={};int candidate[4]={};origin<4>(golden);trial(candidate);return golden[0]!=candidate[0];}'''
        candidate = 'void trial(int (&values)[4]) { values[0] += 2; }'
        with tempfile.TemporaryDirectory() as root:
            result = TestbenchPreflight().compile_and_link(
                work_dir=root,
                testbench_code=driver,
                original_code=reference,
                candidate_code=candidate,
                original_top_function='origin',
                candidate_top_function='trial',
            )
            self.assertEqual(result.status.value, 'passed', result.stderr)
            self.assertEqual(subprocess.run([str(Path(root) / 'testbench_preflight')]).returncode, 0)

    def test_hidden_eval_forwards_original_template_identity(self):
        source = 'template<int L> void origin(int (&values)[L]) { values[0] += 2; }'
        driver = '''void origin(int (&values)[4]);
void trial(int (&values)[4]);
int main(){int golden[4]={};int candidate[4]={};origin(golden);trial(candidate);return golden[0]!=candidate[0];}'''
        candidate = 'void trial(int (&values)[4]) { values[0] += 2; }'
        result = tb_hidden_eval.eval_against_hidden_tb(
            source,
            candidate,
            driver,
            original_name='origin',
        )
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['failure_kind'], 'pass', result)

    def test_global_array_state_detects_a_wrong_candidate(self):
        source = 'int values[5]; int size; void update(int step){for(int i=0;i<size;++i)values[i]+=step;}'
        reference = isolate_reference_program_entry(source, top_function='update')
        self.assertIn('agrefactor_state_values', reference)
        driver = '''void update(int, int &, int (&)[5]);
void trial(int, int &, int (&)[5]);
int main(){int n=5,m=5,a[5]={},b[5]={};update(2,n,a);trial(2,m,b);for(int i=0;i<5;++i)if(a[i]!=b[i])return 1;return n!=m;}'''
        with tempfile.TemporaryDirectory() as root:
            result = TestbenchPreflight().compile_and_link(
                work_dir=root, testbench_code=driver, original_code=reference,
                candidate_code='void trial(int, int &, int (&)[5]) {}',
                original_top_function='update', candidate_top_function='trial',
            )
            self.assertEqual(result.status.value, 'passed', result.stderr)
            self.assertEqual(subprocess.run([str(Path(root)/'testbench_preflight')]).returncode, 1)

    def test_header_macro_and_support_unit_share_compilation_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'types.hpp').write_text('using Value = long; Value helper(Value);')
            (root/'support.cpp').write_text('#include "types.hpp"\nValue helper(Value x){return x+SHIFT;}')
            driver = '#include "types.hpp"\nValue origin(Value); Value trial(Value); int main(){return origin(4)!=trial(4);}'
            result = tb_coverage.check_original_execution(
                '#include "types.hpp"\nValue origin(Value x){return helper(x);}', driver,
                'Value trial(Value x);', 'trial', source_root=directory,
                extra_sources=(str(root/'support.cpp'),), compile_flags=('-DSHIFT=9',),
            )
            self.assertEqual(result['status'], 'ok', result)

    def test_namespace_state_bridge_uses_compiler_qualified_names(self):
        source = '''namespace left {int value;} namespace right {int value;}
void change(int amount){left::value+=amount;right::value-=amount;}'''
        reference = isolate_reference_program_entry(source, top_function='change')
        driver = '''void change(int, int &, int &);
int main(){int a=2,b=8;change(3,a,b);return a!=5||b!=5;}'''
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'reference.cpp').write_text(reference)
            Path(root, 'driver.cpp').write_text(driver)
            compiled = subprocess.run(
                ['g++', 'reference.cpp', 'driver.cpp', '-o', 'check'],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertEqual(subprocess.run([str(Path(root)/'check')]).returncode, 0)

    def test_third_input_domain_repair_retains_every_failure(self):
        loader = Mock()
        loader.load_agent.return_value = Mock()
        with patch.object(input_domain, 'HLSAgentLoader', return_value=loader), \
             patch.object(input_domain, '_json_response', side_effect=[ValueError('first'), ValueError('second'), ValueError('third'), '{}']) as request, \
             patch.object(input_domain, 'normalize_input_domain_contract', return_value={}):
            input_domain.generate_input_domain_contract(orig_code='int task(){return 1;}', kernel_name='task')
        self.assertEqual(request.call_count, 4)
        for evidence in ('first', 'second', 'third'):
            self.assertIn(evidence, request.call_args.args[1])
    def test_header_and_macro_context_resolves_array_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "types.hpp").write_text("using value_type = unsigned long;\n")
            source = '#include "types.hpp"\nvoid task(value_type (&values)[WIDTH]) {}\n'
            interface = extract_top_interface(
                source, "task", source_path=root / "kernel.cpp",
                include_dirs=(directory,), compile_flags=("-DWIDTH=7",),
            )
            self.assertIsNotNone(interface)
            self.assertIn("unsigned long (&)[7]", interface.parameters[0].canonical_type)

    def test_missing_header_is_unknown(self):
        self.assertIsNone(extract_top_interface('#include "absent_type.hpp"\nvoid task(int x) {}', "task"))

    def test_unknown_signature_is_not_recovered_as_int(self):
        self.assertIsNone(extract_top_interface('void task(missing_type &x) {}', "task"))

    def test_unnamed_valid_declaration_is_available(self):
        interface = extract_top_interface('void task(const int &);', "task", require_definition=False)
        self.assertIsNotNone(interface)
        self.assertEqual(interface.parameters[0].canonical_type, "const int &")

    def test_cpp_standard_library_types_keep_their_identity(self):
        interface = extract_top_interface('#include <string>\nvoid task(const std::string &x) {}', "task")
        self.assertIsNotNone(interface)
        self.assertIn("basic_string", interface.parameters[0].canonical_type)

    def test_original_qualification_reuses_testbench_type_context(self):
        original = '#include <string>\nvoid origin(const std::string &x) {}\n'
        driver = '#include <string>\nusing Text = std::string;\nvoid origin(const Text &x);\nvoid trial(const Text &x);\nint main(){Text x; origin(x); trial(x); return 0;}\n'
        result = tb_coverage.check_original_execution(original, driver, 'void trial(const Text &x);', 'trial')
        self.assertEqual(result['status'], 'ok', result)

    def test_invalid_stub_is_not_routed_to_testbench(self):
        result = tb_coverage.check_original_execution(
            'void origin() {}', 'int main(){return 0;}',
            'void trial(unavailable_type &x);', 'trial',
        )
        self.assertEqual(result['failure_owner'], 'stub', result)
        self.assertEqual(result['next_action'], 'regenerate_stub')

    def test_public_runtime_testbench_limits_are_three(self):
        limits = RecoveryLimits()
        self.assertEqual(limits.testbench_public_csim_repairs, 3)
        self.assertEqual(limits.testbench_public_cosim_repairs, 3)
        self.assertEqual(limits.hidden_repairs, 0)

    def test_third_public_qualification_repair_has_all_failures(self):
        driver = 'void origin(); void origin_hls(); int main(){origin();origin_hls();return 0;}'
        agent = Mock()
        response = Mock(messages=[{'content': 'instruction'}])
        agent.run.return_value = response
        loader = Mock()
        loader.load_agent.return_value = agent
        failures = [dict(status='original_run_failed', failure_owner='original', run_stderr=f'failure-{i}') for i in range(3)]
        context = {'kernel_name': 'origin', 'curr_code': 'void origin() {}'}
        with patch.object(testbench, 'HLSAgentLoader', return_value=loader), \
             patch.object(tb_optimizer, '_request_cpp_artifact', return_value=driver) as request, \
             patch.object(tb_coverage, 'check_original_execution', side_effect=[*failures, {'status': 'ok'}]):
            testbench.gen_tb_prior(context)
        self.assertEqual(request.call_count, 4)
        last_prompt = request.call_args_list[-1].args[1]
        for index in range(3):
            self.assertIn(f'failure-{index}', last_prompt)

    def test_stub_failure_consumes_no_testbench_repairs(self):
        driver = 'void origin(); void origin_hls(); int main(){origin();origin_hls();return 0;}'
        loader = Mock()
        loader.load_agent.return_value = Mock()
        context = {'kernel_name': 'origin', 'curr_code': 'void origin() {}'}
        with patch.object(testbench, 'HLSAgentLoader', return_value=loader), \
             patch.object(tb_optimizer, '_request_cpp_artifact', return_value=driver) as request, \
             patch.object(tb_coverage, 'check_original_execution', return_value={'status': 'compile_failed', 'failure_owner': 'stub'}):
            with self.assertRaises(tb_optimizer.TestbenchGenerationExhausted):
                testbench.gen_tb_prior(context)
        self.assertEqual(request.call_count, 1)
