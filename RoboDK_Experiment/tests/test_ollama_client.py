import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from RoboDK_Experiment.robodk_adaptive.models import *
from RoboDK_Experiment.robodk_adaptive.planner import KEEP_ALIVE, NUM_CTX, PLAN_SCHEMA, Planner
from RoboDK_Experiment.robodk_adaptive.task_state import TaskState
from RoboDK_Experiment.robodk_adaptive.validator import PlanValidator

TAGS = {
    "models": [
        {"name": "qwen3:4b", "capabilities": ["completion", "thinking"]},
        {"name": "llama3:latest", "capabilities": ["completion"]},
    ]
}


def fixture():
    parts = {"Part_A": Part("Part_A", Pose(600, 350, 50))}
    slots = {"Slot_1": Slot("Slot_1", Pose(150, -300, 60))}
    snapshot = SceneSnapshot("scene-1", 0, RobotState([], False, "tool", "frame"), parts, slots)
    spec = TaskSpec("task-A", ["Part_A"], ["Part_A"], {"Part_A": "Slot_1"})
    return spec, TaskState(spec, snapshot), snapshot


def good_plan(spec, snapshot):
    return {
        "disposition": "execute",
        "actions": [
            ["inspect", "Part_A"],
            ["pick", "Part_A"],
            ["place", "Part_A", "Slot_1"],
            ["move_to", "Clearance"],
        ],
    }


class FakeOllama:
    """A local HTTP server speaking just enough of Ollama's API."""

    def __init__(self, chat_reply=None, chat_status=200, chat_delay=0.0, tags=TAGS):
        self.chat_reply, self.chat_status, self.chat_delay = chat_reply, chat_status, chat_delay
        self.tags = tags
        self.chat_requests = []
        self.paths = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send(200, owner.tags)

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                owner.paths.append(self.path)
                owner.chat_requests.append(json.loads(self.rfile.read(length)))
                time.sleep(owner.chat_delay)
                try:
                    self._send(owner.chat_status, owner.chat_reply)
                except OSError:
                    pass  # the client gave up (timeout test)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def host(self):
        return f"127.0.0.1:{self.server.server_address[1]}"


def reply(content, **extra):
    return {"message": {"role": "assistant", "content": json.dumps(content)}, "done": True, **extra}


def test_available_models_lists_installed_ollama_models(monkeypatch):
    with FakeOllama() as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        assert Planner.available_models() == ["qwen3:4b", "llama3:latest"]


def test_available_models_is_empty_when_ollama_is_not_running(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:1")
    assert Planner.available_models() == []


def test_plan_comes_from_ollama_and_passes_the_validator(monkeypatch):
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply=reply(good_plan(spec, snapshot))) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        planner = Planner("llm", model="qwen3:4b")
        plan = planner.plan(spec, state)

    assert [a.skill for a in plan.actions] == ["inspect", "pick", "place", "move_to"]
    assert not PlanValidator(spec).validate(plan, snapshot)
    assert planner.calls[-1]["provider"] == "ollama"
    assert planner.calls[-1]["model"] == "qwen3:4b"
    request = server.chat_requests[0]
    assert request["model"] == "qwen3:4b"
    assert request["stream"] is False
    assert request["format"] == PLAN_SCHEMA  # output constrained to a plan
    assert request["think"] is False  # qwen3 is a thinking model
    assert request["options"]["temperature"] == 0
    user_prompt = json.loads(request["messages"][1]["content"])
    assert user_prompt["current_part"] == "Part_A" and user_prompt["destination_slot"] == "Slot_1"


def test_thinking_is_not_disabled_for_models_that_do_not_think(monkeypatch):
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply=reply(good_plan(spec, snapshot))) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        Planner("llm", model="llama3").plan(spec, state)  # resolves llama3:latest
    assert "think" not in server.chat_requests[0]


def test_model_defaults_to_ollama_model_env_then_first_installed(monkeypatch):
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply=reply(good_plan(spec, snapshot))) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        first = Planner("llm")
        first.plan(spec, state)
        monkeypatch.setenv("OLLAMA_MODEL", "llama3:latest")
        from_env = Planner("llm")
        from_env.plan(spec, state)
    assert first.model == "qwen3:4b"  # recorded, so the summary shows what ran
    assert from_env.model == "llama3:latest"


def test_unknown_model_reports_ollamas_own_error(monkeypatch):
    spec, state, _ = fixture()
    error = {"error": "model 'nope' not found, try pulling it first"}
    with FakeOllama(chat_reply=error, chat_status=404) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        with pytest.raises(RuntimeError, match="LLM_UNAVAILABLE.*not found, try pulling"):
            Planner("llm", model="nope").plan(spec, state)


def test_ollama_not_running_is_reported_clearly(monkeypatch):
    spec, state, _ = fixture()
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:1")
    with pytest.raises(RuntimeError, match="Ollama is not reachable.*ollama serve"):
        Planner("llm", model="qwen3:4b").plan(spec, state)


def test_slow_model_is_reported_as_a_planning_timeout(monkeypatch):
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply=reply(good_plan(spec, snapshot)), chat_delay=1.5) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        monkeypatch.setenv("OLLAMA_TIMEOUT_S", "0.3")
        # Experiment recognises this wording as LLM_TIMEOUT.
        with pytest.raises(RuntimeError, match="exceeded the 0.3s planning timeout"):
            Planner("llm", model="qwen3:4b").plan(spec, state)


