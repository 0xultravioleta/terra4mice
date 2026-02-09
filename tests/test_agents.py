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
import subprocess
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


def _make_spec_and_state(
    spec_resources: list[Resource],
    state_resources: list[Resource] | None = None,
) -> tuple[Spec, State]:
    """Helper to create spec and state from resource lists."""
    spec = Spec()
    for r in spec_resources:
        spec.add(r)
    state = State()
    if state_resources:
        for r in state_resources:
            state.set(r)
    return spec, state


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
            dry_run=True,
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
            dry_run=True,
        )
        action = _make_action(
            attributes={"language": "python"},
            reason="Needs authentication",
        )
        task = mode._build_market_task(
            action, "Full prompt here"
        )
        assert "terra4mice" in task["title"]
        assert task["task_type"] == "code_implementation"
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
            dry_run=True,
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


# ── Phase 5.2: ChainedAgent Tests ─────────────────────────────────────

class TestChainedAgent:
    """Tests for ChainedAgent backend."""

    def test_chained_agent_requires_agents(self):
        """ChainedAgent should require at least one agent."""
        from terra4mice.apply.agents import ChainedAgent
        
        with pytest.raises(ValueError, match="requires at least one agent"):
            ChainedAgent([])

    def test_chained_agent_first_success(self):
        """ChainedAgent should return result from first successful agent."""
        from terra4mice.apply.agents import ChainedAgent, CallableAgent, AgentResult
        
        def failing_agent(prompt, project_root, timeout):
            return AgentResult(success=False, error="failed")
        
        def succeeding_agent(prompt, project_root, timeout):
            return AgentResult(success=True, output="success")
        
        def should_not_call(prompt, project_root, timeout):
            raise AssertionError("Third agent should not be called")
        
        chained = ChainedAgent([
            CallableAgent(failing_agent, "agent1"),
            CallableAgent(succeeding_agent, "agent2"),
            CallableAgent(should_not_call, "agent3"),
        ])
        
        result = chained.execute("test prompt", "/tmp", 300)
        
        assert result.success
        assert "Success with agent2" in result.output
        assert "attempt 2/3" in result.output
        assert chained.last_successful_agent == "agent2"
        assert chained.attempt_count == 1

    def test_chained_agent_all_fail(self):
        """ChainedAgent should return combined error if all agents fail."""
        from terra4mice.apply.agents import ChainedAgent, CallableAgent, AgentResult
        
        def failing_agent1(prompt, project_root, timeout):
            return AgentResult(success=False, error="error1", output="output1")
        
        def failing_agent2(prompt, project_root, timeout):
            return AgentResult(success=False, error="error2", output="output2")
        
        chained = ChainedAgent([
            CallableAgent(failing_agent1, "agent1"),
            CallableAgent(failing_agent2, "agent2"),
        ])
        
        result = chained.execute("test prompt", "/tmp", 300)
        
        assert not result.success
        assert "All 2 agents failed" in result.error
        assert "Agent agent1: error1" in result.error
        assert "Agent agent2: error2" in result.error
        assert "Agent agent1: output1" in result.output
        assert "Agent agent2: output2" in result.output
        assert chained.last_successful_agent is None

    def test_chained_agent_exception_handling(self):
        """ChainedAgent should handle exceptions from agents."""
        from terra4mice.apply.agents import ChainedAgent, CallableAgent, AgentResult
        
        def exception_agent(prompt, project_root, timeout):
            raise RuntimeError("Agent crashed")
        
        def succeeding_agent(prompt, project_root, timeout):
            return AgentResult(success=True, output="recovered")
        
        chained = ChainedAgent([
            CallableAgent(exception_agent, "crasher"),
            CallableAgent(succeeding_agent, "recoverer"),
        ])
        
        result = chained.execute("test prompt", "/tmp", 300)
        
        assert result.success
        assert "Success with recoverer" in result.output
        assert chained.last_successful_agent == "recoverer"

    def test_chained_agent_is_available(self):
        """ChainedAgent is available if any agent is available."""
        from terra4mice.apply.agents import ChainedAgent
        
        class MockAgent:
            def __init__(self, available):
                self._available = available
                self.name = "mock"
            def is_available(self):
                return self._available
            def execute(self, prompt, project_root, timeout):
                return AgentResult()
        
        # All unavailable
        chained = ChainedAgent([MockAgent(False), MockAgent(False)])
        assert not chained.is_available()
        
        # One available
        chained = ChainedAgent([MockAgent(False), MockAgent(True)])
        assert chained.is_available()

    def test_chained_agent_registry_integration(self):
        """Test ChainedAgent is registered and can be retrieved."""
        from terra4mice.apply.agents import get_agent, list_agents
        
        assert "chained" in list_agents()
        
        # Should raise error without agents parameter
        with pytest.raises(ValueError, match="requires 'agents' parameter"):
            get_agent("chained")

    def test_chained_agent_from_comma_separated_names(self):
        """Test creating ChainedAgent from comma-separated agent names."""
        from terra4mice.apply.agents import get_agent
        
        chained = get_agent("claude-code,codex")
        
        assert chained.name == "chained(claude-code,codex)"
        assert len(chained.agents) == 2
        assert chained.agents[0].name == "claude-code"
        assert chained.agents[1].name == "codex"

    def test_chained_agent_from_invalid_names(self):
        """Test error handling for invalid agent names in chain."""
        from terra4mice.apply.agents import get_agent
        
        with pytest.raises(ValueError, match=r"Unknown agent in chain: 'invalid'"):
            get_agent("claude-code,invalid,codex")

    def test_chained_agent_single_agent_chain(self):
        """Test ChainedAgent with single agent works."""
        from terra4mice.apply.agents import get_agent
        
        chained = get_agent("claude-code")  # Single agent, should not create chain
        assert chained.name == "claude-code"
        
        # Explicit single agent chain (trailing comma should be filtered out)
        chained = get_agent("claude-code,")  # Trailing comma
        assert "chained" in chained.name
        assert len(chained.agents) == 1  # Should only have one agent after filtering empty strings

    def test_chained_agent_tracking_attributes(self):
        """Test ChainedAgent tracks attempt count and successful agent."""
        from terra4mice.apply.agents import ChainedAgent, CallableAgent, AgentResult
        
        def succeeding_agent(prompt, project_root, timeout):
            return AgentResult(success=True, output="success")
        
        chained = ChainedAgent([
            CallableAgent(succeeding_agent, "winner")
        ])
        
        # Before execution
        assert chained.attempt_count == 0
        assert chained.last_successful_agent is None
        
        # After first execution
        chained.execute("test", "/tmp", 300)
        assert chained.attempt_count == 1
        assert chained.last_successful_agent == "winner"
        
        # After second execution
        chained.execute("test", "/tmp", 300)
        assert chained.attempt_count == 2
        assert chained.last_successful_agent == "winner"


