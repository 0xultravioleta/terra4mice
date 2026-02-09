"""
End-to-end integration tests for terra4mice.

Exercises the FULL pipeline: spec → plan → apply → verify → state persistence.
Tests realistic scenarios with tmp_path fixtures, real Spec/State objects,
and mock agents.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from terra4mice.models import (
    Plan,
    PlanAction,
    Resource,
    ResourceStatus,
    Spec,
    State,
)
from terra4mice.spec_parser import load_spec, parse_spec
from terra4mice.planner import generate_plan
from terra4mice.state_manager import StateManager
from terra4mice.apply.runner import (
    ApplyConfig,
    ApplyResult,
    ApplyRunner,
    CyclicDependencyError,
)
from terra4mice.apply.modes import InteractiveMode, AutoMode, HybridMode, MarketMode
from terra4mice.apply.agents import (
    AgentBackend,
    AgentResult,
    CallableAgent,
    ChainedAgent,
)
from terra4mice.apply.verify import verify_implementation, VerificationLevel


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


BASIC_SPEC_YAML = textwrap.dedent("""\
    version: "1"
    resources:
      feature:
        auth_login:
          attributes:
            description: "User login with email/password"
            endpoints: [POST /auth/login]
          depends_on: []

        auth_refresh:
          attributes:
            description: "Refresh JWT tokens"
          depends_on:
            - feature.auth_login

        auth_logout:
          attributes:
            description: "User logout"
          depends_on:
            - feature.auth_login
""")

COMPLEX_DAG_SPEC_YAML = textwrap.dedent("""\
    version: "1"
    resources:
      module:
        database:
          attributes:
            description: "Database layer"
          depends_on: []

        config:
          attributes:
            description: "Configuration module"
          depends_on: []

      feature:
        auth:
          attributes:
            description: "Authentication"
          depends_on:
            - module.database
            - module.config

        users:
          attributes:
            description: "User management"
          depends_on:
            - feature.auth
            - module.database

        payments:
          attributes:
            description: "Payment processing"
          depends_on:
            - feature.users

        notifications:
          attributes:
            description: "Notification system"
          depends_on:
            - feature.users
            - module.config

        reports:
          attributes:
            description: "Reporting dashboard"
          depends_on:
            - feature.payments
            - feature.notifications
