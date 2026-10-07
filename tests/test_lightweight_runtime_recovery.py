from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agrefactor.config import TestSourceKind, TestSourceSpec
from agrefactor.evaluation import FeedbackRouteAction, FeedbackRouter, ValidationState
from agrefactor.evidence import FeedbackOwner, FeedbackStage
from agrefactor.recovery import (
    RecoveryAction, RecoveryAuthority, RecoveryPolicy, RecoveryRequest,
    RecoveryRole, RecoveryStage,
)
from agrefactor.runtime import CandidateRepairOrchestrationRequest, CandidateRepairValidationOrchestrator
from agrefactor.runtime.preflight_stage import PreflightStageInputs, PreflightValidationStageHandler
from tests.test_candidate_repair_integration import (
    ScenarioFactory, feedback_item, make_adapter, make_context, make_task, pass_scenario, report_for,
)


class LightweightRuntimeRecoveryTests(unittest.TestCase):
    def test_unknown_public_link_failure_gets_three_repairs_after_real_reference_run(self):
        with tempfile.TemporaryDirectory() as root:
            task = replace(make_task(public=True, hidden=True), kernel_name='trial')
            original = 'int origin(int x){return x+1;}'
            driver = 'int origin(int);int trial(int);int main(){return origin(2)!=trial(2);}'
            broken = 'int unavailable(int);int trial(int x){return unavailable(x);}'
            adapter, provider = make_adapter([
                broken.replace('unavailable', 'second_dependency'),
                broken.replace('unavailable', 'third_dependency'),
                'int trial(int x){return x+1;}',
            ])

            def scenario(request, state):
                if state is ValidationState.PREFLIGHT:
                    handler = PreflightValidationStageHandler(PreflightStageInputs(
                        work_dir=Path(root) / f'attempt_{request.attempt}',
                        testbench_code=driver, original_code=original,
                        candidate_code=request.candidate_code,
                        original_top_function='origin', candidate_top_function='trial',
                    ))
                    return handler(make_context(task))
                return pass_scenario(request, state)

            result = CandidateRepairValidationOrchestrator(
                model_adapter=adapter, handler_factory=ScenarioFactory(scenario),
            ).run(make_context(task), CandidateRepairOrchestrationRequest(
                initial_candidate=broken, original_code=original,
                preflight_testbench_code=driver, prompt_public_testbench_code=driver,
                suite_testbench_codes={'public-main': driver, 'hidden-final': driver + '\n// HIDDEN_SECRET'},
                reference_top_function='origin', candidate_top_function='trial',
            ), validation_id='unknown-public-link')
            self.assertTrue(result.accepted, result.to_dict())
            self.assertEqual(len(provider.calls), 3)
            item = result.initial_validation.steps[0].selected_feedback_items[0]
            self.assertEqual(item.owner, FeedbackOwner.UNKNOWN)
            self.assertEqual(item.metadata['owner_authority'], 'public_reference_qualified')
            prompt = '\n'.join(message.content for message in provider.calls[-1][1].messages)
            for name in ('unavailable', 'second_dependency', 'third_dependency'):
                self.assertIn(name, prompt)
            self.assertIn('Ownership remains unknown', prompt)
            self.assertNotIn('HIDDEN_SECRET', prompt)

    def test_public_link_failure_without_reference_execution_stays_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            task = make_task(public=True, hidden=True)
            report = PreflightValidationStageHandler(PreflightStageInputs(
                work_dir=root, testbench_code='int origin(int);int trial(int);int main(){volatile int run=0;if(run)origin(0);return trial(0);}',
                original_code='int origin(int x){return x+1;}',
                candidate_code='int unavailable(int);int trial(int x){return unavailable(x);}',
                original_top_function='origin', candidate_top_function='trial',
            ))(make_context(task))
            self.assertEqual(FeedbackRouter().route(report, decision_id='no-entry-run').action,
                             FeedbackRouteAction.REVIEW_UNKNOWN)

    def test_third_runtime_testbench_repair_revalidates_and_keeps_all_failures(self):
        with tempfile.TemporaryDirectory() as root:
            task = make_task(public=True, hidden=True)
            task = replace(task, test_suites=(
                replace(task.test_suites[0], testbench_path=str(Path(root) / 'public.cpp'), source=TestSourceSpec(
                    'auto-public', source_kind=TestSourceKind.GENERATED,
                    generation_model='fake', generation_profile='lightweight',
                    operator_artifact_path=str(Path(root) / 'public.cpp'),
                    prompt_sha256='a' * 64, round_index=0, trajectory_id='trajectory-0',
                )),
                task.test_suites[1],
            ))
            original = 'int origin(int x){return x;}'
            candidate = 'int trial(int x){return x;}'
            driver = 'int origin(int); int trial(int); int main(){return origin(2)!=trial(2);}'
            proposals = [driver + f'\n// revision {index}' for index in range(1, 4)]
            seen = []

            class Repairer:
                def repair(self, request):
                    seen.append(request)
                    return proposals[len(seen)-1]

            def scenario(request, state):
                if state is ValidationState.PUBLIC_EVALUATION and request.attempt < 3:
                    item = replace(feedback_item(
                        f'runtime-{request.attempt}', state=state, owner=FeedbackOwner.TESTBENCH,
                    ), summary=f'runtime-{request.attempt}', metadata={
                        'suite_id': 'public-main',
                        'owner_authority': 'deterministic_proven',
                        'physical_tool_launched': True,
                        'tool_launched': True,
                        'evidence_complete': True,
                        'repair_eligible': True,
                    })
                    return report_for(state, item=item, report_id=f'failure-{request.attempt}')
                return pass_scenario(request, state)

            factory = ScenarioFactory(scenario)
            factory.work_root = Path(root)
            adapter, provider = make_adapter([])
            request = CandidateRepairOrchestrationRequest(
                initial_candidate=candidate, original_code=original,
                preflight_testbench_code=driver, prompt_public_testbench_code=driver,
                suite_testbench_codes={'public-main': driver, 'hidden-final': driver + '\n// HIDDEN_SECRET'},
                reference_top_function='origin', candidate_top_function='trial',
            )
            with patch('agrefactor.runtime.candidate_repair_integration.build_openai_compatible_testbench_repairer', return_value=Repairer()):
                result = CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=factory).run(
                    make_context(task), request, validation_id='runtime-recovery',
                )
            self.assertTrue(result.accepted, result.to_dict())
            self.assertEqual(len(seen), 3)
            self.assertEqual(result.metadata['runtime_testbench_repair_attempts_used'], 3)
            history = '\n'.join(seen[-1].prior_attempt_summaries)
            for index in range(3):
                self.assertIn(f'runtime-{index}', history)
            self.assertNotIn('HIDDEN_SECRET', history)
            self.assertEqual(len(provider.calls), 0)
            self.assertEqual(factory.requests[-1].suite_testbench_codes['hidden-final'], driver + '\n// HIDDEN_SECRET')

    def test_explicit_four_candidate_repairs_are_not_capped_by_policy_defaults(self):
        task = make_task(public=False, hidden=False)
        adapter, provider = make_adapter([f'int top(int x){{return x+{index};}}' for index in range(1, 5)])

        def scenario(request, state):
            if state is ValidationState.PREFLIGHT and request.attempt < 4:
                return report_for(state, item=feedback_item(f'compile-{request.attempt}', state=state))
            return pass_scenario(request, state)

        result = CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=ScenarioFactory(scenario)).run(
            make_context(task), CandidateRepairOrchestrationRequest(
                initial_candidate='int top(int x){return x;}', original_code='int top(int x){return x;}',
                preflight_testbench_code='int top(int); int main(){return top(0);}',
                suite_testbench_codes={}, prompt_public_testbench_code=None,
                max_attempts=4,
            ), validation_id='four-repairs',
        )
        self.assertTrue(result.accepted, result.to_dict())
        self.assertEqual(len(provider.calls), 4)
        prompt = '\n'.join(message.content for message in provider.calls[-1][1].messages)
        self.assertIn('Initial validation failure', prompt)
        self.assertIn('compile-0', prompt)

    def test_public_qualified_unknown_remains_unknown_and_hidden_is_terminal(self):
        state = ValidationState.PUBLIC_COSIM
        item = replace(feedback_item('unknown', state=state, owner=FeedbackOwner.UNKNOWN),
                       stage=FeedbackStage.COSIM,
                       metadata={'owner_authority': 'public_reference_qualified', 'repair_eligible': True,
                                 'evidence_complete': True, 'tool_launched': True})
        report = replace(report_for(state, item=item), metadata={
            'evidence_view': 'agent_safe', 'evaluation_split': 'public', 'feedback_visible_to_agent': True,
        })
        route = FeedbackRouter().route(report, decision_id='public-unknown')
        self.assertEqual(route.action, FeedbackRouteAction.REPAIR_CANDIDATE)
        self.assertEqual(report.items[0].owner, FeedbackOwner.UNKNOWN)
        self.assertEqual(route.metadata['owner_authority'], 'public_reference_qualified')
        policy = RecoveryPolicy()
        request = RecoveryRequest(RecoveryAction.REPAIR, RecoveryRole.CANDIDATE, RecoveryStage.PUBLIC_COSIM,
                                  'agent_safe', RecoveryAuthority.PUBLIC_REFERENCE_QUALIFIED, 'run', True, True)
        self.assertTrue(policy.decide(request).allowed)
        self.assertFalse(policy.decide(replace(request, stage=RecoveryStage.HIDDEN)).allowed)
        self.assertFalse(policy.decide(replace(request, physical_tool_launched=False)).allowed)

    def test_qualified_unknown_cosim_reaches_model_and_third_repair(self):
        task = replace(make_task(public=True, hidden=True), kernel_name='trial')
        adapter, provider = make_adapter([f'int trial(int x){{return x+{index};}}' for index in range(1, 4)])
        driver = 'int origin(int);int trial(int);int main(){return origin(2)!=trial(2);}'

        def scenario(request, state):
            if state is ValidationState.PUBLIC_COSIM and request.attempt < 3:
                item = replace(feedback_item(f'cosim-{request.attempt}', state=state, owner=FeedbackOwner.UNKNOWN),
                               stage=FeedbackStage.COSIM,
                               metadata={'owner_authority': 'public_reference_qualified', 'repair_eligible': True,
                                         'evidence_complete': True, 'tool_launched': True,
                                         'physical_tool_launched': True})
                return replace(report_for(state, item=item), metadata={
                    'evidence_view': 'agent_safe', 'evaluation_split': 'public', 'feedback_visible_to_agent': True,
                })
            return pass_scenario(request, state)

        result = CandidateRepairValidationOrchestrator(model_adapter=adapter, handler_factory=ScenarioFactory(scenario)).run(
            make_context(task), CandidateRepairOrchestrationRequest(
                initial_candidate='int trial(int x){return x;}', original_code='int origin(int x){return x+3;}',
                preflight_testbench_code=driver, prompt_public_testbench_code=driver,
                suite_testbench_codes={'public-main': driver, 'hidden-final': driver + '\n// HIDDEN_SECRET'},
                reference_top_function='origin', candidate_top_function='trial',
            ), validation_id='unknown-cosim-three',
        )
        self.assertTrue(result.accepted, result.to_dict())
        self.assertEqual(len(provider.calls), 3)
        prompt = '\n'.join(message.content for message in provider.calls[-1][1].messages)
        self.assertIn('Ownership remains unknown', prompt)
        self.assertNotIn('HIDDEN_SECRET', prompt)

    def test_unqualified_unknown_does_not_acquire_repair_authority(self):
        state = ValidationState.PUBLIC_COSIM
        report = report_for(state, item=feedback_item('unknown', state=state, owner=FeedbackOwner.UNKNOWN))
        self.assertEqual(FeedbackRouter().route(report, decision_id='no-reference').action, FeedbackRouteAction.REVIEW_UNKNOWN)
