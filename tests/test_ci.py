"""
Tests for terra4mice CI/CD integration.

Covers:
- JSON output format validation
- Markdown output rendering
- Convergence badge generation
- ANSI stripping
- Convergence calculation edge cases
- CLI integration (--format, --no-color, --ci flags)
- CI subcommand
"""

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from terra4mice.models import (
    Plan, PlanAction, Resource, ResourceStatus, Spec, State,
)
from terra4mice.ci import (
    format_plan_json,
    format_plan_markdown,
    format_convergence_badge,
    strip_ansi,
    _compute_convergence,
)
from terra4mice.planner import generate_plan, format_plan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_resource(rtype: str, name: str, status: ResourceStatus = ResourceStatus.MISSING) -> Resource:
    """Helper to create a Resource."""
    return Resource(type=rtype, name=name, status=status)


def _make_spec(*resources: Resource) -> Spec:
    """Build a Spec from resources."""
    spec = Spec()
    for r in resources:
        spec.add(r)
    return spec


def _make_state(*resources: Resource) -> State:
    """Build a State from resources."""
    state = State()
    for r in resources:
        state.set(r)
    return state


@pytest.fixture
def mixed_scenario():
    """
    Scenario with a mix of implemented, partial, and missing resources.

    3 in spec:
      - feature.auth  → implemented in state
      - module.core   → partial in state
      - feature.search → missing from state
    """
    spec = _make_spec(
        _make_resource("feature", "auth"),
        _make_resource("module", "core"),
        _make_resource("feature", "search"),
    )
    state = _make_state(
        _make_resource("feature", "auth", ResourceStatus.IMPLEMENTED),
        _make_resource("module", "core", ResourceStatus.PARTIAL),
    )
    plan = generate_plan(spec, state)
    return plan, spec, state


@pytest.fixture
def all_implemented():
    """Scenario where everything is implemented (100% convergence)."""
    spec = _make_spec(
        _make_resource("feature", "auth"),
        _make_resource("feature", "users"),
    )
    state = _make_state(
        _make_resource("feature", "auth", ResourceStatus.IMPLEMENTED),
        _make_resource("feature", "users", ResourceStatus.IMPLEMENTED),
    )
    plan = generate_plan(spec, state)
    return plan, spec, state


@pytest.fixture
def empty_scenario():
    """Scenario with empty spec and state."""
    spec = Spec()
    state = State()
    plan = generate_plan(spec, state)
    return plan, spec, state


@pytest.fixture
def all_missing():
    """Scenario where everything is missing (0% convergence)."""
    spec = _make_spec(
        _make_resource("feature", "auth"),
        _make_resource("feature", "users"),
        _make_resource("module", "core"),
    )
    state = State()
    plan = generate_plan(spec, state)
    return plan, spec, state


@pytest.fixture
def with_deletions():
    """Scenario with extra resources in state not in spec."""
    spec = _make_spec(
        _make_resource("feature", "auth"),
    )
    state = _make_state(
        _make_resource("feature", "auth", ResourceStatus.IMPLEMENTED),
        _make_resource("feature", "legacy", ResourceStatus.IMPLEMENTED),
    )
    plan = generate_plan(spec, state)
    return plan, spec, state


# ---------------------------------------------------------------------------
# Tests: strip_ansi
# ---------------------------------------------------------------------------

class TestStripAnsi:
    """Tests for ANSI code stripping."""

    def test_strips_color_codes(self):
        """Should remove standard ANSI color codes."""
        text = "\033[32mgreen\033[0m and \033[31mred\033[0m"
        assert strip_ansi(text) == "green and red"

    def test_strips_bold_codes(self):
        """Should remove bold/bright ANSI codes."""
        text = "\033[1;32mbold green\033[0m"
        assert strip_ansi(text) == "bold green"

    def test_no_ansi_unchanged(self):
        """Should not modify text without ANSI codes."""
        text = "plain text here"
        assert strip_ansi(text) == "plain text here"

    def test_empty_string(self):
        """Should handle empty strings."""
        assert strip_ansi("") == ""

    def test_strips_from_format_plan(self):
        """Should clean format_plan output."""
        spec = _make_spec(_make_resource("feature", "auth"))
        state = State()
        plan = generate_plan(spec, state)
        output = format_plan(plan)
        clean = strip_ansi(output)
        assert "\033[" not in clean
        assert "feature.auth" in clean


