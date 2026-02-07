"""Tests for CLI context commands."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from terra4mice.cli import (
    cmd_contexts_list,
    cmd_contexts_show,
    cmd_contexts_sync,
    cmd_contexts_export,
    cmd_contexts_import,
    cmd_mark,
    DEFAULT_CONTEXTS_FILE,
)
from terra4mice.contexts import ContextRegistry
from terra4mice.state_manager import StateManager


class MockArgs:
    """Mock argparse namespace for testing."""
    
    def __init__(self, **kwargs):
        self.spec = None
        self.state = None
        self.contexts = None
        self.verbose = False
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.fixture
def temp_dir():
    """Create a temporary directory and change to it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            yield Path(tmpdir)
        finally:
            os.chdir(old_cwd)


@pytest.fixture
def initialized_project(temp_dir):
    """Initialize a terra4mice project in temp dir."""
    # Create minimal spec
    spec_content = """
version: "1.0"
project: test-project
resources:
  - type: module
    name: test
    files: ["src/test.py"]
  - type: module
    name: other
    files: ["src/other.py"]
"""
    (temp_dir / "terra4mice.spec.yaml").write_text(spec_content)
    
    # Create empty state
    sm = StateManager()
    sm.save()
    
    return temp_dir


class TestContextsList:
    """Tests for terra4mice contexts list."""
    
    def test_list_empty(self, temp_dir, capsys):
        """List with no contexts shows helpful message."""
        args = MockArgs()
        result = cmd_contexts_list(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "No contexts tracked" in captured.out
    
    def test_list_with_contexts(self, temp_dir, capsys):
        """List shows agents and their contexts."""
        # Create registry with data
        registry = ContextRegistry()
        registry.register_context(
            agent="claude-code",
            resource="module.test",
            files_touched=["src/test.py"],
        )
        registry.register_context(
            agent="cursor",
            resource="module.other",
            files_touched=["src/other.py"],
        )
        (temp_dir / DEFAULT_CONTEXTS_FILE).write_text(registry.to_json())
        
        args = MockArgs()
        result = cmd_contexts_list(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "claude-code" in captured.out
        assert "module.test" in captured.out
        assert "cursor" in captured.out
        assert "module.other" in captured.out
    
    def test_list_verbose(self, temp_dir, capsys):
        """Verbose list shows file details."""
        registry = ContextRegistry()
        registry.register_context(
            agent="claude-code",
            resource="module.test",
            files_touched=["src/test.py", "src/utils.py"],
            knowledge=["Uses dependency injection"],
        )
        (temp_dir / DEFAULT_CONTEXTS_FILE).write_text(registry.to_json())
        
        args = MockArgs(verbose=True)
        result = cmd_contexts_list(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "src/test.py" in captured.out
        assert "src/utils.py" in captured.out
        assert "dependency injection" in captured.out


class TestContextsShow:
    """Tests for terra4mice contexts show."""
    
    def test_show_not_found(self, temp_dir, capsys):
        """Show returns error for unknown agent."""
        args = MockArgs(agent="unknown")
        result = cmd_contexts_show(args)
        
        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out
    
    def test_show_agent(self, temp_dir, capsys):
        """Show displays agent details."""
        registry = ContextRegistry()
        registry.register_context(
            agent="claude-code",
            resource="module.test",
            files_touched=["src/test.py"],
            confidence=0.9,
        )
        (temp_dir / DEFAULT_CONTEXTS_FILE).write_text(registry.to_json())
        
        args = MockArgs(agent="claude-code")
        result = cmd_contexts_show(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "claude-code" in captured.out
        assert "module.test" in captured.out
        assert "0.9" in captured.out


class TestContextsExportImport:
    """Tests for context export/import."""
    
    def test_export(self, initialized_project, capsys):
        """Export creates valid handoff file."""
        temp_dir = initialized_project
        
        # Create registry with data
        registry = ContextRegistry()
        registry.register_context(
            agent="claude-code",
            resource="module.test",
            files_touched=["src/test.py"],
        )
        (temp_dir / DEFAULT_CONTEXTS_FILE).write_text(registry.to_json())
        
        args = MockArgs(
            agent="claude-code",
            output="handoff.json",
            project="test",
            notes="Test notes",
            recommend=None,
            to=None,
            include_state=False,
        )
        result = cmd_contexts_export(args)
        
        assert result == 0
        assert (temp_dir / "handoff.json").exists()
        
        # Verify content
        handoff = json.loads((temp_dir / "handoff.json").read_text())
        assert handoff["from_agent"] == "claude-code"
        assert "module.test" in handoff["resources"]
        assert handoff["notes"] == "Test notes"
    
    def test_import(self, temp_dir, capsys):
        """Import loads handoff into registry."""
        # Create handoff file
        handoff = {
            "format_version": "1.0",
            "from_agent": "claude-code",
            "resources": {
                "module.test": {
                    "status": "implemented",
                    "files": ["src/test.py"],
                    "knowledge": ["Important info"],
                    "confidence": 1.0,
                }
            },
            "notes": "Test handoff",
            "recommendations": ["Focus on tests"],
            "warnings": [],
        }
        (temp_dir / "handoff.json").write_text(json.dumps(handoff))
        
        args = MockArgs(
            input="handoff.json",
            agent="codex",
            strategy="merge",
            decay=0.1,
        )
        result = cmd_contexts_import(args)
        
        assert result == 0
        
        # Verify imported
        registry = ContextRegistry.from_json(
            (temp_dir / DEFAULT_CONTEXTS_FILE).read_text()
        )
        entries = registry.get_agent_contexts("codex")
        assert len(entries) == 1
        assert entries[0].resource == "module.test"


class TestContextsSync:
    """Tests for context sync."""
    
    def test_sync(self, initialized_project, capsys):
        """Sync transfers context between agents."""
        temp_dir = initialized_project
        
        # Create registry with source agent
        registry = ContextRegistry()
        registry.register_context(
            agent="claude-code",
            resource="module.test",
            files_touched=["src/test.py"],
            knowledge=["Uses pytest"],
        )
        (temp_dir / DEFAULT_CONTEXTS_FILE).write_text(registry.to_json())
        
        args = MockArgs(
            from_agent="claude-code",
            to_agent="cursor",
            resources=None,
            decay=0.1,
        )
        result = cmd_contexts_sync(args)
        
        assert result == 0
        
        # Verify synced
        registry = ContextRegistry.from_json(
            (temp_dir / DEFAULT_CONTEXTS_FILE).read_text()
        )
        cursor_entries = registry.get_agent_contexts("cursor")
        assert len(cursor_entries) == 1
        assert cursor_entries[0].resource == "module.test"
        assert cursor_entries[0].confidence == 0.9  # decayed


class TestMarkWithAgent:
    """Tests for mark command with --agent flag."""
    
    def test_mark_tracks_context(self, initialized_project, capsys):
        """Mark with --agent tracks context."""
        temp_dir = initialized_project
        
        args = MockArgs(
            address="module.test",
            status="implemented",
            files="src/test.py",
            tests="",
            reason="",
            lock=False,
            agent="claude-code",
        )
        result = cmd_mark(args)
        
        assert result == 0
        
        # Verify context tracked
        registry = ContextRegistry.from_json(
            (temp_dir / DEFAULT_CONTEXTS_FILE).read_text()
        )
        entries = registry.get_agent_contexts("claude-code")
        assert len(entries) == 1
        assert entries[0].resource == "module.test"
        assert "src/test.py" in entries[0].files_touched
    
    def test_mark_without_agent_no_tracking(self, initialized_project, capsys):
        """Mark without --agent doesn't track context."""
        temp_dir = initialized_project
        
        args = MockArgs(
            address="module.test",
            status="implemented",
            files="src/test.py",
            tests="",
            reason="",
            lock=False,
            agent=None,
        )
        
        # Clear any env vars that might auto-detect agent
        with patch.dict(os.environ, {}, clear=True):
            result = cmd_mark(args)
        
        assert result == 0
        
        # No contexts file should be created
        assert not (temp_dir / DEFAULT_CONTEXTS_FILE).exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