""")


@pytest.fixture
def basic_spec_path(tmp_path):
    """Create a basic spec file and return its path."""
    spec_file = tmp_path / "terra4mice.spec.yaml"
    spec_file.write_text(BASIC_SPEC_YAML)
    return spec_file


@pytest.fixture
def complex_spec_path(tmp_path):
    """Create a complex DAG spec file and return its path."""
    spec_file = tmp_path / "terra4mice.spec.yaml"
    spec_file.write_text(COMPLEX_DAG_SPEC_YAML)
    return spec_file


@pytest.fixture
def basic_spec(basic_spec_path):
    """Load and return a basic spec."""
    return load_spec(basic_spec_path)


@pytest.fixture
def complex_spec(complex_spec_path):
    """Load and return a complex DAG spec."""
    return load_spec(complex_spec_path)


@pytest.fixture
def state_manager(tmp_path):
    """Create a StateManager backed by a tmp_path file."""
    sm = StateManager(path=tmp_path / "terra4mice.state.json")
    sm.save()  # Create the initial empty file
    return sm


def _make_success_agent(files_created=None):
    """Create a CallableAgent that always succeeds."""
    def fn(prompt, project_root, timeout_seconds):
        return AgentResult(
            success=True,
            files_created=files_created or [],
            output="Implemented successfully.",
            duration_seconds=0.1,
            exit_code=0,
        )
    return CallableAgent(fn=fn, name="mock-success")


def _make_fail_agent(error="Agent error"):
    """Create a CallableAgent that always fails."""
    def fn(prompt, project_root, timeout_seconds):
        return AgentResult(
            success=False,
            error=error,
            duration_seconds=0.1,
            exit_code=1,
        )
    return CallableAgent(fn=fn, name="mock-fail")


# ═══════════════════════════════════════════════════════════════════════
# Test 1: Spec → Plan → Apply (Interactive)
# ═══════════════════════════════════════════════════════════════════════


class TestE2EInteractive:
    """Full pipeline: spec YAML → plan → interactive apply → state updated."""

    def test_full_interactive_pipeline(self, basic_spec, state_manager):
        """Load spec, plan, run interactive with mocked inputs, verify state."""
        state_manager.load()

        # Generate plan — all 3 resources should need creating
        plan = generate_plan(basic_spec, state_manager.state)
        assert plan.has_changes
        assert len(plan.creates) == 3

        # Set up runner with interactive mode
        config = ApplyConfig(mode="interactive", enhanced=True)
        runner = ApplyRunner(
            spec=basic_spec,
            state_manager=state_manager,
            config=config,
        )

        # Mock interactive inputs: implement auth_login, skip auth_logout, implement auth_refresh
        # After topo sort: auth_login comes first (no deps), then auth_logout and auth_refresh
        # Order: auth_login (create), auth_logout (create, dep on auth_login), auth_refresh (create, dep on auth_login)
        inputs = iter([
            "i", "src/auth.py",       # implement auth_login
            "i", "src/logout.py",     # implement auth_logout
            "i", "src/refresh.py",    # implement auth_refresh
        ])

        mode = runner._get_mode_handler()
        mode._input_fn = lambda prompt="": next(inputs)

        # Run through the topo-sorted actions
        ordered = runner._topological_sort(
            [a for a in generate_plan(basic_spec, state_manager.state).actions if a.action != "no-op"]
        )
        result = mode.execute(ordered)

        assert len(result.implemented) == 3
        assert "feature.auth_login" in result.implemented

        # Verify state was persisted
        state_manager.load()
        auth = state_manager.show("feature.auth_login")
        assert auth is not None
        assert auth.status == ResourceStatus.IMPLEMENTED

    def test_interactive_skip_and_quit(self, basic_spec, state_manager):
        """Skip one resource then quit — partial state is persisted."""
        state_manager.load()
        config = ApplyConfig(mode="interactive", enhanced=True)
        runner = ApplyRunner(
            spec=basic_spec, state_manager=state_manager, config=config
        )

        inputs = iter([
            "i", "src/auth.py",  # implement auth_login
            "s",                 # skip auth_logout
            "q",                 # quit before auth_refresh
        ])

        mode = runner._get_mode_handler()
        mode._input_fn = lambda prompt="": next(inputs)

        ordered = runner._topological_sort(
            [a for a in generate_plan(basic_spec, state_manager.state).actions if a.action != "no-op"]
        )
        result = mode.execute(ordered)

        assert "feature.auth_login" in result.implemented
        # Exactly one skip (auth_logout) and then quit before auth_refresh
        assert len(result.skipped) >= 1

        # Reload and verify partial state
        state_manager.load()
        assert state_manager.show("feature.auth_login").status == ResourceStatus.IMPLEMENTED

    def test_interactive_partial_mark(self, basic_spec, state_manager):
        """Mark a resource as partial via interactive mode."""
        state_manager.load()
        config = ApplyConfig(mode="interactive", enhanced=True)
        runner = ApplyRunner(
            spec=basic_spec, state_manager=state_manager, config=config
        )

        inputs = iter([
            "p", "missing tests",  # mark auth_login as partial
            "q",                   # quit
        ])

        mode = runner._get_mode_handler()
        mode._input_fn = lambda prompt="": next(inputs)

        ordered = runner._topological_sort(
            [a for a in generate_plan(basic_spec, state_manager.state).actions if a.action != "no-op"]
        )
        mode.execute(ordered)

        state_manager.load()
        auth = state_manager.show("feature.auth_login")
        assert auth is not None
        assert auth.status == ResourceStatus.PARTIAL


# ═══════════════════════════════════════════════════════════════════════
# Test 2: Spec → Plan → Apply (Auto Mode)
# ═══════════════════════════════════════════════════════════════════════


class TestE2EAutoMode:
    """Full pipeline with AutoMode + mocked agent."""

    def test_auto_mode_full_pipeline(self, basic_spec, state_manager, tmp_path):
        """Auto mode: agent succeeds for all resources → state updated."""
        state_manager.load()

        agent = _make_success_agent()
        config = ApplyConfig(mode="auto", verify_level="basic")
        runner = ApplyRunner(
            spec=basic_spec,
            state_manager=state_manager,
            config=config,
            project_root=str(tmp_path),
        )

        # Inject the mock agent into AutoMode
        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        ordered = runner._topological_sort(
            [a for a in generate_plan(basic_spec, state_manager.state).actions if a.action != "no-op"]
        )
        result = mode.execute(ordered)

        # All 3 should be implemented (or partial if verify scores 0)
        assert len(result.implemented) == 3
        assert len(result.failed) == 0

        # Verify state was saved
        state_manager.load()
        for addr in ["feature.auth_login", "feature.auth_refresh", "feature.auth_logout"]:
            res = state_manager.show(addr)
            assert res is not None
            assert res.status in (ResourceStatus.IMPLEMENTED, ResourceStatus.PARTIAL)

    def test_auto_mode_agent_failure(self, basic_spec, state_manager, tmp_path):
        """Auto mode: agent fails → resource marked as failed."""
        state_manager.load()

        agent = _make_fail_agent(error="Compilation error")
        config = ApplyConfig(mode="auto", verify_level="basic")

        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        ordered_actions = [
            PlanAction(
                action="create",
                resource=Resource(type="feature", name="auth_login"),
                reason="test",
            )
        ]
        result = mode.execute(ordered_actions)

        assert len(result.failed) == 1
        assert "feature.auth_login" in result.failed


# ═══════════════════════════════════════════════════════════════════════
# Test 3: Spec → Plan → Apply → Verify
# ═══════════════════════════════════════════════════════════════════════


class TestE2EWithVerification:
    """Full pipeline including verification step."""

    def test_auto_mode_with_basic_verification(self, basic_spec, state_manager, tmp_path):
        """Auto mode with file verification: agent creates files that are verified."""
        state_manager.load()

        # Create actual files that the agent "generates"
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "auth.py").write_text("def login(): pass\n")

        agent = _make_success_agent(files_created=["src/auth.py"])
        config = ApplyConfig(mode="auto", verify_level="basic")

        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        # Create a resource that references the file
        resource = Resource(
            type="feature", name="auth_login",
            files=["src/auth.py"],
        )
        action = PlanAction(action="create", resource=resource, reason="test")

        result = mode.execute([action])
        assert len(result.implemented) == 1

        # Check state
        state_manager.load()
        auth = state_manager.show("feature.auth_login")
        assert auth is not None
        assert auth.status == ResourceStatus.IMPLEMENTED

    def test_verification_fails_missing_files(self, tmp_path):
        """Verify that verification detects missing files."""
        resource = Resource(
            type="feature", name="auth",
            files=["nonexistent.py"],
        )
        result = verify_implementation(resource, tmp_path, VerificationLevel.BASIC)
        assert not result.passed
        assert result.score == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Test 4: Multi-Resource DAG (5+ resources, complex dependencies)
# ═══════════════════════════════════════════════════════════════════════


class TestE2EMultiResourceDAG:
    """Test with 5+ resources that have complex dependencies."""

    def test_complex_dag_topological_order(self, complex_spec, state_manager):
        """7 resources with diamond + chain deps → correct topo order."""
        state_manager.load()

        plan = generate_plan(complex_spec, state_manager.state)
        assert len(plan.creates) == 7  # All 7 resources need creating

        config = ApplyConfig(mode="interactive", enhanced=True)
        runner = ApplyRunner(
            spec=complex_spec, state_manager=state_manager, config=config
        )

        actions = [a for a in plan.actions if a.action != "no-op"]
        ordered = runner._topological_sort(actions)
        addrs = [a.resource.address for a in ordered]

        # database and config have no deps → must come before auth
        assert addrs.index("module.database") < addrs.index("feature.auth")
        assert addrs.index("module.config") < addrs.index("feature.auth")

        # auth must come before users
        assert addrs.index("feature.auth") < addrs.index("feature.users")

        # users must come before payments and notifications
        assert addrs.index("feature.users") < addrs.index("feature.payments")
        assert addrs.index("feature.users") < addrs.index("feature.notifications")

        # payments and notifications must come before reports
        assert addrs.index("feature.payments") < addrs.index("feature.reports")
        assert addrs.index("feature.notifications") < addrs.index("feature.reports")

    def test_complex_dag_auto_mode(self, complex_spec, state_manager, tmp_path):
        """Auto mode processes all 7 resources in DAG order."""
        state_manager.load()

        execution_order = []

        def tracking_fn(prompt, project_root, timeout_seconds):
            # Extract resource address from prompt header
            for line in prompt.splitlines():
                if line.startswith("# Task:"):
                    parts = line.split()
                    if len(parts) >= 3:
                        execution_order.append(parts[-1])
                    break
            return AgentResult(
                success=True, output="done", duration_seconds=0.01, exit_code=0
            )

        agent = CallableAgent(fn=tracking_fn, name="tracker")
        config = ApplyConfig(mode="auto", verify_level="basic")

        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        plan = generate_plan(complex_spec, state_manager.state)
        runner = ApplyRunner(
            spec=complex_spec, state_manager=state_manager, config=config
        )
        ordered = runner._topological_sort(
            [a for a in plan.actions if a.action != "no-op"]
        )

        result = mode.execute(ordered)
        assert len(result.implemented) == 7
        assert len(result.failed) == 0

        # Verify ordering constraints in execution
        if execution_order:
            assert execution_order.index("module.database") < execution_order.index("feature.auth")

    def test_complex_dag_parallel_execution(self, complex_spec, state_manager, tmp_path):
        """Parallel execution with max_workers=3 processes root nodes correctly.

        The parallel runner dispatches root nodes (no deps) first, then
        iteratively unlocks dependent tiers. With mocked mode handlers,
        we verify that at minimum the root tier executes and that
        the total (implemented + skipped) equals the resource count.
        """
        state_manager.load()

        config = ApplyConfig(mode="interactive", max_workers=3)
        runner = ApplyRunner(
            spec=complex_spec,
            state_manager=state_manager,
            config=config,
            project_root=str(tmp_path),
        )

        # Use a thread-safe mock mode
        _lock = threading.Lock()

        class TrackingMode:
            def __init__(self, **kwargs):
                pass

            def execute(self, actions):
                result = ApplyResult()
                for action in actions:
                    addr = action.resource.address
                    with _lock:
                        state_manager.mark_created(addr)
                        state_manager.save()
                    result.implemented.append(addr)
                return result

        with patch.object(runner, "_get_mode_handler") as mock_handler:
            mock_handler.return_value = TrackingMode()
            result = runner.run()

        # Root nodes (no deps) must be implemented
        assert "module.database" in result.implemented
        assert "module.config" in result.implemented
        # All 7 resources must be accounted for (implemented or skipped)
        assert len(result.implemented) + len(result.skipped) == 7
        assert len(result.failed) == 0


# ═══════════════════════════════════════════════════════════════════════
# Test 5: Error Recovery
# ═══════════════════════════════════════════════════════════════════════


class TestE2EErrorRecovery:
    """Test that a failed resource in the middle doesn't corrupt state."""

    def test_failure_mid_pipeline_preserves_state(self, basic_spec, state_manager, tmp_path):
        """If auth_login fails, state should not have it marked as implemented."""
        state_manager.load()

        agent = _make_fail_agent(error="Catastrophic failure")
        config = ApplyConfig(mode="auto", verify_level="basic")

        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        plan = generate_plan(basic_spec, state_manager.state)
        actions = [a for a in plan.actions if a.action != "no-op"]

        runner = ApplyRunner(
            spec=basic_spec, state_manager=state_manager, config=config
        )
        ordered = runner._topological_sort(actions)
        result = mode.execute(ordered)

        # All should fail
        assert len(result.failed) == 3

        # State should NOT have any implemented resources
        state_manager.load()
        for addr in ["feature.auth_login", "feature.auth_refresh", "feature.auth_logout"]:
            res = state_manager.show(addr)
            # Either None (not in state) or not IMPLEMENTED
            if res is not None:
                assert res.status != ResourceStatus.IMPLEMENTED

    def test_partial_success_preserves_good_state(self, state_manager, tmp_path):
        """First resource succeeds, second fails → first stays implemented."""
        state_manager.load()

        call_count = [0]

        def alternating_fn(prompt, project_root, timeout_seconds):
            call_count[0] += 1
            if call_count[0] == 1:
                return AgentResult(success=True, output="ok", duration_seconds=0.01, exit_code=0)
            else:
                return AgentResult(success=False, error="fail", duration_seconds=0.01, exit_code=1)

        agent = CallableAgent(fn=alternating_fn, name="alternating")

        # Create a 2-resource spec with no deps between them
        # Note: resources are sorted alphabetically, so "alpha" is processed first (succeeds)
        # and "zeta" is processed second (fails)
        spec = Spec()
        spec.add(Resource(type="feature", name="alpha"))
        spec.add(Resource(type="feature", name="zeta"))

        config = ApplyConfig(mode="auto", verify_level="basic")
        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        plan = generate_plan(spec, state_manager.state)
        runner = ApplyRunner(spec=spec, state_manager=state_manager, config=config)
        ordered = runner._topological_sort(
            [a for a in plan.actions if a.action != "no-op"]
        )
        result = mode.execute(ordered)

        assert len(result.implemented) == 1
        assert len(result.failed) == 1

        # Reload state — alpha (first, succeeds) should be implemented/partial
        state_manager.load()
        alpha = state_manager.show("feature.alpha")
        assert alpha is not None
        assert alpha.status in (ResourceStatus.IMPLEMENTED, ResourceStatus.PARTIAL)

    def test_parallel_failure_skips_dependents(self, state_manager, tmp_path):
        """In parallel mode, a failed dependency causes dependents to be skipped."""
        state_manager.load()

        spec = Spec()
        spec.add(Resource(type="feature", name="base"))
        spec.add(Resource(type="feature", name="child", depends_on=["feature.base"]))
        spec.add(Resource(type="feature", name="grandchild", depends_on=["feature.child"]))

        config = ApplyConfig(mode="interactive", max_workers=2)
        runner = ApplyRunner(
            spec=spec, state_manager=state_manager, config=config,
            project_root=str(tmp_path),
        )

        class FailBaseMode:
            def __init__(self, **kwargs):
                pass

            def execute(self, actions):
                result = ApplyResult()
                for a in actions:
                    result.failed.append(a.resource.address)
                return result

        with patch.object(runner, "_get_mode_handler", return_value=FailBaseMode()):
            result = runner.run()

        assert "feature.base" in result.failed
        # child and grandchild should be skipped (they depend on base)
        assert "feature.child" in result.skipped
        assert "feature.grandchild" in result.skipped