# ---------------------------------------------------------------------------
# Tests: Convergence Calculation
# ---------------------------------------------------------------------------

class TestConvergence:
    """Tests for convergence percentage computation."""

    def test_mixed_convergence(self, mixed_scenario):
        """Mixed scenario: 1 implemented + 1 partial + 1 missing = 50%."""
        _, spec, state = mixed_scenario
        stats = _compute_convergence(spec, state)
        # 1 * 100 + 1 * 50 + 1 * 0 = 150 / 3 = 50.0
        assert stats["convergence"] == 50.0
        assert stats["total_resources"] == 3
        assert stats["implemented"] == 1
        assert stats["partial"] == 1
        assert stats["missing"] == 1

    def test_all_implemented(self, all_implemented):
        """100% when all resources are implemented."""
        _, spec, state = all_implemented
        stats = _compute_convergence(spec, state)
        assert stats["convergence"] == 100.0
        assert stats["implemented"] == 2
        assert stats["missing"] == 0

    def test_all_missing(self, all_missing):
        """0% when nothing is implemented."""
        _, spec, state = all_missing
        stats = _compute_convergence(spec, state)
        assert stats["convergence"] == 0.0
        assert stats["missing"] == 3

    def test_empty_spec(self, empty_scenario):
        """Empty spec should report 100% convergence (vacuously true)."""
        _, spec, state = empty_scenario
        stats = _compute_convergence(spec, state)
        assert stats["convergence"] == 100.0
        assert stats["total_resources"] == 0

    def test_broken_counts_as_missing(self):
        """Broken resources should count as missing for convergence."""
        spec = _make_spec(_make_resource("feature", "auth"))
        state = _make_state(
            _make_resource("feature", "auth", ResourceStatus.BROKEN),
        )
        stats = _compute_convergence(spec, state)
        assert stats["convergence"] == 0.0
        assert stats["missing"] == 1


# ---------------------------------------------------------------------------
# Tests: JSON Output
# ---------------------------------------------------------------------------

class TestFormatPlanJson:
    """Tests for JSON output format."""

    def test_valid_json(self, mixed_scenario):
        """Output must be valid JSON."""
        plan, spec, state = mixed_scenario
        output = format_plan_json(plan, spec, state)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_schema_fields(self, mixed_scenario):
        """JSON must contain all required fields."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_plan_json(plan, spec, state))

        assert data["version"] == "1"
        assert "convergence" in data
        assert "total_resources" in data
        assert "implemented" in data
        assert "partial" in data
        assert "missing" in data
        assert "has_changes" in data
        assert "actions" in data

    def test_convergence_value(self, mixed_scenario):
        """Convergence should match computed value."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_plan_json(plan, spec, state))
        assert data["convergence"] == 50.0

    def test_has_changes_true(self, mixed_scenario):
        """has_changes should be true when there are pending actions."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_plan_json(plan, spec, state))
        assert data["has_changes"] is True

    def test_has_changes_false(self, all_implemented):
        """has_changes should be false when everything is done."""
        plan, spec, state = all_implemented
        data = json.loads(format_plan_json(plan, spec, state))
        assert data["has_changes"] is False

    def test_actions_list(self, mixed_scenario):
        """Actions should list non-no-op items."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_plan_json(plan, spec, state))
        actions = data["actions"]

        assert len(actions) >= 1
        for action in actions:
            assert "action" in action
            assert "address" in action
            assert "reason" in action
            assert action["action"] in ("create", "update", "delete")

    def test_no_noop_in_actions(self, mixed_scenario):
        """no-op actions should not appear in JSON output."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_plan_json(plan, spec, state))
        for action in data["actions"]:
            assert action["action"] != "no-op"

    def test_empty_spec_json(self, empty_scenario):
        """Empty spec should produce valid JSON with no actions."""
        plan, spec, state = empty_scenario
        data = json.loads(format_plan_json(plan, spec, state))
        assert data["total_resources"] == 0
        assert data["actions"] == []
        assert data["has_changes"] is False

    def test_resource_counts(self, mixed_scenario):
        """Resource counts should add up."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_plan_json(plan, spec, state))
        assert data["implemented"] + data["partial"] + data["missing"] == data["total_resources"]


# ---------------------------------------------------------------------------
# Tests: Markdown Output
# ---------------------------------------------------------------------------