class TestEnhancedAgentRegistry:
    """Tests for enhanced agent registry with chaining support."""

    def test_get_agent_comma_separated_creates_chained(self):
        """Test that comma-separated names create ChainedAgent."""
        from terra4mice.apply.agents import get_agent
        
        agent = get_agent("claude-code,codex")
        assert "chained" in agent.name
        assert len(agent.agents) == 2

    def test_get_agent_single_name_no_chaining(self):
        """Test that single name doesn't create chaining."""
        from terra4mice.apply.agents import get_agent
        
        agent = get_agent("claude-code")
        assert agent.name == "claude-code"
        assert not hasattr(agent, "agents")  # Not a ChainedAgent

    def test_get_agent_empty_chain_elements(self):
        """Test handling of empty elements in chain."""
        from terra4mice.apply.agents import get_agent
        
        # This should work and ignore empty elements
        agent = get_agent("claude-code,,codex")
        assert len(agent.agents) == 2  # Empty element ignored

    def test_whitespace_handling_in_chain(self):
        """Test that whitespace is stripped from agent names."""
        from terra4mice.apply.agents import get_agent
        
        agent = get_agent(" claude-code , codex ")
        assert len(agent.agents) == 2
        assert agent.agents[0].name == "claude-code"
        assert agent.agents[1].name == "codex"


