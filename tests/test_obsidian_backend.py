"""
Tests for ObsidianBackend - Obsidian vault state storage.

Covers:
- Basic init, exists, backend_type, supports_locking
- Write creates directory structure, index, per-resource notes
- Read reassembles JSON state from vault notes
- Round-trip fidelity (write then read preserves all fields)
- Body preservation on update (user notes survive writes)
- Default body generation (headings, wikilinks, file lists)
- Deletion of removed resources (managed notes only)
- Unmanaged notes left untouched
- Empty directory cleanup
- Frontmatter parsing (valid, missing, broken YAML, empty, unicode)
- create_backend factory integration
"""

import json
import yaml
from pathlib import Path

import pytest

from terra4mice.backends import ObsidianBackend, create_backend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_json(resources=None, version="1", serial=5):
    """Build a state JSON blob matching terra4mice format."""
    state = {
        "version": version,
        "serial": serial,
        "resources": resources or [],
    }
    return json.dumps(state).encode("utf-8")


def _single_resource(
    rtype="feature",
    rname="auth",
    status="implemented",
    locked=False,
    source="auto",
    attributes=None,
    depends_on=None,
    files=None,
    tests=None,
):
    return {
        "type": rtype,
        "name": rname,
        "status": status,
        "locked": locked,
        "source": source,
        "attributes": attributes or {},
        "depends_on": depends_on or [],
        "files": files or [],
        "tests": tests or [],
    }


# ---------------------------------------------------------------------------
# TestObsidianBackendBasic
# ---------------------------------------------------------------------------