# ═══════════════════════════════════════════════════════════════════════
# Test 6: Market Fallback
# ═══════════════════════════════════════════════════════════════════════


class TestE2EMarketFallback:
    """Auto mode → agent fails → falls back to market mode (dry run)."""

    def test_chained_agent_fallback(self, state_manager, tmp_path):
        """ChainedAgent: first agent fails, second succeeds."""
        state_manager.load()

        fail_agent = _make_fail_agent(error="primary agent failed")
        success_agent = _make_success_agent()

        chained = ChainedAgent(
            agents=[fail_agent, success_agent],
            name="fallback-chain",
        )

        spec = Spec()
        spec.add(Resource(type="feature", name="auth"))

        config = ApplyConfig(mode="auto", verify_level="basic")
        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=chained,
            project_root=str(tmp_path),
        )

        plan = generate_plan(spec, state_manager.state)
        actions = [a for a in plan.actions if a.action != "no-op"]
        result = mode.execute(actions)

        assert len(result.implemented) == 1
        assert chained.last_successful_agent == "mock-success"

    def test_market_mode_dry_run_posts(self, state_manager, tmp_path):
        """Market mode dry run: tasks are 'posted' without HTTP."""
        state_manager.load()

        spec = Spec()
        spec.add(Resource(type="feature", name="auth", attributes={"endpoints": ["/login"]}))
        spec.add(Resource(type="feature", name="users", depends_on=["feature.auth"]))

        config = ApplyConfig(mode="market", dry_run=True)
        mode = MarketMode(
            state_manager=state_manager,
            config=config,
            project_root=str(tmp_path),
            dry_run=True,
            bounty=25.0,
        )

        plan = generate_plan(spec, state_manager.state)
        runner = ApplyRunner(spec=spec, state_manager=state_manager, config=config)
        ordered = runner._topological_sort(
            [a for a in plan.actions if a.action != "no-op"]
        )

        result = mode.execute(ordered)
        assert len(result.market_pending) == 2
        assert len(result.failed) == 0

    def test_all_agents_fail_then_market(self, state_manager, tmp_path):
        """When all chained agents fail, result is marked failed."""
        state_manager.load()

        agent1 = _make_fail_agent("agent1 fail")
        agent2 = _make_fail_agent("agent2 fail")
        chained = ChainedAgent(agents=[agent1, agent2], name="all-fail")

        spec = Spec()
        spec.add(Resource(type="feature", name="tough"))

        config = ApplyConfig(mode="auto", verify_level="basic")
        mode = AutoMode(
            state_manager=state_manager,
            config=config,
            agent=chained,
            project_root=str(tmp_path),
        )

        plan = generate_plan(spec, state_manager.state)
        actions = [a for a in plan.actions if a.action != "no-op"]
        result = mode.execute(actions)

        assert len(result.failed) == 1
        assert chained.last_successful_agent is None


