from __future__ import annotations
import argparse, json, shutil, re, sys
from pathlib import Path
from .models import TaskSpec
from .robodk_adapter import RoboDKAdapter
from .experiment import Experiment
from .planner import Planner
ROOT=Path(__file__).resolve().parents[1]; SOURCE=ROOT/'Doosan_M0609_SSM3_Original.rdk'; COPY=ROOT/'Doosan_M0609_SSM3_Experiment.rdk'
def make_spec(parts):
    parts=[f'Part_{p.upper()}' if len(p)==1 else p for p in parts]
    destinations={
        part: f"Slot_{'ABC'.index(part[-1]) + 1}"
        for part in parts
    }
    return TaskSpec(
        'task-'+''.join(p[-1] for p in parts),
        parts,
        parts,
        destinations,
        'Place requested parts in their assigned processing slots',
    )
def parts_from_task(text):
    found=[]
    for letter in 'ABC':
        if re.search(rf'\b(?:Part\s*)?{letter}\b',text,re.I): found.append(letter)
    if not found: raise ValueError('task must explicitly name Part A, Part B or Part C')
    return found
def adapter_for(copy=True):
    if copy and not COPY.exists(): shutil.copy2(SOURCE,COPY)
    return RoboDKAdapter(COPY if copy else SOURCE)
def cmd_doctor(args):
    adapter=adapter_for(False)
    try:
        print(json.dumps(adapter.doctor(),indent=2))
        return 0
    finally:
        adapter.close()
def cmd_reset(args):
    adapter=adapter_for(True)
    try:
        adapter.reset()
        print('Experiment station reset:',COPY)
        return 0
    finally:
        adapter.close()
def selected_parts(args): return args.parts or parts_from_task(args.task)
def preload_model(args):
    # Load the Ollama model while RoboDK is still opening/resetting the station.
    if args.mode=='llm': Planner.preload(args.model or None)
def cmd_run(args):
    preload_model(args)
    adapter=adapter_for(True)
    try:
        spec=make_spec(selected_parts(args))
        exp=Experiment(adapter,spec,args.mode,args.disturbance_mode,args.log_dir,replanning_mode=args.replanning_mode,model=args.model,allow_local_safe_plan=args.allow_local_safe_plan)
        state=exp.run()
        print(json.dumps({'success':state.verify(),'completed_parts':state.completed_parts,'replans':state.replan_count},indent=2))
        return 0
    finally:
        adapter.close()
def cmd_experiment(args):
    disturbance='none' if args.scenario=='no-change' else args.disturbance_mode
    preload_model(args)
    for trial in range(args.trials):
        adapter=adapter_for(True)
        try:
            adapter.reset()
            if args.scenario=='missing-b' and hasattr(adapter,'mark_missing'):
                adapter.mark_missing('Part_B')
            spec=make_spec(args.parts or ['A','B','C'])
            exp=Experiment(adapter,spec,args.mode,disturbance,args.log_dir,scenario=args.scenario,replanning_mode=args.replanning_mode,model=args.model,allow_local_safe_plan=args.allow_local_safe_plan)
            state=exp.run()
            summary = exp.summary
            print(
                f"trial={trial+1} scenario={args.scenario} "
                f"success={summary.get('success', state.verify())} "
                f"completed={','.join(summary.get('completed_parts', state.completed_parts))} "
                f"actions={summary.get('action_count', len(state.completed_actions))} "
                f"replans={summary.get('replan_count', state.replan_count)} "
                f"local_safe_plan={summary.get('allow_local_safe_plan', 'on')} "
                f"model={summary.get('model') or 'deterministic/default'} "
                f"ai_time_s={summary.get('ai_time_s', 0)} "
                f"elapsed_s={summary.get('elapsed_s', 0)} "
                f"error={summary.get('failure_code') or 'none'}",
                flush=True,
            )
        finally:
            adapter.close()
    return 0
def add_common(p):
    p.add_argument('--parts',nargs='+',choices=['A','B','C']); p.add_argument('--task',default='Pick up Part A, then Part B, then Part C and place them in the processing area',help='Explicit task text naming the requested parts'); p.add_argument('--mode',choices=['deterministic','llm'],default='deterministic'); p.add_argument('--model',default='',help='Ollama model for this run, for example qwen3:4b (default: OLLAMA_MODEL, else the first installed model)'); p.add_argument('--replanning-mode',choices=['hybrid-fast','full-llm'],default='hybrid-fast'); p.add_argument('--disturbance-mode',choices=['automatic','manual'],default='automatic'); p.add_argument('--log-dir',default='logs'); p.add_argument('--allow-local-safe-plan',action=argparse.BooleanOptionalAction,default=True,help='When the Ollama planner times out or fails, continue with the local safe plan (default). With --no-allow-local-safe-plan the trial is marked as failed instead.')
def main():
    parser=argparse.ArgumentParser(description='Adaptive RoboDK multi-part experiment (simulation only)'); sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('doctor'); p.set_defaults(func=cmd_doctor)
    p=sub.add_parser('reset'); p.set_defaults(func=cmd_reset)
    p=sub.add_parser('run'); add_common(p); p.set_defaults(func=cmd_run)
    p=sub.add_parser('experiment'); add_common(p); p.add_argument('--scenario',choices=['sequential-relocation','missing-b','unreachable-c','no-change'],default='sequential-relocation'); p.add_argument('--trials',type=int,default=1); p.set_defaults(func=cmd_experiment)
    args=parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
if __name__=='__main__': raise SystemExit(main())