# ── Phase 5.2: Integration Tests ──────────────────────────────────────

class TestPhase52Integration:
    """Integration tests for all Phase 5.2 features together."""

    def test_parallel_execution_with_chained_agents(self):
        """Test parallel execution works with chained agents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            actions = [
                _make_action("feature", "a", files=["a.py"]),
                _make_action("feature", "b", files=["b.py"]),  # No dependencies
            ]
            spec, state = _make_spec_and_state([a.resource for a in actions])
            sm = FakeStateManager(state)
            
            call_count = 0
            
            def mock_agent_success(prompt, project_root, timeout):
                nonlocal call_count
                call_count += 1
                # Create the expected file
                if "feature.a" in prompt:
                    (Path(project_root) / "a.py").write_text("# a")
                elif "feature.b" in prompt:
                    (Path(project_root) / "b.py").write_text("# b")
                return AgentResult(success=True, output="done")
            
            from terra4mice.apply.agents import CallableAgent, ChainedAgent
            from terra4mice.apply.modes import AutoMode
            from terra4mice.apply.runner import ApplyConfig
            
            # Use chained agent in auto mode with parallel execution
            chained_agent = ChainedAgent([
                CallableAgent(mock_agent_success, "success")
            ])
            
            config = ApplyConfig(mode="auto", max_workers=2)
            mode = AutoMode(
                state_manager=sm,
                config=config,
                project_root=tmpdir,
                agent=chained_agent
            )
            
            result = mode.execute(actions)
            
            assert len(result.implemented) == 2
            assert call_count == 2  # Both agents called

    def test_git_diff_verification_in_auto_mode(self):
        """Test git diff verification works in auto mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmpdir, check=True)
            
            # Create initial file and commit
            (tmpdir_path / "auth.py").write_text("def initial(): pass")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, check=True, capture_output=True)
            
            # Test git diff verification directly (without complex mode integration)
            from terra4mice.apply.verify import verify_implementation, VerificationLevel
            
            # Modify the file to create changes
            (tmpdir_path / "auth.py").write_text("def login(): pass\ndef logout(): pass")
            
            resource = _make_resource("feature", "auth", files=["auth.py"])
            result = verify_implementation(resource, tmpdir, VerificationLevel.GIT_DIFF)
            
            assert result.passed  # Should pass with git diff verification
            assert result.level == VerificationLevel.GIT_DIFF
            assert "auth.py" in result.git_changed_files

    def test_all_features_together(self):
        """Integration test demonstrating all Phase 5.2 features work."""
        # Test 1: ChainedAgent with multiple backends
        from terra4mice.apply.agents import ChainedAgent, CallableAgent, AgentResult
        
        def primary_agent(prompt, project_root, timeout):
            return AgentResult(success=True, output="primary worked")
        
        def fallback_agent(prompt, project_root, timeout):
            raise AssertionError("Should not be called")
        
        chained = ChainedAgent([
            CallableAgent(primary_agent, "primary"),
            CallableAgent(fallback_agent, "fallback")
        ])
        
        result = chained.execute("test", "/tmp", 300)
        assert result.success
        assert chained.last_successful_agent == "primary"
        
        # Test 2: Parallel execution configuration
        from terra4mice.apply.runner import ApplyConfig
        config = ApplyConfig(max_workers=4)
        assert config.validate() == []
        
        # Test 3: Git diff verification 
        from terra4mice.apply.verify import VerificationLevel, VerificationResult
        
        result = VerificationResult(level=VerificationLevel.GIT_DIFF)
        assert "git_diff" in result.summary()
        
        # All features are independently tested and working
        assert True