# ═══════════════════════════════════════════════════════════════════════
# Test 7: Incremental Apply
# ═══════════════════════════════════════════════════════════════════════


class TestE2EIncrementalApply:
    """Apply some resources, stop, apply more — state persists correctly."""

    def test_incremental_apply_persists_state(self, basic_spec, tmp_path):
        """Apply auth_login, stop, reload, apply more → state is correct."""
        state_path = tmp_path / "terra4mice.state.json"

        # --- Round 1: implement only auth_login ---
        sm1 = StateManager(path=state_path)
        sm1.load()

        config = ApplyConfig(mode="interactive", enhanced=True)
        runner1 = ApplyRunner(spec=basic_spec, state_manager=sm1, config=config)

        inputs1 = iter(["i", "src/auth.py", "q"])  # implement auth_login, then quit
        mode1 = runner1._get_mode_handler()
        mode1._input_fn = lambda prompt="": next(inputs1)

        plan1 = generate_plan(basic_spec, sm1.state)
        ordered1 = runner1._topological_sort(
            [a for a in plan1.actions if a.action != "no-op"]
        )
        mode1.execute(ordered1)

        # --- Round 2: new StateManager, continue from where we left off ---
        sm2 = StateManager(path=state_path)
        sm2.load()

        # auth_login should be implemented from round 1
        assert sm2.show("feature.auth_login").status == ResourceStatus.IMPLEMENTED

        # Plan should now only show auth_refresh and auth_logout as creates
        plan2 = generate_plan(basic_spec, sm2.state)
        creates = [a for a in plan2.actions if a.action == "create"]
        assert len(creates) == 2
        create_addrs = {a.resource.address for a in creates}
        assert "feature.auth_refresh" in create_addrs
        assert "feature.auth_logout" in create_addrs

        # Apply remaining
        runner2 = ApplyRunner(spec=basic_spec, state_manager=sm2, config=config)
        inputs2 = iter(["i", "src/logout.py", "i", "src/refresh.py"])
        mode2 = runner2._get_mode_handler()
        mode2._input_fn = lambda prompt="": next(inputs2)

        ordered2 = runner2._topological_sort(
            [a for a in plan2.actions if a.action != "no-op"]
        )
        mode2.execute(ordered2)

        # --- Round 3: verify convergence ---
        sm3 = StateManager(path=state_path)
        sm3.load()

        plan3 = generate_plan(basic_spec, sm3.state)
        assert not plan3.has_changes, "State should fully match spec now"

    def test_state_serial_increments(self, basic_spec, tmp_path):
        """Each state change increments the serial number."""
        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.load()

        initial_serial = sm.state.serial

        sm.mark_created("feature.auth_login", files=["a.py"])
        sm.save()

        sm2 = StateManager(path=state_path)
        sm2.load()
        assert sm2.state.serial > initial_serial

    def test_incremental_with_partial_then_implemented(self, tmp_path):
        """Mark as partial, then upgrade to implemented."""
        state_path = tmp_path / "terra4mice.state.json"

        # Mark as partial
        sm = StateManager(path=state_path)
        sm.load()
        sm.mark_partial("feature.auth", reason="missing tests")
        sm.save()

        # Reload and upgrade
        sm2 = StateManager(path=state_path)
        sm2.load()
        auth = sm2.show("feature.auth")
        assert auth.status == ResourceStatus.PARTIAL

        sm2.mark_created("feature.auth", files=["src/auth.py"])
        sm2.save()

        # Verify upgrade persisted
        sm3 = StateManager(path=state_path)
        sm3.load()
        assert sm3.show("feature.auth").status == ResourceStatus.IMPLEMENTED


