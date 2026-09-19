from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib, json, sys, tempfile
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from agrefactor.recovery import AdvisoryConfidence, AdvisoryOwner, AdvisoryRepairScope, CalibrationCertificate, DiagnosticAdvisory, R5MemoryPayload
from agrefactor.recovery.gated_candidate_repair import R4CanaryManifest
from agrefactor.recovery.pattern_lifecycle import Lifecycle, LifecycleReduction, R5PatternRevision
from agrefactor.recovery.r4_provenance import canonical_artifact_sha256
from agrefactor.recovery.r5_authorization import R5AuthorizationMode, R5ResearchAuthorization
from agrefactor.recovery.r5_memory_payload import memory_payload_manifest_sha256
from agrefactor.recovery.r5_snapshot_builder import R5MemorySnapshot
from agrefactor.runtime import BudgetLimits
from agrefactor.runtime.r5_binding import R5RuntimeBinding
from agrefactor.runtime.r5_integration import ExistingOrchestratorR5Integration, R5IntegrationConfig
from agrefactor.runtime.r5_profile import R5Arm, resolve_r5_profile
from tests.test_r4_existing_orchestrator_integration import Mutation, R4ExistingOrchestratorIntegrationTests, helpers
ADMISSION_ROOT=Path("/data/agrefactor_runs/r5_p3_authorized_revalidation_admission_a55a9bb")
CALIBRATION_BUNDLE=Path("/data/agrefactor_runs/v23_r2_real_calibration_identity_v2_20260918/calibration_bundle.json")
STATE_PATH=ROOT/"docs/roadmap/V2_3_STATE.json"
def _sha_text(v): return hashlib.sha256(v.encode()).hexdigest()
def _sha_bytes(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def _json(p):
    v=json.loads(Path(p).read_text(encoding="utf-8"))
    if not isinstance(v,dict): raise RuntimeError(f"expected JSON object: {p}")
    return v
def _write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
def _load_admission():
    state=_json(STATE_PATH)
    if state.get("R4_ACCEPTED") is not True: raise RuntimeError("R4 must be accepted before the R5 wiring smoke")
    if state.get("R5_ACCEPTED") is not False or state.get("R6_STARTED") is not False: raise RuntimeError("smoke cannot run after R5 acceptance or R6 start")
    expected={"admission_result.json":"R5_AUTHORIZED_CANDIDATE_REVALIDATION_ADMISSION_RESULT_FILE_SHA256","admission_manifest.json":"R5_AUTHORIZED_CANDIDATE_REVALIDATION_ADMISSION_MANIFEST_FILE_SHA256","memory_snapshot.json":None,"memory_payload.json":None,"lifecycle_reduction.json":None,"evidence_inventory.json":None}
    for name,key in expected.items():
        p=ADMISSION_ROOT/name
        if not p.is_file(): raise RuntimeError(f"missing Trusted admission artifact: {p}")
        if key and _sha_bytes(p)!=state[key]: raise RuntimeError(f"admission artifact hash mismatch: {name}")
    result=_json(ADMISSION_ROOT/"admission_result.json"); manifest=_json(ADMISSION_ROOT/"admission_manifest.json"); sv=_json(ADMISSION_ROOT/"memory_snapshot.json"); pv=_json(ADMISSION_ROOT/"memory_payload.json"); rv=_json(ADMISSION_ROOT/"lifecycle_reduction.json"); inv=_json(ADMISSION_ROOT/"evidence_inventory.json")
    audit_path=ADMISSION_ROOT.parent/"r5_p3_authorized_revalidation_admission_audit_a55a9bb"/"independent_audit.json"
    audit=_json(audit_path)
    if audit.get("status")!="clean_verified_positive_lifecycle_admission" or audit.get("critical_finding_count")!=0 or audit.get("blocking_finding_count")!=0:
        raise RuntimeError("Trusted admission independent audit is not clean")
    if audit.get("admission_result_sha256") != _sha_bytes(ADMISSION_ROOT/"admission_result.json"):
        raise RuntimeError("admission result is not bound to its independent audit")
    if result.get("future_files_read") is not False or result.get("future_outcomes_observed") is not False or inv.get("future_files_read") is not False: raise RuntimeError("Trusted admission crossed the future holdout")
    if sv.get("snapshot_sha256")!=state["R5_AUTHORIZED_CANDIDATE_REVALIDATION_SNAPSHOT_SHA256"]: raise RuntimeError("snapshot identity mismatch")
    if pv.get("payload_sha256")!=state["R5_AUTHORIZED_CANDIDATE_REVALIDATION_PAYLOAD_SHA256"]: raise RuntimeError("payload identity mismatch")
    if rv.get("revision_sha256")!=state["R5_AUTHORIZED_CANDIDATE_REVALIDATION_REVISION_SHA256"]: raise RuntimeError("revision identity mismatch")
    return state,manifest,sv,pv,rv
def _objects(sv,pv,rv):
    revision=R5PatternRevision(revision_id=rv["revision_id"],parent_revision_id=rv["parent_revision_id"],failure_family=rv["failure_family"],stage=rv["stage"],owner=rv["owner"],supported_when=rv["supported_when"],avoid_when=rv["avoid_when"],exact_exclusions=rv["exact_exclusions"],required_evidence=tuple(rv["required_evidence"]),positive_episode_refs=tuple(rv["positive_episode_refs"]),negative_episode_refs=tuple(rv["negative_episode_refs"]),calibration_refs=tuple(rv["calibration_refs"]),memory_payload_manifest_sha256=rv["memory_payload_manifest_sha256"],lifecycle=Lifecycle(rv["lifecycle"]),transition_reason=rv["transition_reason"],threshold_source=rv["threshold_source"],created_at=rv["created_at"],revision_sha256=rv["revision_sha256"])
    snapshot=R5MemorySnapshot(snapshot_id=sv["snapshot_id"],frozen_at=sv["frozen_at"],latest_allowed_timestamp=sv["latest_allowed_timestamp"],selected_revision_hashes=tuple(sv["selected_revision_hashes"]),rejected_revision_hashes=tuple(sv["rejected_revision_hashes"]),lifecycle_policy_sha256=sv["lifecycle_policy_sha256"],evidence_inventory_sha256=sv["evidence_inventory_sha256"],exact_exclusions=sv["exact_exclusions"],conflict_sparsity_ood_facts=sv["conflict_sparsity_ood_facts"],history_episode_ids=tuple(sv["history_episode_ids"]),snapshot_sha256=sv["snapshot_sha256"])
    payload=R5MemoryPayload(**pv); reduction=LifecycleReduction(revision=revision,eligible_episode_ids=tuple(revision.positive_episode_refs),rejected_episode_ids=(),positive_count=3,negative_count=0,independent_sources=2,independent_contexts=3,negative_rate=0.0,false_or_unsafe_count=0)
    return revision,snapshot,payload,reduction
class _BundleAdvisor:
    def __init__(self,identity): self._identity=dict(identity)
    @property
    def identity(self): return dict(self._identity)
    def diagnose(self,request): return DiagnosticAdvisory(suspected_owner=AdvisoryOwner.CANDIDATE,suspected_failure_class="unsupported_construct",evidence_refs=(request.evidence_ids[0],),repair_scope=AdvisoryRepairScope.CANDIDATE_ONLY,confidence=AdvisoryConfidence.HIGH,metadata={"strict_parser":"r2-v1","prompt_contract_version":"r2-shadow-output-v4"})
def _authorization(arm,profile,revision,snapshot,payload):
    mode={R5Arm.A2:R5AuthorizationMode.ADVISOR_ONLY,R5Arm.A3:R5AuthorizationMode.SIMILARITY_ONLY,R5Arm.A4:R5AuthorizationMode.GATED_MEMORY,R5Arm.A5:R5AuthorizationMode.GATED_MEMORY,R5Arm.A6:R5AuthorizationMode.GATED_MEMORY}[arm]
    v={"authorization_id":"smoke-"+arm.value,"arm_id":arm.value,"mode":mode,"calibration_certificate_sha256":_sha_text("calibration"),"advisory_sha256":_sha_text("advisory-"+arm.value),"policy_sha256":_sha_text("policy-"+arm.value),"ledger_sha256":_sha_text("ledger-"+arm.value),"budget_reservation_sha256":_sha_text("budget-"+arm.value),"r4_controller_contract_sha256":_sha_text("r4-controller"),"memory_mode":profile.memory_mode}
    if arm is R5Arm.A3: v["retrieval_manifest_sha256"]=_sha_text("r5-wiring-retrieval-manifest")
    if arm in {R5Arm.A4,R5Arm.A5,R5Arm.A6}: v.update(gate_contract_sha256=_sha_text("gate-"+arm.value),revision_sha256=revision.revision_sha256,snapshot_sha256=snapshot.snapshot_sha256,payload_manifest_sha256=memory_payload_manifest_sha256((payload,)))
    return R5ResearchAuthorization(**v)
def run(output):
    state,manifest,sv,pv,rv=_load_admission(); revision,snapshot,payload,reduction=_objects(sv,pv,rv); bundle=_json(CALIBRATION_BUNDLE); cv=bundle["certificate"]
    cert=CalibrationCertificate(split_id=cv["split_id"],split_sha256=cv["split_sha256"],report_sha256=cv["report_sha256"],policy_sha256=cv["policy_sha256"],provider_identity_sha256=cv["provider_identity_sha256"],prompt_contract_version=cv["prompt_contract_version"],strict_parser=cv["strict_parser"],input_contract_version=cv["input_contract_version"],eligible_confidence_labels=tuple(cv["eligible_confidence_labels"]),accepted=cv["accepted"],reasons=tuple(cv["reasons"]),certificate_id=cv["certificate_id"])
    model=helpers(); base=R4ExistingOrchestratorIntegrationTests(); adapter,_=model.make_adapter([model.P1]); request=replace(model.make_request(max_attempts=1),llm_advisory_mode="candidate-only")
    main=model.CandidateRepairValidationOrchestrator(model_adapter=adapter,handler_factory=model.ScenarioFactory(base._scenario(model)),shadow_advisor=_BundleAdvisor(bundle["provider_identity"])).run(model.make_context(limits=BudgetLimits(max_llm_calls=8,max_tool_calls=20,max_compile_calls=20,max_csim_calls=10,max_csynth_calls=10,max_cosim_calls=10,max_wall_time_s=5000)),request,validation_id="r5-a0-a6-wiring")
    events=tuple(main.metadata.get("diagnostic_events",()))
    if len(events)!=1: raise RuntimeError(f"wiring smoke expected one event, got {len(events)}")
    event=events[0]; target=event["target_identity"]["fingerprint"]; toolchain=event["toolchain_identity"]["fingerprint"]; model_name=bundle["provider_identity"]["model_name"]
    identity={"run_id":event["run_id"],"case_id":"r5-a0-a6-wiring","stage":event["stage"],"identity_complete":True,"hidden_input_count":0,"secret_present":False,"private_reasoning_present":False,"source_sha256":_sha_text(request.original_code),"target_identity":target,"toolchain_identity":toolchain,"parser_identity":"r5-wiring-parser","model_identity":model_name,"prompt_sha256":_sha_text("r5-wiring-prompt")}
    seed=R4CanaryManifest(manifest_id="r5-a0-a6-wiring-canary",manifest_sha256="0"*64,enabled=True,operator_enabled=True,case_ids=("r5-a0-a6-wiring",),source_sha256=_sha_text(request.original_code),target_identity=target,toolchain_identity=toolchain,parser_identity="r5-wiring-parser",model_identity=model_name,prompt_sha256=_sha_text("r5-wiring-prompt"),allowed_stage=event["stage"],expires_at="2099-01-01T00:00:00Z"); canary=replace(seed,manifest_sha256=canonical_artifact_sha256(seed.to_dict()))
    smoke_root=Path(tempfile.mkdtemp(prefix="r5_a0_a6_wiring_",dir="/tmp")); observations={}
    for arm in R5Arm:
        profile=resolve_r5_profile(arm.value)
        if arm in {R5Arm.A0,R5Arm.A1}:
            binding=R5RuntimeBinding(profile); observations[arm.value]={"profile":profile.to_dict(),"binding":binding.to_dict(),"status":"observation_only","provider_calls":0,"vitis_launches":0,"mutation_calls":0,"cross_arm_cache_used":False}; continue
        auth=_authorization(arm,profile,revision,snapshot,payload); kwargs={}; memory_payloads=(); retrieval=None
        if arm is R5Arm.A3: retrieval=_sha_text("r5-wiring-retrieval-manifest"); kwargs["retrieval_manifest_sha256"]=retrieval
        if arm in {R5Arm.A4,R5Arm.A5,R5Arm.A6}: memory_payloads=(payload,); kwargs.update(memory_snapshot=snapshot,revision=revision,lifecycle_reduction=reduction,memory_payloads=memory_payloads)
        mutation=Mutation(model.P1); integration=ExistingOrchestratorR5Integration(R5IntegrationConfig(profile=profile,canary=canary,execution_identity=identity,calibration_certificate=cert,episode_ledger_root=str(smoke_root/arm.value/"episodes"),campaign_manifest_sha256=_sha_text("r5-a0-a6-wiring-campaign"),mutation_adapter=mutation,validation_wall_time_s=100,**kwargs))
        binding=R5RuntimeBinding(profile,authorization=auth,memory_payloads=memory_payloads,retrieval_manifest_sha256=retrieval,r4_integration_factory=lambda _: integration)
        result=integration.run_from_existing_orchestrator(context=model.make_context(limits=BudgetLimits(max_llm_calls=4,max_tool_calls=20,max_csim_calls=10,max_csynth_calls=10,max_cosim_calls=10,max_wall_time_s=5000)),request=replace(request,r5_arm=arm.value,llm_advisory_mode="candidate-only"),main_result=main,handler_factory=model.ScenarioFactory(model.pass_scenario))
        observations[arm.value]={"profile":profile.to_dict(),"binding":binding.to_dict(),"status":result.get("status"),"gate":result.get("gate"),"provider_calls":0,"vitis_launches":0,"mutation_calls":mutation.calls,"cross_arm_cache_used":False}
    for arm in ("A2","A3","A4","A5","A6"):
        if observations[arm]["status"]!="verified_positive" or observations[arm]["mutation_calls"]!=1: raise RuntimeError(f"{arm} did not traverse the mutation integration positively: {observations[arm]}")
    for arm in ("A4","A5","A6"):
        if observations[arm]["gate"]["decision"]!="accept": raise RuntimeError(f"{arm} gate did not accept the clean smoke context")
    result={"schema_version":"r5-a0-a6-wiring-smoke-v1","synthetic_smoke":True,"repository_head":state["head"],"r4_accepted":state["R4_ACCEPTED"],"r5_accepted":state["R5_ACCEPTED"],"r6_started":state["R6_STARTED"],"trusted_revision_sha256":revision.revision_sha256,"trusted_snapshot_sha256":snapshot.snapshot_sha256,"trusted_payload_sha256":payload.payload_sha256,"history_only":True,"future_files_read":False,"arms":observations,"provider_calls":0,"vitis_launches":0,"git_history_mutations":0,"cross_arm_cache_used":False,"status":"passed","reason":"all_a0_a6_profiles_and_existing_refactor_orchestrator_wiring_passed","evidence_root":str(output)}
    _write(output/"wiring_smoke.json",result); _write(output/"protocol_audit.json",{"schema_version":"r5-a0-a6-wiring-protocol-audit-v1","status":"clean","critical_findings":[],"future_files_read":False,"provider_calls":0,"vitis_launches":0,"git_history_mutations":0,"source_path":"existing_refactor_candidate_orchestrator","arm_ids":list(observations),"manifest_sha256":manifest["manifest_sha256"]}); return result
def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,default=Path("/data/agrefactor_runs/r5_p4_a0_a6_wiring_smoke")); args=parser.parse_args(); result=run(args.output.resolve())
    print("R5_A0_A6_WIRING_SMOKE_STATUS=passed"); print("R5_A0_A6_WIRING_SMOKE_REASON="+result["reason"]); print("PROVIDER_CALLS=0"); print("VITIS_LAUNCHES=0"); print("GIT_HISTORY_MUTATIONS=0"); print("R5_ACCEPTED=false"); print("R6_STARTED=false"); return 0
if __name__=="__main__": raise SystemExit(main())

