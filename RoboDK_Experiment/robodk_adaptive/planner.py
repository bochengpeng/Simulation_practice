from __future__ import annotations
from .models import *
from .skills import SkillRegistry
import json, os, socket, threading, time, urllib.error, urllib.request

DEFAULT_OLLAMA_HOST = 'http://localhost:11434'
# A local model has to load and then generate a whole plan on this machine,
# which takes a minute or more on CPU; override with OLLAMA_TIMEOUT_S.
DEFAULT_TIMEOUT_S = 180.0
MAX_PLAN_TOKENS = 2048
# The plan request and the background preload must use the same context size and
# keep-alive, or Ollama reloads the model for the real request.
NUM_CTX = 4096
KEEP_ALIVE = '10m'
# The model is not asked for task_id / based_on_snapshot: copying a random id
# is error-prone for small models (they returned "scene-9c0:211e" for
# "scene-9c0a211e"), so Planner.plan fills both in from the snapshot the
# prompt was built from. PlanValidator therefore still catches a scene that
# changed while the model was thinking, but not the model's typos.
# Ollama constrains the model's output to this schema, so a reply is always
# well-formed JSON of the right shape. Unknown skills and wrong argument counts
# are rejected when the arrays are expanded, and PlanValidator still checks the
# plan's content (ids, order, destinations) afterwards.
PLAN_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['disposition', 'actions'],
    'properties': {
        'disposition': {'type': 'string', 'enum': [d.value for d in Disposition]},
        # Each action is [skill, arg...], expanded to Action(skill, args) locally.
        # Ollama pretty-prints constrained JSON, so the object form costs about
        # twice as many reply tokens, and the reply length is the main latency.
        'actions': {
            'type': 'array',
            'items': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 2, 'maxItems': 3},
        },
    },
}
ACTION_ARGS = {
    'inspect': ('object_id',),
    'pick': ('object_id',),
    'place': ('object_id', 'slot_id'),
    'move_to': ('target_id',),
}
class Planner:
    def __init__(self,mode='deterministic',registry=None,model=None):
        self.mode=mode; self.registry=registry or SkillRegistry(); self.model=model or os.environ.get('OLLAMA_MODEL','').strip(); self.calls=[]

    @staticmethod
    def _ollama_url(path=''):
        host = os.environ.get('OLLAMA_HOST', '').strip() or DEFAULT_OLLAMA_HOST
        if '://' not in host:
            host = 'http://' + host
        # OLLAMA_HOST=0.0.0.0 is a bind address for the server, not a target.
        return host.replace('//0.0.0.0', '//127.0.0.1').rstrip('/') + path

    @classmethod
    def _ollama_request(cls, path, body=None, timeout=10.0):
        data = None if body is None else json.dumps(body).encode('utf-8')
        request = urllib.request.Request(
            cls._ollama_url(path), data=data,
            headers={'Content-Type': 'application/json'},
        )
        timeout_error = TimeoutError(f'Ollama exceeded the {timeout:g}s planning timeout')
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            try:
                detail = json.loads(detail).get('error', detail)
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f'Ollama returned HTTP {exc.code}: {detail}') from exc
        except (socket.timeout, TimeoutError) as exc:
            raise timeout_error from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise timeout_error from exc
            raise RuntimeError(
                f'Ollama is not reachable at {cls._ollama_url()} ({exc.reason}); '
                'start it with "ollama serve"'
            ) from exc

    @classmethod
    def _installed_models(cls):
        tags = cls._ollama_request('/api/tags', timeout=10.0)
        return {m['name']: m for m in tags.get('models', []) if m.get('name')}

    @classmethod
    def preload(cls, model=None):
        """Load the model into memory in the background so the first plan does
        not also pay for loading it. Returns the started thread."""
        def load():
            try:
                installed = cls._installed_models()
                name = model or os.environ.get('OLLAMA_MODEL', '').strip() or next(iter(installed), '')
                if name:
                    cls._ollama_request(
                        '/api/generate',
                        {'model': name, 'prompt': '', 'stream': False,
                         'keep_alive': KEEP_ALIVE, 'options': {'num_ctx': NUM_CTX}},
                        timeout=float(os.environ.get('OLLAMA_TIMEOUT_S', DEFAULT_TIMEOUT_S)),
                    )
            except Exception:
                # Only an optimisation: a real problem (Ollama down, model
                # missing) is reported when the plan itself is requested.
                pass
        thread = threading.Thread(target=load, daemon=True)
        thread.start()
        return thread

    @classmethod
    def available_models(cls):
        try:
            return list(cls._installed_models())
        except (RuntimeError, OSError, ValueError):
            return []
    def build_prompt(self,spec,state,feedback=None):
        current = next(
            (part for part in spec.order if part not in state.completed_parts),
            None,
        )
        snapshot = state.snapshot
        example_actions = []
        if current is not None:
            example_actions = [
                ['inspect', current],
                ['pick', current],
                ['place', current, spec.destinations[current]],
                ['move_to', 'Clearance'],
            ]
        # Static content first so Ollama can reuse its cached prompt prefix.
        # No coordinates: the plan never uses them and they cost prompt tokens.
        prompt={
        'instruction': (
            'Return only one valid JSON plan, with no markdown or explanation. '
            'Plan ONLY current_part, not the other parts: '
            'inspect, pick, place into destination_slot, then move_to '
            'Clearance, as in example_actions. Use disposition "execute" '
            'whenever you return actions, including when replanning after a '
            'failure or a moved object; "stop" and "clarify" require an empty '
            'actions list. Write each action as an array exactly in the form '
            'given in action_schema, with no coordinates: the robot generates '
            'all motion itself. If scene.held_object is '
            'current_part, return only place and move_to Clearance.'
        ),
            'action_schema': {
                'inspect': ['inspect', 'current_part'],
                'pick': ['pick', 'current_part'],
                'place': ['place', 'current_part', 'destination_slot'],
                'move_to': ['move_to', 'Clearance or Home'],
            },
            'current_part': current,
            'destination_slot': spec.destinations.get(current),
            'scene': {
                'held_object': snapshot.robot.held_object,
                'parts': {
                    pid: {'status': part.status.value, 'available': part.available}
                    for pid, part in snapshot.parts.items()
                },
                'slots': {sid: {'occupant': slot.occupant} for sid, slot in snapshot.slots.items()},
            },
            'failure': {k: feedback.get(k) for k in ('code', 'reason', 'affected_object')} if feedback else None,
            'example_actions': example_actions,
        }
        return prompt
    def plan(self,spec,state,feedback=None):
        prompt=self.build_prompt(spec,state,feedback)
        if self.mode=='llm':
            timeout=float(os.environ.get('OLLAMA_TIMEOUT_S',DEFAULT_TIMEOUT_S))
            task_id, snapshot_id = spec.task_id, state.snapshot.snapshot_id
            try:
                installed = self._installed_models()
                model = self.model or next(iter(installed), '')
                if not model:
                    raise RuntimeError(
                        'no Ollama models are installed; run "ollama pull qwen3:4b"'
                    )
                self.model = model
                info = installed.get(model) or installed.get(f'{model}:latest') or {}
                body = {
                    'model': model,
                    'stream': False,
                    'format': PLAN_SCHEMA,
                    'keep_alive': KEEP_ALIVE,
                    'options': {'temperature': 0, 'num_ctx': NUM_CTX, 'num_predict': MAX_PLAN_TOKENS},
                    'messages': [
                        {'role': 'system', 'content': prompt['instruction']},
                        {'role': 'user', 'content': json.dumps({k: v for k, v in prompt.items() if k != 'instruction'})},
                    ],
                }
                if 'thinking' in (info.get('capabilities') or []):
                    # Thinking models spend minutes reasoning first; the
                    # schema-constrained answer needs no chain of thought.
                    body['think'] = False
                started = time.perf_counter()
                reply = self._ollama_request('/api/chat', body, timeout)
                elapsed = time.perf_counter() - started
                if reply.get('done_reason') == 'length':
                    raise ValueError(f'the response was cut off at {MAX_PLAN_TOKENS} tokens')
                data=self._response_data((reply.get('message') or {}).get('content', ''))
                required={'disposition','actions'}
                unknown=set(data)-required-{'reason','plan_id','task_id','based_on_snapshot'}
                if unknown: raise ValueError(f'unknown plan fields: {sorted(unknown)}')
                if not isinstance(data['actions'], list): raise ValueError('actions must be a list')
                actions=[self._expand_action(x) for x in data['actions']]
                plan=Plan(task_id=task_id,based_on_snapshot=snapshot_id,disposition=Disposition(data['disposition']),reason=data.get('reason',''),actions=actions)
                self.calls.append({'provider':'ollama','model':self.model,'prompt':prompt,'response':data,'elapsed_s':elapsed}); return plan
            except Exception as exc: raise RuntimeError(f'LLM_UNAVAILABLE: Ollama request failed: {exc}') from exc
        actions=self.registry.compile(spec,state.snapshot)
        plan=Plan(spec.task_id,state.snapshot.snapshot_id,Disposition.EXECUTE,'Deterministic allowlisted plan',actions)
        self.calls.append({'provider':'deterministic','model':'','prompt':prompt,'response':plan.to_dict(),'elapsed_s':0.0}); return plan

    def fast_replan(self, spec, state, feedback=None):
        """Rebuild a safe plan locally after a known scene relocation."""
        prompt={'goal':spec.goal,'task':spec.__dict__,'snapshot':state.snapshot.to_dict(),'completed':state.completed_parts,'remaining':state.remaining_parts,'failure':feedback,'allowed_skills':sorted(self.registry.names)}
        actions=self.registry.compile(spec,state.snapshot)
        plan=Plan(
            spec.task_id,
            state.snapshot.snapshot_id,
            Disposition.EXECUTE,
            'Immediate deterministic replan after observed scene change',
            actions,
        )
        self.calls.append({'provider':'deterministic-fast-replan','model':'','prompt':prompt,'response':plan.to_dict(),'elapsed_s':0.0})
        return plan

    @staticmethod
    def _expand_action(raw):
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError('invalid action schema')
        skill, *values = raw
        fields = ACTION_ARGS.get(skill)
        if fields is None:
            raise ValueError(f'unknown skill in action {raw}')
        if len(values) != len(fields):
            raise ValueError(f'invalid action {raw}: {skill} takes {len(fields)} argument(s)')
        return Action(skill, dict(zip(fields, values)))

    @staticmethod
    def _response_data(output):
        if isinstance(output, bytes):
            raw = output.decode("utf-8", errors="replace").strip()
        elif isinstance(output, str):
            raw = output.strip()
        else:
            raise ValueError("Ollama returned an unsupported output type")
        if not raw:
            raise ValueError('Ollama returned no output')
        candidates=[]
        try:
            candidates.append(json.loads(raw))
        except json.JSONDecodeError:
            # Tolerate a plan wrapped in prose or a markdown fence.
            decoder = json.JSONDecoder()
            for index, character in enumerate(raw):
                if character != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(raw[index:])
                except json.JSONDecodeError:
                    continue
                candidates.append(value)
        for candidate in reversed(candidates):
            if isinstance(candidate, dict) and {'disposition','actions'} <= set(candidate):
                return candidate
        raise ValueError('Ollama output did not contain a valid JSON plan')
