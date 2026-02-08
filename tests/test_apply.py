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
import tempfile
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