class TestObsidianBackendBasic:
    def test_init_paths(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path, subfolder="state")
        assert backend.vault_path == tmp_path
        assert backend.base_path == tmp_path / "state"

    def test_init_default_subfolder(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        assert backend.base_path == tmp_path / "terra4mice"

    def test_exists_false_when_empty(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        assert backend.exists() is False

    def test_exists_true_after_write(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        backend.write(_make_state_json())
        assert backend.exists() is True

    def test_backend_type(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        assert backend.backend_type == "obsidian"

    def test_supports_locking_false(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        assert backend.supports_locking is False


# ---------------------------------------------------------------------------
# TestObsidianBackendReadWrite
# ---------------------------------------------------------------------------

class TestObsidianBackendReadWrite:
    def test_write_creates_base_dir(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path, subfolder="mystate")
        backend.write(_make_state_json())
        assert (tmp_path / "mystate").is_dir()

    def test_write_creates_index(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        backend.write(_make_state_json(serial=7))
        index = tmp_path / "terra4mice" / "_index.md"
        assert index.exists()
        text = index.read_text(encoding="utf-8")
        assert "terra4mice_index: true" in text
        assert "serial: 7" in text

    def test_write_creates_resource_note(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(rtype="module", rname="planner", status="partial")
        backend.write(_make_state_json([res]))

        note = tmp_path / "terra4mice" / "module" / "planner.md"
        assert note.exists()
        text = note.read_text(encoding="utf-8")
        assert "terra4mice: true" in text
        assert "type: module" in text
        assert "name: planner" in text
        assert "status: partial" in text

    def test_write_creates_type_subdirectory(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(rtype="endpoint", rname="health")
        backend.write(_make_state_json([res]))
        assert (tmp_path / "terra4mice" / "endpoint").is_dir()

    def test_read_returns_none_when_no_index(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        assert backend.read() is None

    def test_read_reassembles_state(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        resources = [
            _single_resource(rtype="feature", rname="auth", status="implemented"),
            _single_resource(rtype="module", rname="cli", status="partial"),
        ]
        data_in = _make_state_json(resources, serial=10)
        backend.write(data_in)

        data_out = backend.read()
        assert data_out is not None
        state = json.loads(data_out)
        assert state["version"] == "1"
        assert state["serial"] == 10
        assert len(state["resources"]) == 2
        names = {r["name"] for r in state["resources"]}
        assert names == {"auth", "cli"}

    def test_roundtrip_preserves_all_fields(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(
            rtype="feature",
            rname="payments",
            status="implemented",
            locked=False,
            source="auto",
            attributes={"description": "Payment processing"},
            depends_on=["module.auth"],
            files=["src/payments.py"],
            tests=["tests/test_payments.py"],
        )
        data_in = _make_state_json([res], serial=3)
        backend.write(data_in)

        data_out = backend.read()
        state = json.loads(data_out)
        r = state["resources"][0]
        assert r["type"] == "feature"
        assert r["name"] == "payments"
        assert r["status"] == "implemented"
        assert r["attributes"] == {"description": "Payment processing"}
        assert r["depends_on"] == ["module.auth"]
        assert r["files"] == ["src/payments.py"]
        assert r["tests"] == ["tests/test_payments.py"]

    def test_optional_fields_omitted_when_default(self, tmp_path):
        """Locked=False, source=auto should not appear in frontmatter."""
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(locked=False, source="auto", attributes={}, depends_on=[], files=[], tests=[])
        backend.write(_make_state_json([res]))

        note = tmp_path / "terra4mice" / "feature" / "auth.md"
        text = note.read_text(encoding="utf-8")
        assert "locked:" not in text
        assert "source:" not in text


# ---------------------------------------------------------------------------
# TestObsidianBackendBody
# ---------------------------------------------------------------------------

class TestObsidianBackendBody:
    def test_body_preserved_on_update(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(rtype="feature", rname="auth")
        backend.write(_make_state_json([res], serial=1))

        # Simulate user adding notes to the body
        note = tmp_path / "terra4mice" / "feature" / "auth.md"
        text = note.read_text(encoding="utf-8")
        text += "\nMy custom notes about auth.\n"
        note.write_text(text, encoding="utf-8")

        # Write again with updated status
        res["status"] = "broken"
        backend.write(_make_state_json([res], serial=2))

        updated_text = note.read_text(encoding="utf-8")
        assert "My custom notes about auth." in updated_text
        assert "status: broken" in updated_text

    def test_default_body_has_heading(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(rtype="module", rname="planner")
        backend.write(_make_state_json([res]))

        note = tmp_path / "terra4mice" / "module" / "planner.md"
        text = note.read_text(encoding="utf-8")
        assert "# module.planner" in text

    def test_default_body_wikilinks_for_deps(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(depends_on=["module.auth", "feature.logging"])
        backend.write(_make_state_json([res]))

        note = tmp_path / "terra4mice" / "feature" / "auth.md"
        text = note.read_text(encoding="utf-8")
        assert "[[module/auth]]" in text
        assert "[[feature/logging]]" in text

    def test_default_body_lists_files(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        res = _single_resource(files=["src/auth.py", "src/auth_utils.py"])
        backend.write(_make_state_json([res]))

        note = tmp_path / "terra4mice" / "feature" / "auth.md"
        text = note.read_text(encoding="utf-8")
        assert "`src/auth.py`" in text
        assert "`src/auth_utils.py`" in text

    def test_index_body_contains_status_breakdown(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        resources = [
            _single_resource(rname="a", status="implemented"),
            _single_resource(rname="b", status="partial"),
            _single_resource(rname="c", status="implemented"),
        ]
        backend.write(_make_state_json(resources))

        index = tmp_path / "terra4mice" / "_index.md"
        text = index.read_text(encoding="utf-8")
        assert "Total resources: 3" in text
        assert "implemented: 2" in text
        assert "partial: 1" in text


# ---------------------------------------------------------------------------
# TestObsidianBackendDeletion
# ---------------------------------------------------------------------------

class TestObsidianBackendDeletion:
    def test_removed_resource_deletes_managed_note(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        resources = [
            _single_resource(rname="auth"),
            _single_resource(rname="payments"),
        ]
        backend.write(_make_state_json(resources))
        assert (tmp_path / "terra4mice" / "feature" / "payments.md").exists()

        # Remove payments from state
        backend.write(_make_state_json([_single_resource(rname="auth")]))
        assert not (tmp_path / "terra4mice" / "feature" / "payments.md").exists()
        assert (tmp_path / "terra4mice" / "feature" / "auth.md").exists()

    def test_unmanaged_notes_untouched(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        backend.write(_make_state_json([_single_resource()]))

        # Create an unmanaged note in the same directory
        unmanaged = tmp_path / "terra4mice" / "feature" / "my_notes.md"
        unmanaged.write_text("# My personal notes\n", encoding="utf-8")

        # Write again - unmanaged note should survive
        backend.write(_make_state_json([_single_resource()]))
        assert unmanaged.exists()
        assert unmanaged.read_text(encoding="utf-8") == "# My personal notes\n"

    def test_empty_dirs_cleaned_up(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        resources = [
            _single_resource(rtype="endpoint", rname="health"),
        ]
        backend.write(_make_state_json(resources))
        assert (tmp_path / "terra4mice" / "endpoint").is_dir()

        # Remove the only resource in that type dir
        backend.write(_make_state_json([]))
        assert not (tmp_path / "terra4mice" / "endpoint").exists()


# ---------------------------------------------------------------------------
# TestFrontmatterParsing
# ---------------------------------------------------------------------------

class TestFrontmatterParsing:
    def test_valid_frontmatter(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        note = tmp_path / "test.md"
        note.write_text("---\nfoo: bar\nnum: 42\n---\n\nBody.\n", encoding="utf-8")
        fm = backend._read_frontmatter(note)
        assert fm == {"foo": "bar", "num": 42}

    def test_no_frontmatter_returns_none(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        note = tmp_path / "test.md"
        note.write_text("Just a plain markdown file.\n", encoding="utf-8")
        fm = backend._read_frontmatter(note)
        assert fm is None

    def test_broken_yaml_returns_none(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        note = tmp_path / "test.md"
        note.write_text("---\n: [invalid yaml\n---\n", encoding="utf-8")
        fm = backend._read_frontmatter(note)
        assert fm is None

    def test_empty_frontmatter_returns_empty_dict(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        note = tmp_path / "test.md"
        note.write_text("---\n---\n\nBody.\n", encoding="utf-8")
        fm = backend._read_frontmatter(note)
        assert fm == {}

    def test_unicode_content(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        note = tmp_path / "test.md"
        note.write_text("---\ntitle: Integracion\ndesc: Modulo de pago\n---\n", encoding="utf-8")
        fm = backend._read_frontmatter(note)
        assert fm["title"] == "Integracion"
        assert fm["desc"] == "Modulo de pago"

    def test_nonexistent_file_returns_none(self, tmp_path):
        backend = ObsidianBackend(vault_path=tmp_path)
        fm = backend._read_frontmatter(tmp_path / "does_not_exist.md")
        assert fm is None


# ---------------------------------------------------------------------------
# TestCreateBackendFactory
# ---------------------------------------------------------------------------

class TestCreateBackendFactory:
    def test_create_obsidian_backend(self, tmp_path):
        config = {
            "type": "obsidian",
            "config": {
                "vault_path": str(tmp_path),
                "subfolder": "my_state",
            },
        }
        backend = create_backend(backend_config=config)
        assert isinstance(backend, ObsidianBackend)
        assert backend.vault_path == Path(str(tmp_path))
        assert backend.base_path == Path(str(tmp_path)) / "my_state"

    def test_create_obsidian_default_subfolder(self, tmp_path):
        config = {
            "type": "obsidian",
            "config": {"vault_path": str(tmp_path)},
        }
        backend = create_backend(backend_config=config)
        assert backend.base_path == Path(str(tmp_path)) / "terra4mice"

    def test_create_obsidian_missing_vault_path_raises(self):
        config = {
            "type": "obsidian",
            "config": {},
        }
        with pytest.raises(ValueError, match="vault_path"):
            create_backend(backend_config=config)