def test_response_cut_off_at_the_token_limit_is_rejected(monkeypatch):
    spec, state, snapshot = fixture()
    truncated = reply(good_plan(spec, snapshot), done_reason="length")
    with FakeOllama(chat_reply=truncated) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        with pytest.raises(RuntimeError, match="cut off"):
            Planner("llm", model="qwen3:4b").plan(spec, state)


def test_non_plan_content_is_rejected(monkeypatch):
    spec, state, _ = fixture()
    with FakeOllama(chat_reply=reply({"hello": "world"})) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        with pytest.raises(RuntimeError, match="LLM_UNAVAILABLE"):
            Planner("llm", model="qwen3:4b").plan(spec, state)


def test_no_installed_models_gives_an_actionable_error(monkeypatch):
    spec, state, _ = fixture()
    with FakeOllama(tags={"models": []}) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        with pytest.raises(RuntimeError, match="ollama pull"):
            Planner("llm").plan(spec, state)


def test_preload_loads_the_model_with_the_same_settings_as_the_plan_request(monkeypatch):
    # A different num_ctx or keep_alive would make Ollama reload the model for
    # the real request, defeating the preload.
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply={"done": True}) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        Planner.preload("qwen3:4b").join(timeout=10)
        preload = server.chat_requests[0]
        server.chat_reply = reply(good_plan(spec, snapshot))
        Planner("llm", model="qwen3:4b").plan(spec, state)
        chat = server.chat_requests[1]
    assert server.paths == ["/api/generate", "/api/chat"]
    assert preload["model"] == chat["model"] == "qwen3:4b"
    assert preload["options"]["num_ctx"] == chat["options"]["num_ctx"] == NUM_CTX
    assert preload["keep_alive"] == chat["keep_alive"] == KEEP_ALIVE


def test_preload_defaults_to_the_first_installed_model(monkeypatch):
    with FakeOllama(chat_reply={"done": True}) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        Planner.preload().join(timeout=10)
    assert server.chat_requests[0]["model"] == "qwen3:4b"


def test_preload_never_raises_when_ollama_is_down(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:1")
    thread = Planner.preload("qwen3:4b")
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_array_actions_are_expanded_to_skills_and_named_arguments(monkeypatch):
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply=reply(good_plan(spec, snapshot))) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        plan = Planner("llm", model="qwen3:4b").plan(spec, state)
    assert [(a.skill, a.args) for a in plan.actions] == [
        ("inspect", {"object_id": "Part_A"}),
        ("pick", {"object_id": "Part_A"}),
        ("place", {"object_id": "Part_A", "slot_id": "Slot_1"}),
        ("move_to", {"target_id": "Clearance"}),
    ]


@pytest.mark.parametrize("bad_action", [
    ["fly", "Part_A"],                       # unknown skill
    ["place", "Part_A"],                     # missing slot
    ["pick", "Part_A", "Slot_1"],            # extra argument
    "pick Part_A",                           # not an array
])
def test_malformed_actions_are_rejected(monkeypatch, bad_action):
    spec, state, snapshot = fixture()
    plan = good_plan(spec, snapshot)
    plan["actions"][1] = bad_action
    with FakeOllama(chat_reply=reply(plan)) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        with pytest.raises(RuntimeError, match="LLM_UNAVAILABLE"):
            Planner("llm", model="qwen3:4b").plan(spec, state)


def test_action_arguments_cover_exactly_the_skill_registry():
    from RoboDK_Experiment.robodk_adaptive.planner import ACTION_ARGS
    from RoboDK_Experiment.robodk_adaptive.skills import SkillRegistry
    assert set(ACTION_ARGS) == SkillRegistry.names


def test_ids_come_from_the_snapshot_not_from_the_model(monkeypatch):
    # Regression: a model that garbles (or invents) the ids it echoes must not
    # make a correct plan fail validation as "stale snapshot".
    spec, state, snapshot = fixture()
    garbled = good_plan(spec, snapshot)
    garbled["task_id"] = "task-A:relocate:Part_A"
    garbled["based_on_snapshot"] = "scene-9c0:211e"
    for content in (good_plan(spec, snapshot), garbled):
        with FakeOllama(chat_reply=reply(content)) as server:
            monkeypatch.setenv("OLLAMA_HOST", server.host)
            plan = Planner("llm", model="qwen3:4b").plan(spec, state)
        assert plan.task_id == "task-A"
        assert plan.based_on_snapshot == "scene-1"
        assert not PlanValidator(spec).validate(plan, snapshot)


def test_a_scene_that_changed_while_the_model_was_thinking_is_still_stale(monkeypatch):
    spec, state, snapshot = fixture()
    with FakeOllama(chat_reply=reply(good_plan(spec, snapshot))) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.host)
        plan = Planner("llm", model="qwen3:4b").plan(spec, state)
    newer = SceneSnapshot("scene-2", 1, snapshot.robot, snapshot.parts, snapshot.slots)
    assert "stale snapshot" in PlanValidator(spec).validate(plan, newer)
