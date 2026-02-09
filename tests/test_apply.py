"""
Tests for terra4mice Phase 5: Apply Runner.

Tests cover:
- ApplyConfig validation
- ApplyResult tracking and summary
- Topological sort (simple, complex, cycles, no deps)
- InteractiveMode with mocked input
- verify_implementation with existing/missing files
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
import pytest

from terra4mice.models import (
    Plan,
    PlanAction,
    Resource,
    ResourceStatus,
    Spec,
    State,
)
from terra4mice.state_manager import StateManager
from terra4mice.apply.runner import (
    ApplyConfig,
    ApplyResult,
    ApplyRunner,
    CyclicDependencyError,
)
from terra4mice.apply.modes import InteractiveMode, AutoMode, HybridMode, MarketMode
from terra4mice.apply.verify import VerificationResult, verify_implementation


# ── Helpers ───────────────────────────────────────────────────────────

def _make_resource(
    rtype: str,
    name: str,
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
    rtype: str,
    name: str,
    action: str = "create",
    depends_on: list[str] | None = None,
    files: list[str] | None = None,
    attributes: dict | None = None,
) -> PlanAction:
    resource = _make_resource(
        rtype, name, depends_on=depends_on, files=files, attributes=attributes
    )
    return PlanAction(action=action, resource=resource, reason="test")


def _make_spec_and_state(
    spec_resources: list[Resource],
    state_resources: list[Resource] | None = None,
) -> tuple[Spec, State]:
    spec = Spec()
    for r in spec_resources:
        spec.add(r)
    state = State()
    if state_resources:
        for r in state_resources:
            state.set(r)
    return spec, state


class FakeStateManager:
    """Minimal StateManager fake for testing."""

    def __init__(self, state: State | None = None):
        self.state = state or State()
        self._saved = False

    def load(self):
        pass

    def save(self):
        self._saved = True

    def mark_created(self, address: str, files: list[str] | None = None):
        resource = self.state.get(address)
        if resource is None:
            parts = address.split(".", 1)
            resource = Resource(type=parts[0], name=parts[1] if len(parts) > 1 else parts[0])
        resource.status = ResourceStatus.IMPLEMENTED
        if files:
            resource.files = files
        self.state.set(resource)

    def mark_partial(self, address: str, reason: str = ""):
        resource = self.state.get(address)
        if resource is None:
            parts = address.split(".", 1)
            resource = Resource(type=parts[0], name=parts[1] if len(parts) > 1 else parts[0])
        resource.status = ResourceStatus.PARTIAL
        self.state.set(resource)


# ══════════════════════════════════════════════════════════════════════
# ApplyConfig Tests
# ══════════════════════════════════════════════════════════════════════

class TestApplyConfig:
    def test_defaults(self):
        cfg = ApplyConfig()
        assert cfg.mode == "interactive"
        assert cfg.agent is None
        assert cfg.parallel == 1
        assert cfg.timeout_minutes == 0
        assert cfg.require_tests is False
        assert cfg.auto_commit is False
        assert cfg.dry_run is False
        assert cfg.enhanced is True

    def test_valid_config(self):
        cfg = ApplyConfig(mode="auto", agent="claude-code", parallel=3)
        assert cfg.validate() == []

    def test_invalid_mode(self):
        cfg = ApplyConfig(mode="yolo")
        errors = cfg.validate()
        assert len(errors) == 1
        assert "Invalid mode" in errors[0]

    def test_invalid_parallel(self):
        cfg = ApplyConfig(parallel=0)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "parallel" in errors[0]

    def test_invalid_timeout(self):
        cfg = ApplyConfig(timeout_minutes=-5)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "timeout_minutes" in errors[0]

    def test_multiple_errors(self):
        cfg = ApplyConfig(mode="bad", parallel=-1, timeout_minutes=-10)
        errors = cfg.validate()
        assert len(errors) == 3


# ══════════════════════════════════════════════════════════════════════
# ApplyResult Tests
# ══════════════════════════════════════════════════════════════════════

class TestApplyResult:
    def test_empty_result(self):
        r = ApplyResult()
        assert r.total == 0
        assert "No actions" in r.summary()

    def test_with_implemented(self):
        r = ApplyResult(implemented=["a.b", "c.d"])
        assert r.total == 2
        assert "2 implemented" in r.summary()

    def test_with_mixed(self):
        r = ApplyResult(
            implemented=["a.b"],
            skipped=["c.d"],
            failed=["e.f"],
            market_pending=["g.h"],
            duration_seconds=42.5,
        )
        assert r.total == 4
        summary = r.summary()
        assert "1 implemented" in summary
        assert "1 skipped" in summary
        assert "1 failed" in summary
        assert "1 pending on market" in summary
        assert "42.5s" in summary


# ══════════════════════════════════════════════════════════════════════
# Topological Sort Tests
# ══════════════════════════════════════════════════════════════════════

class TestTopologicalSort:
    def _make_runner(self, state: State | None = None) -> ApplyRunner:
        spec = Spec()
        sm = FakeStateManager(state)
        return ApplyRunner(spec, sm)

    def test_no_dependencies(self):
        runner = self._make_runner()
        actions = [
            _make_action("feature", "c"),
            _make_action("feature", "a"),
            _make_action("feature", "b"),
        ]
        result = runner._topological_sort(actions)
        # With no deps, should be alphabetical (deterministic sort)
        addrs = [a.resource.address for a in result]
        assert addrs == ["feature.a", "feature.b", "feature.c"]

    def test_simple_chain(self):
        """A → B → C"""
        runner = self._make_runner()
        actions = [
            _make_action("feature", "c", depends_on=["feature.b"]),
            _make_action("feature", "a"),
            _make_action("feature", "b", depends_on=["feature.a"]),
        ]
        result = runner._topological_sort(actions)
        addrs = [a.resource.address for a in result]
        assert addrs == ["feature.a", "feature.b", "feature.c"]

    def test_diamond_dag(self):
        """
        A → B → D
        A → C → D
        """
        runner = self._make_runner()
        actions = [
            _make_action("feature", "d", depends_on=["feature.b", "feature.c"]),
            _make_action("feature", "b", depends_on=["feature.a"]),
            _make_action("feature", "c", depends_on=["feature.a"]),
            _make_action("feature", "a"),
        ]
        result = runner._topological_sort(actions)
        addrs = [a.resource.address for a in result]
        # A must come first, D must come last, B and C in between
        assert addrs[0] == "feature.a"
        assert addrs[-1] == "feature.d"
        assert set(addrs[1:3]) == {"feature.b", "feature.c"}

    def test_satisfied_dependencies_ignored(self):
        """Dependencies already in state are ignored."""
        state = State()
        state.set(_make_resource("feature", "a", ResourceStatus.IMPLEMENTED))
        runner = self._make_runner(state)
        actions = [
            _make_action("feature", "b", depends_on=["feature.a"]),
            _make_action("feature", "c", depends_on=["feature.a"]),
        ]
        result = runner._topological_sort(actions)
        # Both should be in result (no ordering constraint between them)
        addrs = [a.resource.address for a in result]
        assert len(addrs) == 2
        assert set(addrs) == {"feature.b", "feature.c"}

    def test_external_dependencies_ignored(self):
        """Dependencies not in the action set are ignored."""
        runner = self._make_runner()
        actions = [
            _make_action("feature", "b", depends_on=["feature.external"]),
        ]
        result = runner._topological_sort(actions)
        assert len(result) == 1
        assert result[0].resource.address == "feature.b"

    def test_cycle_detection(self):
        """A → B → A should raise."""
        runner = self._make_runner()
        actions = [
            _make_action("feature", "a", depends_on=["feature.b"]),
            _make_action("feature", "b", depends_on=["feature.a"]),
        ]
        with pytest.raises(CyclicDependencyError) as exc_info:
            runner._topological_sort(actions)
        assert "cycle" in str(exc_info.value).lower()

    def test_three_way_cycle(self):
        """A → B → C → A"""
        runner = self._make_runner()
        actions = [
            _make_action("feature", "a", depends_on=["feature.c"]),
            _make_action("feature", "b", depends_on=["feature.a"]),
            _make_action("feature", "c", depends_on=["feature.b"]),
        ]
        with pytest.raises(CyclicDependencyError):
            runner._topological_sort(actions)

    def test_single_action(self):
        runner = self._make_runner()
        actions = [_make_action("feature", "only")]
        result = runner._topological_sort(actions)
        assert len(result) == 1

    def test_empty_list(self):
        runner = self._make_runner()
        result = runner._topological_sort([])
        assert result == []


# ══════════════════════════════════════════════════════════════════════
# InteractiveMode Tests
# ══════════════════════════════════════════════════════════════════════

class TestInteractiveMode:
    def _make_mode(
        self, state: State | None = None, inputs: list[str] | None = None
    ) -> tuple[InteractiveMode, FakeStateManager]:
        sm = FakeStateManager(state)
        mode = InteractiveMode(state_manager=sm)
        if inputs:
            it = iter(inputs)
            mode._input_fn = lambda prompt="": next(it)
        return mode, sm

    def test_implement_single(self):
        mode, sm = self._make_mode(inputs=["i", "src/auth.py"])
        actions = [_make_action("feature", "auth")]
        result = mode.execute(actions)
        assert result.implemented == ["feature.auth"]
        assert sm._saved

    def test_skip_single(self):
        mode, sm = self._make_mode(inputs=["s"])
        actions = [_make_action("feature", "auth")]
        result = mode.execute(actions)
        assert result.skipped == ["feature.auth"]

    def test_quit_stops_loop(self):
        mode, sm = self._make_mode(inputs=["q"])
        actions = [
            _make_action("feature", "a"),
            _make_action("feature", "b"),
        ]
        result = mode.execute(actions)
        # Only first action processed before quit
        assert result.total <= 1

    def test_partial(self):
        mode, sm = self._make_mode(inputs=["p", "needs more work"])
        actions = [_make_action("feature", "wip")]
        result = mode.execute(actions)
        assert result.implemented == ["feature.wip"]  # partial counts as implemented

    def test_ai_assist_stub(self):
        mode, sm = self._make_mode(inputs=["a"])
        actions = [_make_action("feature", "ai_test")]
        result = mode.execute(actions)
        assert result.skipped == ["feature.ai_test"]

    def test_market_stub(self):
        mode, sm = self._make_mode(inputs=["m"])
        actions = [_make_action("feature", "market_test")]
        result = mode.execute(actions)
        assert result.market_pending == ["feature.market_test"]

    def test_multiple_actions(self):
        mode, sm = self._make_mode(
            inputs=["i", "a.py", "s", "i", "c.py"]
        )
        actions = [
            _make_action("feature", "a"),
            _make_action("feature", "b"),
            _make_action("feature", "c"),
        ]
        result = mode.execute(actions)
        assert result.implemented == ["feature.a", "feature.c"]
        assert result.skipped == ["feature.b"]

    def test_display_dependencies(self, capsys):
        state = State()
        state.set(_make_resource("module", "db", ResourceStatus.IMPLEMENTED))
        mode, sm = self._make_mode(state=state, inputs=["s"])
        actions = [
            _make_action("feature", "auth", depends_on=["module.db"]),
        ]
        mode.execute(actions)
        output = capsys.readouterr().out
        assert "module.db" in output
        assert "implemented" in output

    def test_display_attributes(self, capsys):
        mode, sm = self._make_mode(inputs=["s"])
        actions = [
            _make_action(
                "feature", "auth",
                attributes={"endpoints": ["/login", "/logout"]},
            ),
        ]
        mode.execute(actions)
        output = capsys.readouterr().out
        assert "endpoints" in output


# ══════════════════════════════════════════════════════════════════════
# Stub Mode Tests
# ══════════════════════════════════════════════════════════════════════

class TestImplementedModes:
    """Phase 5.1: Auto, Hybrid, and Market modes are now implemented."""

    def test_auto_mode_runs_empty(self):
        sm = FakeStateManager()
        mode = AutoMode(state_manager=sm, project_root="/tmp")
        result = mode.execute([])
        assert result.total == 0

    def test_hybrid_mode_runs_empty(self):
        sm = FakeStateManager()
        mode = HybridMode(state_manager=sm, project_root="/tmp")
        result = mode.execute([])
        assert result.total == 0

    def test_market_mode_runs_empty(self):
        sm = FakeStateManager()
        mode = MarketMode(state_manager=sm, project_root="/tmp")
        result = mode.execute([])
        assert result.total == 0


# ══════════════════════════════════════════════════════════════════════
# Verification Tests
# ══════════════════════════════════════════════════════════════════════

class TestVerification:
    def test_no_files_declared(self):
        resource = _make_resource("feature", "empty")
        result = verify_implementation(resource, "/tmp")
        assert result.passed is True
        assert result.score == 0.0
        assert "no files declared" in result.missing_attributes[0]

    def test_existing_files(self, tmp_path):
        # Create files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").write_text("class Auth: pass")
        (tmp_path / "src" / "login.py").write_text("def login(): ...")
        resource = _make_resource(
            "feature", "auth", files=["src/auth.py", "src/login.py"]
        )
        result = verify_implementation(resource, tmp_path)
        assert result.passed is True
        assert result.score == 1.0
        assert len(result.files_checked) == 2
        assert not result.missing_attributes

    def test_missing_files(self, tmp_path):
        resource = _make_resource("feature", "auth", files=["missing.py"])
        result = verify_implementation(resource, tmp_path)
        assert result.passed is False
        assert result.score == 0.0
        assert "file missing" in result.missing_attributes[0]

    def test_partial_files(self, tmp_path):
        (tmp_path / "exists.py").write_text("content")
        resource = _make_resource(
            "feature", "mixed", files=["exists.py", "missing.py"]
        )
        result = verify_implementation(resource, tmp_path)
        assert result.passed is False
        assert result.score == 0.5
        assert len(result.files_checked) == 2

    def test_empty_file_fails(self, tmp_path):
        (tmp_path / "empty.py").write_text("")
        resource = _make_resource("feature", "empty_file", files=["empty.py"])
        result = verify_implementation(resource, tmp_path)
        assert result.passed is False
        assert result.score == 0.0

    def test_attribute_files_included(self, tmp_path):
        (tmp_path / "attr.py").write_text("# from attributes")
        resource = _make_resource(
            "feature", "attr_test",
            attributes={"files": ["attr.py"]},
        )
        result = verify_implementation(resource, tmp_path)
        assert result.passed is True
        assert result.score == 1.0

    def test_summary_format(self):
        r = VerificationResult(passed=True, score=0.85, files_checked=["a", "b"])
        s = r.summary()
        assert "PASS" in s
        assert "85%" in s

    def test_fail_summary(self):
        r = VerificationResult(passed=False, score=0.5, missing_attributes=["x"])
        s = r.summary()
        assert "FAIL" in s


# ══════════════════════════════════════════════════════════════════════
# ApplyRunner Integration Tests
# ══════════════════════════════════════════════════════════════════════

class TestApplyRunnerIntegration:
    def test_dry_run_does_nothing(self):
        spec = Spec()
        spec.add(_make_resource("feature", "auth"))
        sm = FakeStateManager()
        config = ApplyConfig(dry_run=True)
        runner = ApplyRunner(spec, sm, config=config)
        result = runner.run()
        assert result.total == 0
        assert result.duration_seconds >= 0

    def test_no_changes_returns_empty(self):
        # State matches spec (both implemented)
        spec = Spec()
        spec.add(_make_resource("feature", "auth", ResourceStatus.MISSING))
        state = State()
        state.set(_make_resource("feature", "auth", ResourceStatus.IMPLEMENTED))
        sm = FakeStateManager(state)
        runner = ApplyRunner(spec, sm)
        result = runner.run()
        assert result.total == 0

    def test_filter_to_specific_resource(self):
        spec = Spec()
        spec.add(_make_resource("feature", "auth"))
        spec.add(_make_resource("feature", "logout"))
        sm = FakeStateManager()
        config = ApplyConfig(dry_run=True)
        runner = ApplyRunner(spec, sm, config=config)
        result = runner.run(resource="feature.auth")
        assert result.total == 0  # dry run
        # Just verifying it doesn't crash with filtering

    def test_filter_nonexistent_resource(self):
        spec = Spec()
        spec.add(_make_resource("feature", "auth"))
        sm = FakeStateManager()
        config = ApplyConfig(dry_run=True)
        runner = ApplyRunner(spec, sm, config=config)
        result = runner.run(resource="feature.nonexistent")
        assert result.total == 0


# ── Phase 5.2: Parallel Execution Tests ──────────────────────────────

class TestParallelExecution:
    """Tests for parallel execution engine."""

    def test_config_validation_max_workers(self):
        config = ApplyConfig(max_workers=0)
        errors = config.validate()
        assert "max_workers must be >= 1" in "\n".join(errors)

        config = ApplyConfig(max_workers=4)
        errors = config.validate()
        assert not errors

    def test_parallel_execution_disabled_by_default(self):
        """With max_workers=1, should use sequential execution."""
        actions = [
            _make_action("feature", "a"),
            _make_action("feature", "b"),
        ]
        spec, state = _make_spec_and_state([a.resource for a in actions])
        sm = FakeStateManager(state)
        
        # Mock mode handler to track execution order
        execution_order = []
        
        class MockMode:
            def __init__(self, **kwargs):
                pass
            
            def execute(self, actions):
                # Record the addresses in order
                execution_order.extend([a.resource.address for a in actions])
                result = ApplyResult()
                result.implemented = [a.resource.address for a in actions]
                return result
        
        config = ApplyConfig(max_workers=1)  # Sequential
        runner = ApplyRunner(spec, sm, config=config)
        
        # Mock the mode handler
        with patch.object(runner, '_get_mode_handler', return_value=MockMode()):
            result = runner.run()
        
        assert len(execution_order) == 2
        assert result.total == 2

    def test_parallel_execution_enabled(self):
        """With max_workers>1, should use parallel execution."""
        actions = [
            _make_action("feature", "a"),
            _make_action("feature", "b"),  # No dependencies, can run in parallel
        ]
        spec, state = _make_spec_and_state([a.resource for a in actions])
        sm = FakeStateManager(state)
        
        config = ApplyConfig(max_workers=2)  # Parallel
        runner = ApplyRunner(spec, sm, config=config)
        
        # Mock mode handler
        execution_count = []
        
        class MockMode:
            def __init__(self, **kwargs):
                pass
                
            def execute(self, actions):
                execution_count.append(len(actions))
                result = ApplyResult()
                result.implemented = [a.resource.address for a in actions]
                return result
        
        with patch.object(runner, '_get_mode_handler', return_value=MockMode()):
            result = runner.run()
        
        # Should have executed single actions (parallel)
        assert all(count == 1 for count in execution_count)
        assert len(execution_count) == 2  # Two parallel executions
        assert result.total == 2

    def test_parallel_execution_respects_dependencies(self):
        """Dependencies should be respected even in parallel mode."""
        actions = [
            _make_action("feature", "auth"),
            _make_action("feature", "login", depends_on=["feature.auth"]),
            _make_action("feature", "logout", depends_on=["feature.auth"]),
        ]
        spec, state = _make_spec_and_state([a.resource for a in actions])
        sm = FakeStateManager(state)
        
        execution_order = []
        execution_lock = threading.Lock()
        
        class MockMode:
            def __init__(self, **kwargs):
                pass
                
            def execute(self, actions):
                with execution_lock:
                    execution_order.extend([a.resource.address for a in actions])
                
                result = ApplyResult()
                result.implemented = [a.resource.address for a in actions]
                return result
        
        config = ApplyConfig(max_workers=3)
        runner = ApplyRunner(spec, sm, config=config)
        
        with patch.object(runner, '_get_mode_handler', return_value=MockMode()):
            result = runner.run()
        
        # auth should execute first, then login/logout can execute in parallel
        assert execution_order[0] == "feature.auth"
        assert "feature.login" in execution_order[1:]
        assert "feature.logout" in execution_order[1:]
        assert result.total == 3

    def test_parallel_execution_failure_skips_dependents(self):
        """Failed resources should cause dependents to be skipped."""
        actions = [
            _make_action("feature", "base"),
            _make_action("feature", "depends_on_base", depends_on=["feature.base"]),
        ]
        spec, state = _make_spec_and_state([a.resource for a in actions])
        sm = FakeStateManager(state)
        
        class MockMode:
            def __init__(self, **kwargs):
                pass
                
            def execute(self, actions):
                result = ApplyResult()
                for action in actions:
                    if action.resource.address == "feature.base":
                        result.failed.append(action.resource.address)
                    else:
                        result.implemented.append(action.resource.address)
                return result
        
        config = ApplyConfig(max_workers=2)
        runner = ApplyRunner(spec, sm, config=config)
        
        with patch.object(runner, '_get_mode_handler', return_value=MockMode()):
            result = runner.run()
        
        assert "feature.base" in result.failed
        assert "feature.depends_on_base" in result.skipped
        assert len(result.implemented) == 0

    def test_transitive_dependency_detection(self):
        """Test _depends_on_transitively helper."""
        actions = [
            _make_action("feature", "a"),
            _make_action("feature", "b", depends_on=["feature.a"]),
            _make_action("feature", "c", depends_on=["feature.b"]),
            _make_action("feature", "d"),  # Independent
        ]
        spec, state = _make_spec_and_state([a.resource for a in actions])
        sm = FakeStateManager(state)
        runner = ApplyRunner(spec, sm)
        
        dep_map = {
            "feature.a": set(),
            "feature.b": {"feature.a"},
            "feature.c": {"feature.b"},
            "feature.d": set(),
        }
        
        # Test direct dependency
        assert runner._depends_on_transitively("feature.b", "feature.a", dep_map)
        
        # Test transitive dependency
        assert runner._depends_on_transitively("feature.c", "feature.a", dep_map)
        
        # Test no dependency
        assert not runner._depends_on_transitively("feature.d", "feature.a", dep_map)
        
        # Test self-reference (should be False)
        assert not runner._depends_on_transitively("feature.a", "feature.a", dep_map)


# ── Phase 5.2: Git Diff Verification Tests ────────────────────────────

class TestGitDiffVerification:
    """Tests for git diff-based verification."""

    def test_verification_level_enum(self):
        """Test VerificationLevel enum values."""
        from terra4mice.apply.verify import VerificationLevel
        
        assert VerificationLevel.BASIC.value == "basic"
        assert VerificationLevel.GIT_DIFF.value == "git_diff"
        assert VerificationLevel.FULL.value == "full"

    def test_verification_result_with_git_info(self):
        """Test VerificationResult includes git diff information."""
        from terra4mice.apply.verify import VerificationResult, VerificationLevel
        
        result = VerificationResult(
            level=VerificationLevel.GIT_DIFF,
            git_changed_files=["src/auth.py", "tests/test_auth.py"],
            git_diff_stats="2 files changed, 15 insertions(+), 3 deletions(-)"
        )
        
        summary = result.summary()
        assert "level=git_diff" in summary
        assert "git_changed=2" in summary

    def test_basic_verification_still_works(self):
        """Test that BASIC verification still works as before."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            (Path(tmpdir) / "auth.py").write_text("def login(): pass")
            (Path(tmpdir) / "empty.py").write_text("")  # Empty file
            
            resource = _make_resource(
                "feature", "auth", 
                files=["auth.py", "missing.py", "empty.py"]
            )
            
            result = verify_implementation(resource, tmpdir)
            
            assert not result.passed  # missing.py doesn't exist, empty.py is empty
            assert result.score < 1.0
            assert "file missing or empty: missing.py" in result.missing_attributes
            assert "file missing or empty: empty.py" in result.missing_attributes

    def test_git_diff_verification_no_git(self):
        """Test git diff verification when git is not available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            (Path(tmpdir) / "auth.py").write_text("def login(): pass")
            
            resource = _make_resource("feature", "auth", files=["auth.py"])
            
            # Test in directory without git
            from terra4mice.apply.verify import VerificationLevel
            result = verify_implementation(resource, tmpdir, VerificationLevel.GIT_DIFF)
            
            assert not result.passed  # Should fail without git
            assert result.score == 0.0  # Git verification failed
            assert any("Git diff failed" in detail or "not a git repository" in detail.lower() 
                      for detail in result.verification_details)

    def test_git_diff_verification_no_changes(self):
        """Test git diff verification when no changes are detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmpdir, check=True)
            
            # Create and commit file
            (tmpdir_path / "auth.py").write_text("def login(): pass")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, check=True, capture_output=True)
            
            resource = _make_resource("feature", "auth", files=["auth.py"])
            
            from terra4mice.apply.verify import VerificationLevel
            result = verify_implementation(resource, tmpdir, VerificationLevel.GIT_DIFF)
            
            assert not result.passed  # Should fail - no changes in git diff
            assert result.level == VerificationLevel.GIT_DIFF
            assert any("no changes" in detail.lower() for detail in result.verification_details)

    def test_git_diff_verification_with_changes(self):
        """Test git diff verification when changes are detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmpdir, check=True)
            
            # Create and commit initial file
            (tmpdir_path / "auth.py").write_text("def login(): pass")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, check=True, capture_output=True)
            
            # Modify the file
            (tmpdir_path / "auth.py").write_text("def login(): pass\ndef logout(): pass")
            
            resource = _make_resource("feature", "auth", files=["auth.py"])
            
            from terra4mice.apply.verify import VerificationLevel
            result = verify_implementation(resource, tmpdir, VerificationLevel.GIT_DIFF)
            
            assert result.passed  # Should pass - changes detected
            assert result.level == VerificationLevel.GIT_DIFF
            assert "auth.py" in result.git_changed_files
            assert result.git_diff_stats is not None
            assert any("changes to expected files" in detail for detail in result.verification_details)

    def test_git_diff_verification_wrong_files_changed(self):
        """Test git diff verification when wrong files are changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmpdir, check=True)
            
            # Create and commit files
            (tmpdir_path / "auth.py").write_text("def login(): pass")
            (tmpdir_path / "other.py").write_text("def other(): pass")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, check=True, capture_output=True)
            
            # Modify the wrong file
            (tmpdir_path / "other.py").write_text("def other(): pass\ndef modified(): pass")
            
            # Resource expects auth.py to be changed
            resource = _make_resource("feature", "auth", files=["auth.py"])
            
            from terra4mice.apply.verify import VerificationLevel
            result = verify_implementation(resource, tmpdir, VerificationLevel.GIT_DIFF)
            
            assert not result.passed  # Should fail - wrong files changed
            assert "other.py" in result.git_changed_files
            assert "auth.py" not in result.git_changed_files
            assert any("shows changes to other.py but expected auth.py" in detail 
                      for detail in result.verification_details)

    def test_full_verification_level_fallback(self):
        """Test FULL verification level falls back to GIT_DIFF for now."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmpdir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmpdir, check=True)
            
            # Create and commit file
            (tmpdir_path / "auth.py").write_text("def login(): pass")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, check=True, capture_output=True)
            
            # Modify the file
            (tmpdir_path / "auth.py").write_text("def login(): pass\ndef logout(): pass")
            
            resource = _make_resource("feature", "auth", files=["auth.py"])
            
            from terra4mice.apply.verify import VerificationLevel
            result = verify_implementation(resource, tmpdir, VerificationLevel.FULL)
            
            assert result.level == VerificationLevel.FULL
            assert any("FULL verification not yet implemented" in detail 
                      for detail in result.verification_details)
