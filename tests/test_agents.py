"""
Tests for terra4mice Phase 5.1: Agent Backends & Auto/Hybrid/Market Modes.

Tests cover:
- AgentResult dataclass
- PromptBuilder (header, deps, files, context, instructions)
- SubprocessAgent execution (success, failure, timeout)
- CallableAgent
- Agent registry (get_agent, register_agent, list_agents)
- AutoMode with mock agent (success, failure, verification)
- HybridMode with mock agent + input (accept, reject, edit, skip)
- MarketMode (task building, posting)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from terra4mice.models import (
    PlanAction,
    Resource,
    ResourceStatus,
    Spec,
    State,
)
from terra4mice.state_manager import StateManager
from terra4mice.contexts import ContextRegistry
from terra4mice.apply.runner import ApplyConfig, ApplyResult
from terra4mice.apply.agents import (
    AgentBackend,
    AgentResult,
    PromptBuilder,
    SubprocessAgent,
    ClaudeCodeAgent,
    CodexAgent,
    CallableAgent,
    get_agent,
    register_agent,
    list_agents,
)
from terra4mice.apply.modes import AutoMode, HybridMode, MarketMode


# ── Helpers ───────────────────────────────────────────────────────────


def _make_resource(
    rtype: str = "feature",
    name: str = "auth",
    status: ResourceStatus = ResourceStatus.MISSING,
    depends_on: list[str] | None = None,
    files: list[str] | None = None,
    attributes: dict | None = None,
) -> Resource:
    return Resource(
        type=rtype,
        name=name,
        status=status,
        depends_on=depends_on or [],
        files=files or [],
        attributes=attributes or {},
    )


def _make_action(
    rtype: str = "feature",
    name: str = "auth",
    action: str = "create",
    reason: str = "",
    **kwargs,
) -> PlanAction:
    return PlanAction(
        action=action,
        resource=_make_resource(rtype, name, **kwargs),
        reason=reason,
    )


class FakeStateManager:
    """Minimal StateManager mock for testing."""

    def __init__(self, state: State | None = None):
        self.state = state or State()
        self._saved = False

    def mark_created(self, address: str, files: list[str] | None = None):
        res = self.state.get(address)
        if res is None:
            parts = address.split(".", 1)
            res = Resource(type=parts[0], name=parts[1] if len(parts) > 1 else "")
        res.status = ResourceStatus.IMPLEMENTED
        if files:
            res.files = files
        self.state.set(res)

    def mark_partial(self, address: str, reason: str = ""):
        res = self.state.get(address)
        if res is None:
            parts = address.split(".", 1)
            res = Resource(type=parts[0], name=parts[1] if len(parts) > 1 else "")
        res.status = ResourceStatus.PARTIAL
        self.state.set(res)

    def save(self):
        self._saved = True


def _success_agent(prompt, root, timeout):
    """Agent that always succeeds."""
    return AgentResult(
        success=True,
        output="Done",
        files_created=["src/auth.py"],
        duration_seconds=1.5,
        exit_code=0,
    )


def _fail_agent(prompt, root, timeout):
    """Agent that always fails."""
    return AgentResult(
        success=False,
        error="Syntax error",
        duration_seconds=0.5,
        exit_code=1,
    )


# ══════════════════════════════════════════════════════════════════════
# AgentResult Tests
# ══════════════════════════════════════════════════════════════════════


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult()
        assert r.success is False
        assert r.all_files == []
        assert r.exit_code == -1

    def test_all_files_combines_created_and_modified(self):
        r = AgentResult(
            files_created=["a.py", "b.py"],
            files_modified=["c.py"],
        )
        assert r.all_files == ["a.py", "b.py", "c.py"]

    def test_success_result(self):
        r = AgentResult(
            success=True,
            output="Implemented auth",
            files_created=["auth.py"],
            duration_seconds=2.0,
            exit_code=0,
        )
        assert r.success
        assert r.exit_code == 0
        assert r.duration_seconds == 2.0


# ══════════════════════════════════════════════════════════════════════
# PromptBuilder Tests
# ══════════════════════════════════════════════════════════════════════


class TestPromptBuilder:
    def test_basic_prompt_has_header(self):
        builder = PromptBuilder(project_root="/tmp/test")
        action = _make_action(reason="Missing from state")
        prompt = builder.build(action)
        assert "CREATE" in prompt
        assert "feature.auth" in prompt
        assert "/tmp/test" in prompt

    def test_prompt_includes_attributes(self):
        builder = PromptBuilder(project_root="/tmp/test")
        action = _make_action(
            attributes={"language": "python", "framework": "fastapi"}
        )
        prompt = builder.build(action)
        assert "python" in prompt
        assert "fastapi" in prompt

    def test_prompt_includes_dependencies(self):
        state = State()
        state.set(_make_resource("module", "db", ResourceStatus.IMPLEMENTED, files=["db.py"]))
        sm = FakeStateManager(state)
        builder = PromptBuilder(
            project_root="/tmp/test", state_manager=sm
        )
        action = _make_action(depends_on=["module.db"])
        prompt = builder.build(action)
        assert "module.db" in prompt
        assert "implemented" in prompt
        assert "db.py" in prompt

    def test_prompt_includes_suggested_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an existing file
            Path(tmpdir, "existing.py").write_text("# existing")
            builder = PromptBuilder(project_root=tmpdir)
            action = _make_action(files=["existing.py", "new.py"])
            prompt = builder.build(action)
            assert "existing.py" in prompt
            assert "update" in prompt.lower() or "exists" in prompt.lower()
            assert "new.py" in prompt
            assert "create" in prompt.lower() or "does not exist" in prompt.lower()

    def test_prompt_create_vs_update_instructions(self):
        builder = PromptBuilder(project_root="/tmp/test")
        create_action = _make_action(action="create")
        update_action = _make_action(action="update")
        delete_action = _make_action(action="delete")

        assert "from scratch" in builder.build(create_action)
        assert "Update" in builder.build(update_action)
        assert "Remove" in builder.build(delete_action)

    def test_prompt_includes_reason(self):
        builder = PromptBuilder(project_root="/tmp/test")
        action = _make_action(reason="User requested login feature")
        prompt = builder.build(action)
        assert "User requested login feature" in prompt


# ══════════════════════════════════════════════════════════════════════
# SubprocessAgent Tests
# ══════════════════════════════════════════════════════════════════════


class TestSubprocessAgent:
    def test_echo_agent_succeeds(self):
        """Agent wrapping 'cat' just echoes the prompt."""
        agent = SubprocessAgent(command="cat", name="echo-test")
        result = agent.execute(
            prompt="hello world",
            project_root="/tmp",
            timeout_seconds=5,
        )
        assert result.success
        assert result.output.strip() == "hello world"
        assert result.exit_code == 0
        assert result.duration_seconds > 0

    def test_false_agent_fails(self):
        """Agent wrapping 'false' always exits non-zero."""
        agent = SubprocessAgent(command="false", name="fail-test")
        result = agent.execute(
            prompt="",
            project_root="/tmp",
            timeout_seconds=5,
        )
        assert not result.success
        assert result.exit_code != 0

    def test_missing_command_returns_error(self):
        agent = SubprocessAgent(
            command="nonexistent_command_12345", name="missing"
        )
        result = agent.execute(
            prompt="", project_root="/tmp", timeout_seconds=5
        )
        assert not result.success
        assert "not found" in result.error.lower()

    def test_timeout_returns_error(self):
        agent = SubprocessAgent(
            command="sleep", args=["10"], name="slow"
        )
        result = agent.execute(
            prompt="", project_root="/tmp", timeout_seconds=1
        )
        assert not result.success
        assert "timed out" in result.error.lower()

    def test_is_available_for_real_command(self):
        agent = SubprocessAgent(command="cat")
        assert agent.is_available()

    def test_is_not_available_for_missing_command(self):
        agent = SubprocessAgent(command="nonexistent_xyz_123")
        assert not agent.is_available()


# ══════════════════════════════════════════════════════════════════════
# ClaudeCodeAgent & CodexAgent Tests
# ══════════════════════════════════════════════════════════════════════


class TestSpecializedAgents:
    def test_claude_code_agent_name(self):
        agent = ClaudeCodeAgent()
        assert agent.name == "claude-code"
        assert "claude" in agent.command

    def test_codex_agent_name(self):
        agent = CodexAgent()
        assert agent.name == "codex"
        assert "codex" in agent.command


# ══════════════════════════════════════════════════════════════════════
# CallableAgent Tests
# ══════════════════════════════════════════════════════════════════════


class TestCallableAgent:
    def test_callable_success(self):
        agent = CallableAgent(fn=_success_agent, name="test-ok")
        result = agent.execute("do stuff", Path("/tmp"), 60)
        assert result.success
        assert result.files_created == ["src/auth.py"]

    def test_callable_failure(self):
        agent = CallableAgent(fn=_fail_agent, name="test-fail")
        result = agent.execute("do stuff", Path("/tmp"), 60)
        assert not result.success
        assert "Syntax error" in result.error

    def test_callable_name(self):
        agent = CallableAgent(fn=_success_agent, name="my-agent")
        assert agent.name == "my-agent"


# ══════════════════════════════════════════════════════════════════════
# Agent Registry Tests
# ══════════════════════════════════════════════════════════════════════


class TestAgentRegistry:
    def test_list_agents_includes_known(self):
        agents = list_agents()
        assert "claude-code" in agents
        assert "codex" in agents

    def test_get_known_agent(self):
        agent = get_agent("claude-code")
        assert agent.name == "claude-code"

    def test_get_unknown_agent_raises(self):
        with pytest.raises(ValueError, match="Unknown agent"):
            get_agent("nonexistent_agent_xyz")

    def test_register_custom_agent(self):
        class CustomAgent(AgentBackend):
            name = "custom"

            def execute(self, prompt, project_root, timeout_seconds=300):
                return AgentResult(success=True)

        register_agent("custom-test", CustomAgent)
        agent = get_agent("custom-test")
        assert isinstance(agent, CustomAgent)
        result = agent.execute("test", Path("/tmp"))
        assert result.success


# ══════════════════════════════════════════════════════════════════════
# AutoMode Tests
# ══════════════════════════════════════════════════════════════════════


class TestAutoMode:
    def test_auto_mode_success(self):
        """Agent succeeds → resource marked as implemented."""
        sm = FakeStateManager()
        agent = CallableAgent(fn=_success_agent)
        mode = AutoMode(
            state_manager=sm,
            config=ApplyConfig(mode="auto"),
            agent=agent,
            project_root="/tmp",
        )
        actions = [_make_action()]
        result = mode.execute(actions)
        assert "feature.auth" in result.implemented
        assert sm._saved

    def test_auto_mode_failure(self):
        """Agent fails → resource in failed list."""
        sm = FakeStateManager()
        agent = CallableAgent(fn=_fail_agent)
        mode = AutoMode(
            state_manager=sm,
            config=ApplyConfig(mode="auto"),
            agent=agent,
            project_root="/tmp",
        )
        actions = [_make_action()]
        result = mode.execute(actions)
        assert "feature.auth" in result.failed
        assert len(result.implemented) == 0

    def test_auto_mode_multiple_actions(self):
        """Multiple actions processed in order."""
        sm = FakeStateManager()
        agent = CallableAgent(fn=_success_agent)
        mode = AutoMode(
            state_manager=sm,
            config=ApplyConfig(mode="auto"),
            agent=agent,
            project_root="/tmp",
        )
        actions = [
            _make_action(name="login"),
            _make_action(name="logout"),
            _make_action(name="register"),
        ]
        result = mode.execute(actions)
        assert len(result.implemented) == 3

    def test_auto_mode_with_verification(self):
        """Verification passes when files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Agent "creates" the file
            filepath = Path(tmpdir, "auth.py")
            filepath.write_text("def login(): pass")

            def agent_fn(prompt, root, timeout):
                return AgentResult(
                    success=True,
                    files_created=["auth.py"],
                    duration_seconds=1.0,
                    exit_code=0,
                )

            sm = FakeStateManager()
            agent = CallableAgent(fn=agent_fn)
            mode = AutoMode(
                state_manager=sm,
                config=ApplyConfig(mode="auto"),
                agent=agent,
                project_root=tmpdir,
            )
            action = _make_action(files=["auth.py"])
            result = mode.execute([action])
            assert "feature.auth" in result.implemented

    def test_auto_mode_partial_verification(self):
        """Verification fails → marked as partial."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # DON'T create the expected file

            def agent_fn(prompt, root, timeout):
                return AgentResult(
                    success=True,
                    files_created=[],
                    duration_seconds=1.0,
                    exit_code=0,
                )

            sm = FakeStateManager()
            agent = CallableAgent(fn=agent_fn)
            mode = AutoMode(
                state_manager=sm,
                config=ApplyConfig(mode="auto"),
                agent=agent,
                project_root=tmpdir,
            )
            # Resource expects a file that doesn't exist
            action = _make_action(files=["missing.py"])
            result = mode.execute([action])
            # Should still be in implemented (partial)
            assert "feature.auth" in result.implemented
            # State should be partial
            res = sm.state.get("feature.auth")
            assert res is not None
            assert res.status == ResourceStatus.PARTIAL

    def test_auto_mode_lazy_agent_init(self):
        """Agent is lazily initialized from config."""
        sm = FakeStateManager()
        mode = AutoMode(
            state_manager=sm,
            config=ApplyConfig(mode="auto", agent="claude-code"),
            project_root="/tmp",
        )
        # Accessing .agent should create it
        assert mode.agent.name == "claude-code"

    def test_auto_mode_context_tracking(self):
        """Context registry updated after agent work."""
        sm = FakeStateManager()
        cr = ContextRegistry()
        agent = CallableAgent(fn=_success_agent)
        mode = AutoMode(
            state_manager=sm,
            context_registry=cr,
            config=ApplyConfig(mode="auto"),
            agent=agent,
            project_root="/tmp",
        )
        actions = [_make_action()]
        mode.execute(actions)
        # Check context was registered
        contexts = cr.get_resource_contexts("feature.auth")
        assert len(contexts) > 0
        assert contexts[0].agent == "callable"


# ══════════════════════════════════════════════════════════════════════
# HybridMode Tests
# ══════════════════════════════════════════════════════════════════════


class TestHybridMode:
    def _make_hybrid(
        self,
        sm=None,
        agent_fn=_success_agent,
        inputs=None,
    ):
        sm = sm or FakeStateManager()
        agent = CallableAgent(fn=agent_fn)
        mode = HybridMode(
            state_manager=sm,
            config=ApplyConfig(mode="hybrid"),
            agent=agent,
            project_root="/tmp",
        )
        if inputs:
            input_iter = iter(inputs)
            mode._input_fn = lambda prompt="": next(input_iter)
        return mode, sm

    def test_accept(self):
        """Human accepts AI implementation."""
        mode, sm = self._make_hybrid(inputs=["a"])
        result = mode.execute([_make_action()])
        assert "feature.auth" in result.implemented

    def test_reject(self):
        """Human rejects AI implementation."""
        mode, sm = self._make_hybrid(inputs=["r"])
        result = mode.execute([_make_action()])
        assert "feature.auth" in result.failed

    def test_edit_marks_partial(self):
        """Human accepts with edits → partial status."""
        mode, sm = self._make_hybrid(inputs=["e"])
        result = mode.execute([_make_action()])
        assert "feature.auth" in result.implemented
        res = sm.state.get("feature.auth")
        assert res.status == ResourceStatus.PARTIAL

    def test_skip(self):
        """Human skips."""
        mode, sm = self._make_hybrid(inputs=["s"])
        result = mode.execute([_make_action()])
        assert "feature.auth" in result.skipped

    def test_quit_stops_processing(self):
        """Human quits → remaining actions not processed."""
        mode, sm = self._make_hybrid(inputs=["q"])
        actions = [_make_action(name="a"), _make_action(name="b")]
        result = mode.execute(actions)
        assert result.total <= 1  # Only first action processed

    def test_agent_failure_skip(self):
        """Agent fails, human chooses skip."""
        mode, sm = self._make_hybrid(
            agent_fn=_fail_agent, inputs=["s"]
        )
        result = mode.execute([_make_action()])
        assert "feature.auth" in result.skipped

    def test_agent_failure_manual(self):
        """Agent fails, human falls back to manual."""
        mode, sm = self._make_hybrid(
            agent_fn=_fail_agent, inputs=["m", "auth.py"]
        )
        result = mode.execute([_make_action()])
        assert "feature.auth" in result.implemented

    def test_multiple_actions_mixed_decisions(self):
        """Multiple actions with different human decisions."""
        mode, sm = self._make_hybrid(inputs=["a", "r", "s"])
        actions = [
            _make_action(name="login"),
            _make_action(name="logout"),
            _make_action(name="register"),
        ]
        result = mode.execute(actions)
        assert "feature.login" in result.implemented
        assert "feature.logout" in result.failed
        assert "feature.register" in result.skipped


# ══════════════════════════════════════════════════════════════════════
# MarketMode Tests
# ══════════════════════════════════════════════════════════════════════


class TestMarketMode:
    def test_market_posts_tasks(self):
        sm = FakeStateManager()
        mode = MarketMode(
            state_manager=sm,
            config=ApplyConfig(mode="market"),
            project_root="/tmp",
        )
        actions = [_make_action(), _make_action(name="logout")]
        result = mode.execute(actions)
        assert len(result.market_pending) == 2
        assert "feature.auth" in result.market_pending
        assert "feature.logout" in result.market_pending

    def test_market_task_structure(self):
        sm = FakeStateManager()
        mode = MarketMode(
            state_manager=sm,
            config=ApplyConfig(mode="market"),
            project_root="/tmp",
        )
        action = _make_action(
            attributes={"language": "python"},
            reason="Needs authentication",
        )
        task = mode._build_market_task(
            action, "Full prompt here"
        )
        assert "terra4mice" in task["title"]
        assert task["type"] == "code_implementation"
        assert "terra4mice" in task["tags"]
        assert task["metadata"]["resource_address"] == "feature.auth"
        assert task["metadata"]["action"] == "create"

    def test_market_mode_custom_url(self):
        sm = FakeStateManager()
        mode = MarketMode(
            state_manager=sm,
            config=ApplyConfig(mode="market"),
            market_url="https://custom.market",
            project_root="/tmp",
        )
        assert mode._market_url == "https://custom.market"


# ══════════════════════════════════════════════════════════════════════
# Integration: AutoMode with Context Registry
# ══════════════════════════════════════════════════════════════════════


class TestAutoModeIntegration:
    def test_full_pipeline(self):
        """Full auto pipeline: build prompt → dispatch → verify → update state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Pre-create the file the agent will "implement"
            Path(tmpdir, "login.py").write_text("def login(): pass\n")

            def smart_agent(prompt, root, timeout):
                assert "feature.login" in prompt
                assert "CREATE" in prompt.upper()
                return AgentResult(
                    success=True,
                    output="Created login.py",
                    files_created=["login.py"],
                    duration_seconds=2.0,
                    exit_code=0,
                )

            sm = FakeStateManager()
            cr = ContextRegistry()
            agent = CallableAgent(fn=smart_agent, name="smart")
            mode = AutoMode(
                state_manager=sm,
                context_registry=cr,
                config=ApplyConfig(mode="auto"),
                agent=agent,
                project_root=tmpdir,
            )

            action = _make_action(name="login", files=["login.py"])
            result = mode.execute([action])

            assert "feature.login" in result.implemented
            assert sm._saved
            # State updated
            res = sm.state.get("feature.login")
            assert res.status == ResourceStatus.IMPLEMENTED
            # Context tracked
            contexts = cr.get_resource_contexts("feature.login")
            assert len(contexts) == 1
            assert contexts[0].agent == "smart"

    def test_dependency_aware_prompts(self):
        """Prompt builder includes dependency status."""
        state = State()
        state.set(_make_resource(
            "module", "database",
            ResourceStatus.IMPLEMENTED,
            files=["db.py"],
        ))
        sm = FakeStateManager(state)

        prompts_seen: list[str] = []

        def capturing_agent(prompt, root, timeout):
            prompts_seen.append(prompt)
            return AgentResult(success=True, exit_code=0)

        agent = CallableAgent(fn=capturing_agent)
        mode = AutoMode(
            state_manager=sm,
            config=ApplyConfig(mode="auto"),
            agent=agent,
            project_root="/tmp",
        )

        action = _make_action(
            name="api",
            depends_on=["module.database"],
        )
        mode.execute([action])

        assert len(prompts_seen) == 1
        assert "module.database" in prompts_seen[0]
        assert "implemented" in prompts_seen[0]
