from __future__ import annotations
import uuid
import time
from datetime import datetime, timezone
from .models import *
from .task_state import TaskState
from .scene_state import SceneObserver
from .planner import Planner
from .validator import PlanValidator
from .preflight import Preflight
from .executor import Executor
from .run_logger import RunLogger
MAX_IDENTICAL_COLLISIONS=2
class LocalSafePlanDisabled(RuntimeError):
    """The LLM could not produce a plan and the local safe plan fallback is switched off."""
    def __init__(self,code,reason):
        super().__init__(reason); self.code=code; self.reason=reason
class Experiment:
    def __init__(self,adapter,spec,mode='deterministic',disturbance_mode='automatic',log_dir='logs',max_replans=9,scenario='sequential-relocation',replanning_mode='hybrid-fast',model=None,allow_local_safe_plan=True):
        self.adapter=adapter; self.spec=spec; self.state=TaskState(spec,adapter.observe()); self.observer=SceneObserver(adapter); self.observer.last=self.state.snapshot; self.planner=Planner(mode,model=model); self.validator=PlanValidator(spec); self.preflight=Preflight(adapter); self.logger=RunLogger(log_dir,spec.task_id); self.executor=Executor(adapter,self.observer,self.logger);         self.disturbance_mode=disturbance_mode; self.max_replans=max_replans; self.scenario=scenario; self.replanning_mode=replanning_mode; self.gates={}; self.summary={}; self.llm_unavailable=False; self.allow_local_safe_plan=allow_local_safe_plan
    def run(self):
        started = time.perf_counter()
        try:
            try:
                state = self._run()
            except LocalSafePlanDisabled as exc:
                # Mark this trial failed (so a multi-trial run carries on)
                # instead of letting the exception abort the whole command.
                failure=Failure(exc.code,exc.reason)
                self.state.record_failure(failure)
                self.logger.event('result',{'success':False,'reason':failure.reason,'completed':self.state.completed_parts})
                self.logger.write_csv()
                state = self.state
        except Exception as exc:
            self._write_summary(False, started, exc)
            raise
        self._write_summary(state.verify(), started, None if state.verify() else state.failures[-1] if state.failures else None)
        return state

    def _write_summary(self, success, started, failure):
        calls = self.planner.calls
        failure_code = getattr(failure, 'code', '') if failure else ''
        failure_reason = getattr(failure, 'reason', str(failure) if failure else '')
        self.logger.write_summary({
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'task_id': self.spec.task_id,
            'success': success,
            'failure_code': failure_code,
            'failure_reason': failure_reason,
            'completed_parts': ','.join(self.state.completed_parts),
            'requested_parts': ','.join(self.spec.order),
            'action_count': len(self.state.completed_actions),
            'replan_count': self.state.replan_count,
            'planner_mode': self.planner.mode,
            'replanning_mode': self.replanning_mode,
            'allow_local_safe_plan': 'on' if self.allow_local_safe_plan else 'off',
            'model': self.planner.model,
            'ai_calls': sum(1 for call in calls if call.get('provider') == 'ollama'),
            'ai_time_s': sum(float(call.get('elapsed_s', 0.0)) for call in calls if call.get('provider') == 'ollama'),
            'elapsed_s': round(time.perf_counter() - started, 3),
        })
        self.summary = {
            'success': success,
            'allow_local_safe_plan': 'on' if self.allow_local_safe_plan else 'off',
            'failure_code': failure_code,
            'failure_reason': failure_reason,
            'completed_parts': list(self.state.completed_parts),
            'action_count': len(self.state.completed_actions),
            'replan_count': self.state.replan_count,
            'model': self.planner.model,
            'ai_calls': sum(1 for call in calls if call.get('provider') == 'ollama'),
            'ai_time_s': round(sum(float(call.get('elapsed_s', 0.0)) for call in calls if call.get('provider') == 'ollama'), 3),
            'elapsed_s': round(time.perf_counter() - started, 3),
        }

    def _run(self):
        self.logger.event('start',{'task':self.spec.__dict__}); feedback=None
        last_signature=None; identical_failures=0
        part_index=0
        while part_index < len(self.spec.order):
            part = self.spec.order[part_index]
            print(f"Preparing {part}...", flush=True)
            if self.state.snapshot.parts[part].status==PartStatus.PLACED:
                part_index+=1; continue
            if not self.state.snapshot.parts[part].available:
                failure=Failure('OBJECT_MISSING',f'{part} is unavailable',affected_object=part)
                self.state.record_failure(failure)
                self.logger.event('result',{'success':False,'reason':failure.reason,'completed':self.state.completed_parts})
                self.logger.write_csv()
                return self.state
            plan=self._plan(feedback)
            sequence = " -> ".join(
                f"{action.skill}({action.args})" for action in plan.actions
            )
            print(f"Plan {plan.plan_id} ready for {part}: {sequence}", flush=True)
            gate=f'{self.spec.task_id}:relocate:{part}'
            if self.disturbance_mode != 'none' and not self.gates.get(gate):
                print(f"Gate: move {part} at Clearance", flush=True)
                self.state.event_once(gate); self.gates[gate]=True
                if self.disturbance_mode == 'manual':
                    # Manual disturbances require the operator to see the
                    # robot safely parked before moving the part.
                    self.adapter.move_to('Clearance')
                # Automatic relocation is a simulator-side fixture. Do not
                # spend motion time parking the robot before changing the
                # object pose; the next validated plan handles the robot path.
                relocation = self.adapter.relocate(part,self.disturbance_mode)
                print(f"Gate complete: {part} relocated to {relocation}", flush=True)
                fresh,_=self.observer.read(); self.state.apply_snapshot(fresh); feedback=Failure('OBJECT_MOVED',f'{part} moved at relocation gate',gate,part,plan.plan_id); self.state.record_failure(feedback); self.state.replan_count+=1; self.logger.event('relocation',{'event_id':gate,'part':part,'pose':relocation}); plan=self._plan(feedback)
            if self.scenario=='unreachable-c' and part=='Part_C' and hasattr(self.adapter,'mark_unreachable'):
                self.adapter.mark_unreachable(part)
            errors=self.validator.validate(plan,self.state.snapshot)
            if errors:
                failure=Failure('INVALID_PLAN','; '.join(errors),failed_plan_id=plan.plan_id)
                self.state.record_failure(failure)
                self.logger.event('result',{'success':False,'reason':failure.reason,'completed':self.state.completed_parts})
                self.logger.write_csv()
                return self.state
            errors=self.preflight.check(plan,self.state.snapshot)
            if errors:
                code='UNREACHABLE' if any('unreachable' in error for error in errors) else 'PATH_BLOCKED'
                failure=Failure(code,'; '.join(errors),failed_plan_id=plan.plan_id)
                self.state.record_failure(failure)
                self.logger.event('result',{'success':False,'reason':failure.reason,'completed':self.state.completed_parts})
                self.logger.write_csv()
                return self.state
            execute_plan=self._prefix(plan,part)
            print(f"Executing {part}...", flush=True)
            try:
                self.executor.execute(execute_plan,self.state)
            except ExecutionFailure as exc:
                # A collision (or other dispatch failure) during execution
                # does not abort the run: cancel the remaining candidate,
                # re-observe the actual state (including a part that is
                # now held mid-cycle), and request a fresh plan for the
                # same remaining work -- the recovery loop the rest of this
                # method already uses for a relocation gate.
                fresh,_=self.observer.read(); self.state.apply_snapshot(fresh)
                failure=Failure(exc.code,exc.reason,failed_plan_id=plan.plan_id,affected_object=part)
                self.state.record_failure(failure)
                self.logger.event('execution_failure',{'code':exc.code,'reason':exc.reason,'part':part,'plan_id':plan.plan_id,'action':exc.action_skill})
                signature=(exc.code,part,exc.reason)
                identical_failures=identical_failures+1 if signature==last_signature else 1
                last_signature=signature
                # The same collision on an unchanged scene cannot be fixed by
                # replanning the same actions; stop instead of burning every
                # replan, and keep the colliding items in the result.
                repeated_collision=exc.code=='TABLE_COLLISION' and identical_failures>=MAX_IDENTICAL_COLLISIONS
                if repeated_collision or self.state.replan_count>=self.max_replans:
                    self.logger.event('result',{'success':False,'reason':failure.reason,'completed':self.state.completed_parts})
                    self.logger.write_csv()
                    return self.state
                self.state.replan_count+=1
                feedback=failure
                print(f"Recovering from {exc.code} on {part}: {exc.reason}", flush=True)
                continue
            print(f"Verified execution: {part}", flush=True)
            fresh,_=self.observer.read()
            self.state.apply_snapshot(fresh)
            self.state.mark_completed(part)
            feedback=None
            part_index+=1
            if self.state.replan_count>self.max_replans: raise RuntimeError('RETRY_LIMIT')
        self.logger.event('result',{'success':self.state.verify(),'completed':self.state.completed_parts}); self.logger.write_csv(); return self.state
    def _prefix(self,plan,part):
        out=[]; placed=False
        for action in plan.actions:
            out.append(action)
            if action.skill=='place' and action.args.get('object_id')==part: placed=True; break
        if not placed: raise RuntimeError('INVALID_PLAN: current part missing from candidate')
        out.append(Action('move_to',{'target_id':'Clearance'})); return Plan(plan.task_id,plan.based_on_snapshot,plan.disposition,plan.reason,out,plan.plan_id)
    def _plan(self,feedback):
        if self.state.replan_count>self.max_replans: raise RuntimeError('RETRY_LIMIT')
        feedback_data=feedback.__dict__ if feedback else None
        # Hybrid-fast uses the LLM once for the run-level strategy. Every
        # later part and every relocation feedback event is handled locally
        # from the fresh snapshot, so the robot does not wait for another
        # model process and cannot fail on a transient model response.
        if self.llm_unavailable or (
            self.replanning_mode == 'hybrid-fast' and self.planner.calls
        ):
            plan=self.planner.fast_replan(self.spec,self.state,feedback_data)
        else:
            try:
                print(
                    f"Planning {next((p for p in self.spec.order if p not in self.state.completed_parts), 'task')} "
                    f"with {self.planner.model or 'the default Ollama model'}...",
                    flush=True,
                )
                plan=self.planner.plan(self.spec,self.state,feedback_data)
            except RuntimeError as exc:
                if self.planner.mode != 'llm':
                    raise
                detail = str(exc)
                timed_out = "exceeded the" in detail and "planning timeout" in detail
                if not self.allow_local_safe_plan:
                    # The fallback is switched off: the model's failure is the
                    # trial's result, not something to paper over locally.
                    print(
                        "Ollama response timed out" if timed_out
                        else f"Ollama plan unavailable ({detail})",
                        "and the local safe plan is off; marking this trial as failed.",
                        flush=True,
                    )
                    raise LocalSafePlanDisabled(
                        'LLM_TIMEOUT' if timed_out else 'LLM_UNAVAILABLE',
                        detail,
                    ) from exc
                # Never leave the robot idle because a model response timed
                # out or was malformed. The local planner is allowlisted and
                # produces the same safe action sequence used by deterministic
                # mode. A timeout is reported as a continuation status rather
                # than an experiment error because execution can proceed.
                self.llm_unavailable = True
                if timed_out:
                    print(
                        "Ollama response was slow; continuing immediately "
                        "with the local safe plan.",
                        flush=True,
                    )
                else:
                    print(
                        f"Ollama plan unavailable ({detail}); continuing "
                        "with the local safe plan.",
                        flush=True,
                    )
                plan=self.planner.fast_replan(self.spec,self.state,feedback_data)
        plan.plan_id=f'plan-{len(self.planner.calls):03d}'; self.state.active_plan_id=plan.plan_id; self.logger.event('plan',{'plan':plan.to_dict(),'feedback':feedback_data}); return plan