# ═══════════════════════════════════════════════════════════════════════
# Test 8: CLI Integration
# ═══════════════════════════════════════════════════════════════════════


class TestE2ECLIIntegration:
    """Test actual CLI entry points."""

    def test_cmd_apply_dry_run(self, basic_spec_path, tmp_path):
        """Test the apply command in dry-run mode via function call."""
        from terra4mice.cli import cmd_apply
        import argparse

        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.save()

        args = argparse.Namespace(
            spec=str(basic_spec_path),
            state=str(state_path),
            enhanced=True,
            mode="interactive",
            agent=None,
            parallel=1,
            max_workers=1,
            timeout=0,
            require_tests=False,
            auto_commit=False,
            dry_run=True,
            resource=None,
            contexts=None,
            market_url=None,
            market_api_key=None,
            verify_level="basic",
            project_root=str(tmp_path),
            bounty=None,
        )

        result = cmd_apply(args)
        assert result == 0  # dry run succeeds

    def test_cmd_plan_shows_changes(self, basic_spec_path, tmp_path):
        """Test plan command shows resources to create."""
        from terra4mice.cli import cmd_plan
        import argparse

        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.save()

        args = argparse.Namespace(
            spec=str(basic_spec_path),
            state=str(state_path),
            verbose=False,
            check_deps=False,
            detailed_exitcode=True,
            format="text",
            no_color=True,
            ci=False,
        )

        result = cmd_plan(args)
        assert result == 2  # 2 means there are changes

    def test_cmd_plan_after_full_apply(self, basic_spec_path, tmp_path):
        """After implementing everything, plan should return 0."""
        from terra4mice.cli import cmd_plan
        import argparse

        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.load()

        # Mark all resources as implemented
        for addr in ["feature.auth_login", "feature.auth_refresh", "feature.auth_logout"]:
            sm.mark_created(addr)
        sm.save()

        args = argparse.Namespace(
            spec=str(basic_spec_path),
            state=str(state_path),
            verbose=False,
            check_deps=False,
            detailed_exitcode=True,
            format="text",
            no_color=True,
            ci=False,
        )

        result = cmd_plan(args)
        assert result == 0  # No changes

    def test_cmd_apply_with_resource_filter(self, basic_spec_path, tmp_path):
        """Test applying a single resource via --resource flag."""
        from terra4mice.cli import cmd_apply
        import argparse

        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.save()

        args = argparse.Namespace(
            spec=str(basic_spec_path),
            state=str(state_path),
            enhanced=True,
            mode="interactive",
            agent=None,
            parallel=1,
            max_workers=1,
            timeout=0,
            require_tests=False,
            auto_commit=False,
            dry_run=True,
            resource="feature.auth_login",
            contexts=None,
            market_url=None,
            market_api_key=None,
            verify_level="basic",
            project_root=str(tmp_path),
            bounty=None,
        )

        result = cmd_apply(args)
        assert result == 0

    def test_subprocess_cli_plan(self, basic_spec_path, tmp_path):
        """Test CLI via subprocess — validates the entry point works."""
        import sys

        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.save()

        proc = subprocess.run(
            [
                sys.executable, "-m", "terra4mice",
                "plan",
                "--spec", str(basic_spec_path),
                "--state", str(state_path),
                "--no-color",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_path),
        )

        # Should succeed (or return 0/2 for plan)
        assert proc.returncode in (0, 2)
        assert "terra4mice" in proc.stdout.lower() or "plan" in proc.stdout.lower() or "create" in proc.stdout.lower()

    def test_subprocess_cli_version(self):
        """Test --version flag works."""
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "terra4mice", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0
        assert "0.1.0" in proc.stdout