class TestFormatPlanMarkdown:
    """Tests for Markdown output format."""

    def test_contains_header(self, mixed_scenario):
        """Output should contain the terra4mice header."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "## 🐭 terra4mice Plan" in md

    def test_contains_table(self, mixed_scenario):
        """Output should contain a markdown table."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "| Resource | Status | Action |" in md
        assert "|----------|--------|--------|" in md

    def test_status_icons(self, mixed_scenario):
        """Should use correct status icons."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "✅ implemented" in md
        assert "⚠️ partial" in md
        assert "❌ missing" in md

    def test_convergence_line(self, mixed_scenario):
        """Should include convergence percentage."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "**Convergence**:" in md
        assert "50.0%" in md

    def test_plan_summary(self, mixed_scenario):
        """Should include plan summary."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "> Plan:" in md

    def test_no_changes_message(self, all_implemented):
        """Should show no changes message when converged."""
        plan, spec, state = all_implemented
        md = format_plan_markdown(plan, spec, state)
        assert "No changes" in md

    def test_empty_spec_markdown(self, empty_scenario):
        """Empty spec should produce valid markdown."""
        plan, spec, state = empty_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "## 🐭 terra4mice Plan" in md
        assert "100.0%" in md

    def test_deletion_shown(self, with_deletions):
        """Deleted resources should appear in markdown."""
        plan, spec, state = with_deletions
        md = format_plan_markdown(plan, spec, state)
        assert "feature.legacy" in md
        assert "remove" in md

    def test_resource_addresses_present(self, mixed_scenario):
        """All spec resources should appear in the table."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "feature.auth" in md
        assert "module.core" in md
        assert "feature.search" in md

    def test_no_ansi_in_markdown(self, mixed_scenario):
        """Markdown output should not contain ANSI codes."""
        plan, spec, state = mixed_scenario
        md = format_plan_markdown(plan, spec, state)
        assert "\033[" not in md


# ---------------------------------------------------------------------------
# Tests: Convergence Badge
# ---------------------------------------------------------------------------

class TestFormatConvergenceBadge:
    """Tests for Shields.io badge JSON output."""

    def test_valid_json(self, mixed_scenario):
        """Badge output must be valid JSON."""
        plan, spec, state = mixed_scenario
        output = format_convergence_badge(plan, spec, state)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_schema_version(self, mixed_scenario):
        """Badge must have schemaVersion 1."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["schemaVersion"] == 1

    def test_label(self, mixed_scenario):
        """Badge label should be 'convergence'."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["label"] == "convergence"

    def test_message_format(self, mixed_scenario):
        """Message should be percentage."""
        plan, spec, state = mixed_scenario
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["message"].endswith("%")

    def test_green_for_high_convergence(self, all_implemented):
        """Should be green for >= 90%."""
        plan, spec, state = all_implemented
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["color"] == "brightgreen"

    def test_red_for_low_convergence(self, all_missing):
        """Should be red for < 50%."""
        plan, spec, state = all_missing
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["color"] == "red"

    def test_yellow_for_medium_convergence(self):
        """Should be yellow for 70-89%."""
        # 4 resources: 3 implemented, 1 missing = 75%
        spec = _make_spec(
            _make_resource("f", "a"),
            _make_resource("f", "b"),
            _make_resource("f", "c"),
            _make_resource("f", "d"),
        )
        state = _make_state(
            _make_resource("f", "a", ResourceStatus.IMPLEMENTED),
            _make_resource("f", "b", ResourceStatus.IMPLEMENTED),
            _make_resource("f", "c", ResourceStatus.IMPLEMENTED),
        )
        plan = generate_plan(spec, state)
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["color"] == "yellow"

    def test_orange_for_mid_convergence(self):
        """Should be orange for 50-69%."""
        # 2 resources: 1 implemented, 1 missing = 50%
        spec = _make_spec(
            _make_resource("f", "a"),
            _make_resource("f", "b"),
        )
        state = _make_state(
            _make_resource("f", "a", ResourceStatus.IMPLEMENTED),
        )
        plan = generate_plan(spec, state)
        data = json.loads(format_convergence_badge(plan, spec, state))
        assert data["color"] == "orange"


# ---------------------------------------------------------------------------
# Tests: CLI Integration
# ---------------------------------------------------------------------------

