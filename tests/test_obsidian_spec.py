"""
Tests for ObsidianSpecLoader - Loading spec from Obsidian vault notes.

Covers:
- Loading resources from vault folder structure
- Resource type resolution (frontmatter > folder > default)
- Resource name resolution (frontmatter > file stem)
- Dependencies from frontmatter and wikilinks
- Filtering non-spec notes and system files
- Empty and nonexistent vaults
- Wikilink extraction and deduplication
- CLI --spec-source and --vault flags
"""

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path

import pytest
import yaml

from terra4mice.spec_parser import (
    load_spec_from_obsidian,
    _parse_obsidian_frontmatter,
    _read_obsidian_body,
    _extract_wikilink_dependencies,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_vault_note(path, frontmatter, body=""):
    """Helper to write a mock vault note with frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_str = yaml.dump(frontmatter, sort_keys=False, default_flow_style=False).strip()
    content = f"---\n{yaml_str}\n---\n\n{body}\n"
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# TestObsidianSpecLoader
# ---------------------------------------------------------------------------

class TestObsidianSpecLoader:
    def test_load_from_folders(self, tmp_path):
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True},
        )
        _write_vault_note(
            base / "module" / "planner.md",
            {"terra4mice": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        assert len(spec.list()) == 2
        addresses = {r.address for r in spec.list()}
        assert "feature.auth" in addresses
        assert "module.planner" in addresses

    def test_type_from_frontmatter_field(self, tmp_path):
        """type: field in frontmatter overrides folder name."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "health.md",
            {"terra4mice": True, "type": "endpoint"},
        )

        spec = load_spec_from_obsidian(tmp_path)
        r = spec.list()[0]
        assert r.type == "endpoint"
        assert r.address == "endpoint.health"

    def test_type_from_folder(self, tmp_path):
        """Without type: field, parent folder becomes the type."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "module" / "core.md",
            {"terra4mice": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        r = spec.list()[0]
        assert r.type == "module"

    def test_type_default_feature(self, tmp_path):
        """Notes at root subfolder default to type 'feature'."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "standalone.md",
            {"terra4mice": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        r = spec.list()[0]
        assert r.type == "feature"

    def test_name_from_frontmatter(self, tmp_path):
        """name: field in frontmatter overrides file stem."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "old_name.md",
            {"terra4mice": True, "name": "new_name"},
        )

        spec = load_spec_from_obsidian(tmp_path)
        r = spec.list()[0]
        assert r.name == "new_name"

    def test_name_from_stem(self, tmp_path):
        """Without name: field, file stem is used."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "payments.md",
            {"terra4mice": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        r = spec.list()[0]
        assert r.name == "payments"

    def test_depends_on_from_frontmatter(self, tmp_path):
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True},
        )
        _write_vault_note(
            base / "feature" / "dashboard.md",
            {"terra4mice": True, "depends_on": ["feature.auth"]},
        )

        spec = load_spec_from_obsidian(tmp_path)
        dashboard = spec.get("feature.dashboard")
        assert "feature.auth" in dashboard.depends_on

    def test_depends_on_from_wikilinks(self, tmp_path):
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "module" / "core.md",
            {"terra4mice": True},
        )
        _write_vault_note(
            base / "feature" / "search.md",
            {"terra4mice": True},
            body="This depends on [[module/core]] for indexing.",
        )

        spec = load_spec_from_obsidian(tmp_path)
        search = spec.get("feature.search")
        assert "module.core" in search.depends_on

    def test_depends_on_combined_no_duplicates(self, tmp_path):
        """Frontmatter + wikilink deps are merged without duplicates."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "module" / "core.md",
            {"terra4mice": True},
        )
        _write_vault_note(
            base / "feature" / "search.md",
            {"terra4mice": True, "depends_on": ["module.core"]},
            body="Uses [[module/core]] internally.",
        )

        spec = load_spec_from_obsidian(tmp_path)
        search = spec.get("feature.search")
        assert search.depends_on.count("module.core") == 1

    def test_non_spec_notes_ignored(self, tmp_path):
        """Notes without terra4mice: true are skipped."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True},
        )
        # Note without the flag
        _write_vault_note(
            base / "feature" / "random.md",
            {"title": "Just my notes"},
        )

        spec = load_spec_from_obsidian(tmp_path)
        assert len(spec.list()) == 1
        assert spec.list()[0].name == "auth"

    def test_system_files_ignored(self, tmp_path):
        """_index.md and _-prefixed dirs are skipped."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "_index.md",
            {"terra4mice_index": True},
        )
        _write_vault_note(
            base / "_templates" / "resource.md",
            {"terra4mice": True},
        )
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        assert len(spec.list()) == 1
        assert spec.list()[0].name == "auth"

    def test_empty_vault(self, tmp_path):
        """Empty vault subfolder returns empty Spec."""
        (tmp_path / "terra4mice").mkdir()
        spec = load_spec_from_obsidian(tmp_path)
        assert len(spec.list()) == 0

    def test_nonexistent_vault(self, tmp_path):
        """Missing vault subfolder raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_spec_from_obsidian(tmp_path / "nonexistent")

    def test_terra4mice_spec_flag(self, tmp_path):
        """terra4mice_spec: true also marks a note as spec source."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "api.md",
            {"terra4mice_spec": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        assert len(spec.list()) == 1
        assert spec.list()[0].name == "api"

    def test_attributes_from_frontmatter(self, tmp_path):
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True, "attributes": {"description": "Auth module", "endpoints": ["/login"]}},
        )

        spec = load_spec_from_obsidian(tmp_path)
        r = spec.list()[0]
        assert r.attributes["description"] == "Auth module"
        assert r.attributes["endpoints"] == ["/login"]

    def test_all_resources_have_missing_status(self, tmp_path):
        """Spec resources should always have MISSING status (desired state)."""
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True},
        )

        spec = load_spec_from_obsidian(tmp_path)
        from terra4mice.models import ResourceStatus
        assert spec.list()[0].status == ResourceStatus.MISSING


# ---------------------------------------------------------------------------
# TestWikilinkExtraction
# ---------------------------------------------------------------------------

class TestWikilinkExtraction:
    def test_simple_link(self):
        body = "Depends on [[feature/auth]] for login."
        deps = _extract_wikilink_dependencies(body, {"feature.auth"})
        assert deps == ["feature.auth"]

    def test_display_text(self):
        """[[feature/auth|Auth Module]] extracts feature.auth."""
        body = "Uses [[feature/auth|Auth Module]] internally."
        deps = _extract_wikilink_dependencies(body, {"feature.auth"})
        assert deps == ["feature.auth"]

    def test_only_known_addresses(self):
        """Unknown wikilinks are filtered out."""
        body = "See [[feature/auth]] and [[unknown/thing]]."
        deps = _extract_wikilink_dependencies(body, {"feature.auth"})
        assert deps == ["feature.auth"]
        assert "unknown.thing" not in deps

    def test_dedup(self):
        """Same link appearing twice returns only one entry."""
        body = "First [[feature/auth]], then again [[feature/auth]]."
        deps = _extract_wikilink_dependencies(body, {"feature.auth"})
        assert deps == ["feature.auth"]

    def test_no_links(self):
        assert _extract_wikilink_dependencies("No links here.", set()) == []
        assert _extract_wikilink_dependencies("", set()) == []

    def test_multiple_links(self):
        body = "Uses [[feature/auth]] and [[module/core]] together."
        known = {"feature.auth", "module.core"}
        deps = _extract_wikilink_dependencies(body, known)
        assert set(deps) == {"feature.auth", "module.core"}


# ---------------------------------------------------------------------------
# TestFrontmatterParsing (spec_parser versions)
# ---------------------------------------------------------------------------

class TestObsidianFrontmatterParsing:
    def test_valid_frontmatter(self, tmp_path):
        note = tmp_path / "test.md"
        note.write_text("---\nfoo: bar\nnum: 42\n---\n\nBody.\n", encoding="utf-8")
        fm = _parse_obsidian_frontmatter(note)
        assert fm == {"foo": "bar", "num": 42}

    def test_no_frontmatter(self, tmp_path):
        note = tmp_path / "test.md"
        note.write_text("Plain markdown.\n", encoding="utf-8")
        assert _parse_obsidian_frontmatter(note) is None

    def test_broken_yaml(self, tmp_path):
        note = tmp_path / "test.md"
        note.write_text("---\n: [bad yaml\n---\n", encoding="utf-8")
        assert _parse_obsidian_frontmatter(note) is None

    def test_empty_frontmatter(self, tmp_path):
        note = tmp_path / "test.md"
        note.write_text("---\n---\nBody.\n", encoding="utf-8")
        assert _parse_obsidian_frontmatter(note) == {}

    def test_nonexistent_file(self, tmp_path):
        assert _parse_obsidian_frontmatter(tmp_path / "nope.md") is None


class TestObsidianBodyParsing:
    def test_read_body(self, tmp_path):
        note = tmp_path / "test.md"
        note.write_text("---\nfoo: bar\n---\n\nBody content.\n", encoding="utf-8")
        body = _read_obsidian_body(note)
        assert "Body content." in body

    def test_read_body_no_frontmatter(self, tmp_path):
        note = tmp_path / "test.md"
        note.write_text("Just plain text.\n", encoding="utf-8")
        body = _read_obsidian_body(note)
        assert "Just plain text." in body


# ---------------------------------------------------------------------------
# TestCLISpecSource
# ---------------------------------------------------------------------------

class TestCLISpecSource:
    def test_plan_with_obsidian_spec(self, tmp_path):
        """plan --spec-source obsidian --vault loads from vault."""
        # Create vault with one resource
        base = tmp_path / "terra4mice"
        _write_vault_note(
            base / "feature" / "auth.md",
            {"terra4mice": True},
        )

        # Create empty state
        state_path = tmp_path / "state.json"
        state_path.write_text('{"version": "1", "serial": 0, "resources": []}', encoding="utf-8")

        from terra4mice.cli import main

        old_argv = sys.argv
        old_stdout = sys.stdout
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec-source", "obsidian",
                "--vault", str(tmp_path),
                "--state", str(state_path),
            ]
            sys.stdout = StringIO()
            result = main()
            output = sys.stdout.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout

        # Should show the feature.auth resource needs to be created
        assert "feature.auth" in output

    def test_plan_obsidian_missing_vault(self, tmp_path):
        """--spec-source obsidian without --vault should error."""
        state_path = tmp_path / "state.json"
        state_path.write_text('{"version": "1", "serial": 0, "resources": []}', encoding="utf-8")

        from terra4mice.cli import main

        old_argv = sys.argv
        old_stdout = sys.stdout
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec-source", "obsidian",
                "--state", str(state_path),
            ]
            sys.stdout = StringIO()
            result = main()
            output = sys.stdout.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout

        assert result == 1
        assert "--vault" in output or "vault" in output.lower()

    def test_plan_yaml_default(self, tmp_path):
        """Normal plan works without --spec-source (defaults to yaml)."""
        # Create a minimal spec file
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            'version: "1"\nresources:\n  feature:\n    auth:\n      attributes: {}\n',
            encoding="utf-8",
        )
        state_path = tmp_path / "state.json"
        state_path.write_text('{"version": "1", "serial": 0, "resources": []}', encoding="utf-8")

        from terra4mice.cli import main

        old_argv = sys.argv
        old_stdout = sys.stdout
        try:
            sys.argv = [
                "terra4mice", "plan",
                "--spec", str(spec_path),
                "--state", str(state_path),
            ]
            sys.stdout = StringIO()
            result = main()
            output = sys.stdout.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout

        assert "feature.auth" in output