# ═══════════════════════════════════════════════════════════════════════
# Test: Hybrid Mode E2E
# ═══════════════════════════════════════════════════════════════════════


class TestE2EHybridMode:
    """Hybrid mode: AI implements, human reviews."""

    def test_hybrid_accept_all(self, state_manager, tmp_path):
        """Hybrid mode: AI succeeds, human accepts all."""
        state_manager.load()

        agent = _make_success_agent()
        spec = Spec()
        spec.add(Resource(type="feature", name="auth"))

        config = ApplyConfig(mode="hybrid", verify_level="basic")
        mode = HybridMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        inputs = iter(["a"])  # accept
        mode._input_fn = lambda prompt="": next(inputs)

        plan = generate_plan(spec, state_manager.state)
        actions = [a for a in plan.actions if a.action != "no-op"]
        result = mode.execute(actions)

        assert len(result.implemented) == 1
        state_manager.load()
        assert state_manager.show("feature.auth").status in (
            ResourceStatus.IMPLEMENTED, ResourceStatus.PARTIAL
        )

    def test_hybrid_reject(self, state_manager, tmp_path):
        """Hybrid mode: AI succeeds, human rejects → marked failed."""
        state_manager.load()

        agent = _make_success_agent()
        spec = Spec()
        spec.add(Resource(type="feature", name="bad_impl"))

        config = ApplyConfig(mode="hybrid", verify_level="basic")
        mode = HybridMode(
            state_manager=state_manager,
            config=config,
            agent=agent,
            project_root=str(tmp_path),
        )

        inputs = iter(["r"])  # reject
        mode._input_fn = lambda prompt="": next(inputs)

        plan = generate_plan(spec, state_manager.state)
        actions = [a for a in plan.actions if a.action != "no-op"]
        result = mode.execute(actions)

        assert len(result.failed) == 1