class TestCLIIntegration:
    """Tests for CLI flags and subcommands using file-based fixtures."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create a temporary workspace with spec and state files."""
        spec_data = {
            "version": "1",
            "resources": {
                "feature": {
                    "auth": {"attributes": {"description": "Auth"}},
                    "search": {"attributes": {"description": "Search"}},
                },
                "module": {
                    "core": {"attributes": {"description": "Core"}},
                },
            },
        }
        spec_file = tmp_path / "terra4mice.spec.yaml"
        spec_file.write_text(yaml.dump(spec_data), encoding="utf-8")

        state_data = {
            "version": "1",
            "serial": 1,
            "last_updated": None,
            "resources": [
                {
                    "type": "feature",
                    "name": "auth",
                    "status": "implemented",
                    "attributes": {},
                    "depends_on": [],
                    "files": [],
                    "tests": [],
                    "created_at": None,
                    "updated_at": None,
                },
                {
                    "type": "module",
                    "name": "core",
                    "status": "partial",
                    "attributes": {"partial_reason": "Missing tests"},
                    "depends_on": [],
                    "files": [],
                    "tests": [],
                    "created_at": None,
                    "updated_at": None,
                },
            ],
        }
        state_file = tmp_path / "terra4mice.state.json"
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        return tmp_path, str(spec_file), str(state_file)

    def test_plan_json_format(self, workspace):
        """plan --format json should produce valid JSON."""
        _, spec_file, state_file = workspace
        from terra4mice.cli import main
        import sys

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec", spec_file,
                "--state", state_file,
                "--format", "json",
            ]
            # Capture stdout
            import io
            from contextlib import redirect_stdout
            f = io.StringIO()
            with redirect_stdout(f):
                exit_code = main()

            output = f.getvalue()
            data = json.loads(output)
            assert data["version"] == "1"
            assert exit_code == 0
        finally:
            sys.argv = old_argv

    def test_plan_markdown_format(self, workspace):
        """plan --format markdown should produce markdown."""
        _, spec_file, state_file = workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec", spec_file,
                "--state", state_file,
                "--format", "markdown",
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                main()
            output = f.getvalue()
            assert "## 🐭 terra4mice Plan" in output
        finally:
            sys.argv = old_argv

    def test_plan_no_color(self, workspace):
        """plan --no-color should strip ANSI codes."""
        _, spec_file, state_file = workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec", spec_file,
                "--state", state_file,
                "--no-color",
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                main()
            output = f.getvalue()
            assert "\033[" not in output
        finally:
            sys.argv = old_argv

    def test_plan_ci_flag(self, workspace):
        """plan --ci should produce JSON and return exit code 2 when changes exist."""
        _, spec_file, state_file = workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec", spec_file,
                "--state", state_file,
                "--ci",
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                exit_code = main()

            output = f.getvalue()
            data = json.loads(output)
            assert data["has_changes"] is True
            assert exit_code == 2
        finally:
            sys.argv = old_argv

    def test_plan_detailed_exitcode_no_changes(self):
        """plan --detailed-exitcode should return 0 when no changes."""
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            # All implemented
            spec_data = {
                "version": "1",
                "resources": {
                    "feature": {
                        "auth": {"attributes": {}},
                    },
                },
            }
            spec_file = os.path.join(tmp, "spec.yaml")
            with open(spec_file, "w") as f:
                yaml.dump(spec_data, f)

            state_data = {
                "version": "1",
                "serial": 1,
                "last_updated": None,
                "resources": [
                    {
                        "type": "feature",
                        "name": "auth",
                        "status": "implemented",
                        "attributes": {},
                        "depends_on": [],
                        "files": [],
                        "tests": [],
                        "created_at": None,
                        "updated_at": None,
                    },
                ],
            }
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump(state_data, f)

            old_argv = sys.argv
            try:
                sys.argv = [
                    "terra4mice", "plan",
                    "--spec", spec_file,
                    "--state", state_file,
                    "--detailed-exitcode",
                ]
                buf = io.StringIO()
                with redirect_stdout(buf):
                    exit_code = main()
                assert exit_code == 0
            finally:
                sys.argv = old_argv


# ---------------------------------------------------------------------------
# Tests: CI Subcommand
# ---------------------------------------------------------------------------