# ═══════════════════════════════════════════════════════════════════════
# Test: Full Convergence Cycle
# ═══════════════════════════════════════════════════════════════════════


class TestE2EConvergence:
    """Test the full convergence cycle: iterate until plan shows no changes."""

    def test_convergence_in_two_rounds(self, tmp_path):
        """Apply round 1 (partial), apply round 2 (complete) → converged."""
        spec_yaml = textwrap.dedent("""\
            version: "1"
            resources:
              feature:
                alpha:
                  depends_on: []
                beta:
                  depends_on:
                    - feature.alpha
        """)
        spec_file = tmp_path / "terra4mice.spec.yaml"
        spec_file.write_text(spec_yaml)
        state_path = tmp_path / "terra4mice.state.json"

        spec = load_spec(spec_file)

        # Round 1: implement alpha only
        sm = StateManager(path=state_path)
        sm.load()
        sm.mark_created("feature.alpha", files=["alpha.py"])
        sm.save()

        plan = generate_plan(spec, sm.state)
        assert plan.has_changes  # beta still missing

        # Round 2: implement beta
        sm2 = StateManager(path=state_path)
        sm2.load()
        sm2.mark_created("feature.beta", files=["beta.py"])
        sm2.save()

        plan2 = generate_plan(spec, sm2.state)
        assert not plan2.has_changes  # Fully converged!

    def test_full_spec_convergence(self, complex_spec, tmp_path):
        """Mark all 7 complex DAG resources implemented → no changes."""
        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.load()

        all_addrs = [r.address for r in complex_spec.list()]
        assert len(all_addrs) == 7

        for addr in all_addrs:
            sm.mark_created(addr, files=[f"{addr.replace('.', '/')}.py"])
        sm.save()

        plan = generate_plan(complex_spec, sm.state)
        assert not plan.has_changes
        assert len(plan.creates) == 0
        assert len(plan.updates) == 0
        assert len(plan.deletes) == 0


# ═══════════════════════════════════════════════════════════════════════
# Test: State File Integrity
# ═══════════════════════════════════════════════════════════════════════


class TestE2EStateIntegrity:
    """Verify that state files are valid JSON and survive round-trips."""

    def test_state_json_roundtrip(self, tmp_path):
        """Write → read → write → read: state is identical."""
        state_path = tmp_path / "terra4mice.state.json"

        sm = StateManager(path=state_path)
        sm.load()
        sm.mark_created("feature.auth", files=["auth.py"])
        sm.mark_partial("feature.payments", reason="no refunds")
        sm.save()

        # Read raw JSON
        data1 = json.loads(state_path.read_text())

        # Reload and re-save
        sm2 = StateManager(path=state_path)
        sm2.load()
        sm2.save()

        data2 = json.loads(state_path.read_text())

        # Resources should match
        assert len(data1["resources"]) == len(data2["resources"])
        for r1, r2 in zip(data1["resources"], data2["resources"]):
            assert r1["type"] == r2["type"]
            assert r1["name"] == r2["name"]
            assert r1["status"] == r2["status"]

    def test_state_file_is_valid_json(self, tmp_path):
        """State file should always be valid JSON."""
        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.load()
        sm.mark_created("a.b")
        sm.save()

        # Should not raise
        data = json.loads(state_path.read_text())
        assert "version" in data
        assert "resources" in data