class TestCISubcommand:
    """Tests for the 'ci' subcommand."""

    @pytest.fixture
    def ci_workspace(self, tmp_path):
        """Create workspace for CI tests."""
        spec_data = {
            "version": "1",
            "resources": {
                "feature": {
                    "auth": {"attributes": {}},
                    "search": {"attributes": {}},
                },
            },
        }
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_data), encoding="utf-8")

        state_data = {
            "version": "1",
            "serial": 1,
            "last_updated": None,
            "resources": [
                {
                    "type": "feature",
                    "name": "auth",
                    "status": "implemented",
                    "attributes": {},
                    "depends_on": [],
                    "files": [],
                    "tests": [],
                    "created_at": None,
                    "updated_at": None,
                },
            ],
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        return tmp_path, str(spec_file), str(state_file)

    def test_ci_json_output(self, ci_workspace):
        """ci subcommand should produce JSON by default."""
        tmp_path, spec_file, state_file = ci_workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "ci",
                "--spec", spec_file,
                "--state", state_file,
                "--root", str(tmp_path),
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                main()
            output = f.getvalue()
            data = json.loads(output)
            assert data["version"] == "1"
        finally:
            sys.argv = old_argv

    def test_ci_output_file(self, ci_workspace):
        """ci --output should write to file."""
        tmp_path, spec_file, state_file = ci_workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        output_file = str(tmp_path / "plan.json")
        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "ci",
                "--spec", spec_file,
                "--state", state_file,
                "--root", str(tmp_path),
                "--output", output_file,
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                main()

            assert Path(output_file).exists()
            data = json.loads(Path(output_file).read_text())
            assert "convergence" in data
        finally:
            sys.argv = old_argv

    def test_ci_comment_file(self, ci_workspace):
        """ci --comment should write markdown to file."""
        tmp_path, spec_file, state_file = ci_workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        comment_file = str(tmp_path / "comment.md")
        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "ci",
                "--spec", spec_file,
                "--state", state_file,
                "--root", str(tmp_path),
                "--comment", comment_file,
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                main()

            assert Path(comment_file).exists()
            content = Path(comment_file).read_text()
            assert "🐭 terra4mice Plan" in content
        finally:
            sys.argv = old_argv

    def test_ci_fail_on_incomplete(self, ci_workspace):
        """ci --fail-on-incomplete should exit 2 when convergence < 100%."""
        _, spec_file, state_file = ci_workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "ci",
                "--spec", spec_file,
                "--state", state_file,
                "--root", "/nonexistent",
                "--fail-on-incomplete",
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                exit_code = main()
            assert exit_code == 2
        finally:
            sys.argv = old_argv

    def test_ci_fail_under(self, ci_workspace):
        """ci --fail-under should exit 2 when convergence < threshold."""
        _, spec_file, state_file = ci_workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "ci",
                "--spec", spec_file,
                "--state", state_file,
                "--root", "/nonexistent",
                "--fail-under", "80",
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                exit_code = main()
            # 1/2 implemented = 50%, which is below 80%
            assert exit_code == 2
        finally:
            sys.argv = old_argv

    def test_ci_fail_under_passes(self):
        """ci --fail-under should exit 0 when convergence >= threshold."""
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            spec_data = {
                "version": "1",
                "resources": {
                    "feature": {
                        "auth": {"attributes": {}},
                    },
                },
            }
            spec_file = os.path.join(tmp, "spec.yaml")
            with open(spec_file, "w") as f:
                yaml.dump(spec_data, f)

            state_data = {
                "version": "1",
                "serial": 1,
                "last_updated": None,
                "resources": [
                    {
                        "type": "feature",
                        "name": "auth",
                        "status": "implemented",
                        "attributes": {},
                        "depends_on": [],
                        "files": [],
                        "tests": [],
                        "created_at": None,
                        "updated_at": None,
                    },
                ],
            }
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump(state_data, f)

            old_argv = sys.argv
            try:
                sys.argv = [
                    "terra4mice", "ci",
                    "--spec", spec_file,
                    "--state", state_file,
                    "--root", "/nonexistent",
                    "--fail-under", "50",
                ]
                buf = io.StringIO()
                with redirect_stdout(buf):
                    exit_code = main()
                assert exit_code == 0
            finally:
                sys.argv = old_argv

    def test_ci_markdown_format(self, ci_workspace):
        """ci --format markdown should produce markdown output."""
        tmp_path, spec_file, state_file = ci_workspace
        from terra4mice.cli import main
        import sys
        import io
        from contextlib import redirect_stdout

        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "ci",
                "--spec", spec_file,
                "--state", state_file,
                "--root", str(tmp_path),
                "--format", "markdown",
            ]
            f = io.StringIO()
            with redirect_stdout(f):
                main()
            output = f.getvalue()
            assert "## 🐭 terra4mice Plan" in output
        finally:
            sys.argv = old_argv
