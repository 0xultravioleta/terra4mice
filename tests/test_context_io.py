"""
Tests for terra4mice context_io module.

Tests the context handoff, export, import, sync, and conflict detection
functionality for multi-AI collaboration.
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from terra4mice.contexts import (
    AgentProfile,
    ContextEntry,
    ContextRegistry,
    ContextStatus,
)
from terra4mice.context_io import (
    ContextHandoff,
    ImportResult,
    MergeStrategy,
    detect_conflicts,
    export_agent_context,
    export_resource_context,
    format_conflict_warning,
    import_handoff,
    sync_contexts,
)
from terra4mice.models import Resource, ResourceStatus, State


# ========== Fixtures ==========


@pytest.fixture
def empty_registry():
    """Create an empty context registry."""
    return ContextRegistry()


@pytest.fixture
def registry_with_contexts():
    """Create a registry with some pre-populated contexts."""
    registry = ContextRegistry()
    
    # Register agents
    registry.register_agent(AgentProfile(
        id="claude-code",
        name="Claude Code",
        model="claude-opus-4",
        platform="openclaw",
    ))
    registry.register_agent(AgentProfile(
        id="codex",
        name="Codex",
        model="codex-2025",
        platform="github",
    ))
    
    # Add contexts
    registry.register_context(
        agent="claude-code",
        resource="module.inference",
        files_touched=["src/inference.py", "src/utils.py"],
        knowledge=["Uses tree-sitter for parsing", "5-level fallback system"],
        confidence=0.95,
    )
    registry.register_context(
        agent="claude-code",
        resource="module.analyzers",
        files_touched=["src/analyzers.py"],
        knowledge=["Complex regex patterns"],
        confidence=0.8,
    )
    registry.register_context(
        agent="codex",
        resource="module.cli",
        files_touched=["src/cli.py", "src/main.py"],
        knowledge=["Uses click library"],
        confidence=0.9,
    )
    
    return registry


@pytest.fixture
def sample_state():
    """Create a sample state with resources."""
    state = State()
    
    state.set(Resource(
        type="module",
        name="inference",
        status=ResourceStatus.IMPLEMENTED,
        files=["src/inference.py", "src/utils.py"],
    ))
    state.set(Resource(
        type="module",
        name="analyzers",
        status=ResourceStatus.PARTIAL,
        files=["src/analyzers.py"],
    ))
    state.set(Resource(
        type="module",
        name="cli",
        status=ResourceStatus.IMPLEMENTED,
        files=["src/cli.py"],
    ))
    
    return state


# ========== MergeStrategy Tests ==========


class TestMergeStrategy:
    """Tests for MergeStrategy enum."""
    
    def test_merge_strategy_values(self):
        """Test that all expected values exist."""
        assert MergeStrategy.MERGE.value == "merge"
        assert MergeStrategy.REPLACE.value == "replace"
        assert MergeStrategy.SKIP_EXISTING.value == "skip_existing"
    
    def test_merge_strategy_from_string(self):
        """Test creating strategy from string value."""
        assert MergeStrategy("merge") == MergeStrategy.MERGE
        assert MergeStrategy("replace") == MergeStrategy.REPLACE
        assert MergeStrategy("skip_existing") == MergeStrategy.SKIP_EXISTING


# ========== ContextHandoff Tests ==========


class TestContextHandoff:
    """Tests for ContextHandoff dataclass."""
    
    def test_create_empty_handoff(self):
        """Test creating a handoff with minimal info."""
        handoff = ContextHandoff(from_agent="test-agent")
        
        assert handoff.from_agent == "test-agent"
        assert handoff.format_version == "1.0"
        assert handoff.resources == {}
        assert handoff.recommendations == []
        assert handoff.warnings == []
        assert handoff.created_at is not None
    
    def test_create_full_handoff(self):
        """Test creating a handoff with all fields."""
        now = datetime.now()
        handoff = ContextHandoff(
            format_version="1.0",
            created_at=now,
            from_agent="claude-code",
            from_model="claude-opus-4",
            from_session="session-123",
            to_agent="codex",
            project="terra4mice",
            project_root="/path/to/project",
            resources={
                "module.inference": {
                    "status": "implemented",
                    "files": ["src/inference.py"],
                    "knowledge": ["Uses tree-sitter"],
                    "confidence": 0.95,
                }
            },
            notes="Phase 2 complete",
            recommendations=["Focus on Phase 3"],
            warnings=["Careful with analyzers.py"],
        )
        
        assert handoff.from_agent == "claude-code"
        assert handoff.from_model == "claude-opus-4"
        assert handoff.to_agent == "codex"
        assert handoff.project == "terra4mice"
        assert len(handoff.resources) == 1
        assert handoff.notes == "Phase 2 complete"
        assert len(handoff.recommendations) == 1
        assert len(handoff.warnings) == 1
    
    def test_handoff_to_dict(self):
        """Test serializing handoff to dict."""
        handoff = ContextHandoff(
            from_agent="claude-code",
            project="test",
            resources={"module.test": {"status": "implemented"}},
        )
        
        data = handoff.to_dict()
        
        assert data["from_agent"] == "claude-code"
        assert data["project"] == "test"
        assert data["resources"]["module.test"]["status"] == "implemented"
        assert "created_at" in data
    
    def test_handoff_from_dict(self):
        """Test deserializing handoff from dict."""
        data = {
            "format_version": "1.0",
            "created_at": "2026-02-07T00:00:00",
            "from_agent": "claude-code",
            "from_model": "claude-opus-4",
            "project": "test",
            "resources": {"module.test": {"status": "implemented"}},
            "notes": "Test notes",
            "recommendations": ["Do this"],
            "warnings": ["Watch out"],
        }
        
        handoff = ContextHandoff.from_dict(data)
        
        assert handoff.from_agent == "claude-code"
        assert handoff.from_model == "claude-opus-4"
        assert handoff.project == "test"
        assert handoff.notes == "Test notes"
        assert len(handoff.recommendations) == 1
    
    def test_handoff_json_roundtrip(self):
        """Test JSON serialization roundtrip."""
        original = ContextHandoff(
            from_agent="claude-code",
            from_model="claude-opus-4",
            project="terra4mice",
            resources={
                "module.inference": {
                    "status": "implemented",
                    "confidence": 0.95,
                }
            },
            notes="Test handoff",
            recommendations=["Do X", "Do Y"],
            warnings=["Warning 1"],
        )
        
        json_str = original.to_json()
        restored = ContextHandoff.from_json(json_str)
        
        assert restored.from_agent == original.from_agent
        assert restored.from_model == original.from_model
        assert restored.project == original.project
        assert restored.resources == original.resources
        assert restored.notes == original.notes
        assert restored.recommendations == original.recommendations
        assert restored.warnings == original.warnings
    
    def test_handoff_save_load(self):
        """Test saving and loading handoff to/from file."""
        handoff = ContextHandoff(
            from_agent="claude-code",
            project="test-project",
            resources={"module.test": {"status": "implemented"}},
            notes="Save/load test",
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "handoff.json"
            
            handoff.save(path)
            assert path.exists()
            
            loaded = ContextHandoff.load(path)
            assert loaded.from_agent == handoff.from_agent
            assert loaded.project == handoff.project
            assert loaded.resources == handoff.resources
            assert loaded.notes == handoff.notes


# ========== Export Tests ==========


class TestExportAgentContext:
    """Tests for export_agent_context function."""
    
    def test_export_basic(self, registry_with_contexts, sample_state):
        """Test basic export of agent context."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
        )
        
        assert handoff.from_agent == "claude-code"
        assert handoff.from_model == "claude-opus-4"  # From agent profile
        assert len(handoff.resources) == 2  # inference and analyzers
        assert "module.inference" in handoff.resources
        assert "module.analyzers" in handoff.resources
    
    def test_export_with_project(self, registry_with_contexts, sample_state):
        """Test export with project name."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
            project="terra4mice",
        )
        
        assert handoff.project == "terra4mice"
    
    def test_export_with_notes(self, registry_with_contexts, sample_state):
        """Test export with notes and recommendations."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
            notes="Phase 2 complete",
            recommendations=["Focus on Phase 3", "Add tests"],
            warnings=["Complex code in analyzers.py"],
        )
        
        assert handoff.notes == "Phase 2 complete"
        assert len(handoff.recommendations) == 2
        assert len(handoff.warnings) == 1
    
    def test_export_with_state_snapshot(self, registry_with_contexts, sample_state):
        """Test export with full state snapshot."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
            include_state=True,
        )
        
        assert handoff.state_snapshot is not None
        assert "resources" in handoff.state_snapshot
        assert "module.inference" in handoff.state_snapshot["resources"]
    
    def test_export_to_specific_agent(self, registry_with_contexts, sample_state):
        """Test export targeted to specific agent."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
            to_agent="codex",
        )
        
        assert handoff.to_agent == "codex"
    
    def test_export_resource_details(self, registry_with_contexts, sample_state):
        """Test that exported resources contain proper details."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
        )
        
        resource = handoff.resources["module.inference"]
        assert resource["status"] == "implemented"
        assert "src/inference.py" in resource["files"]
        assert resource["confidence"] == 0.95
        assert "Uses tree-sitter for parsing" in resource["knowledge"]
    
    def test_export_nonexistent_agent(self, registry_with_contexts, sample_state):
        """Test exporting context for agent with no contexts."""
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="nonexistent-agent",
        )
        
        assert handoff.from_agent == "nonexistent-agent"
        assert len(handoff.resources) == 0


class TestExportResourceContext:
    """Tests for export_resource_context function."""
    
    def test_export_resource_basic(self, registry_with_contexts, sample_state):
        """Test basic resource context export."""
        info = export_resource_context(
            registry=registry_with_contexts,
            state=sample_state,
            resource="module.inference",
        )
        
        assert info["resource"] == "module.inference"
        assert info["status"] == "implemented"
        assert len(info["agents"]) == 1
        assert info["agents"][0]["agent"] == "claude-code"
    
    def test_export_resource_with_multiple_agents(self, empty_registry, sample_state):
        """Test resource with multiple agents."""
        # Add contexts from multiple agents
        empty_registry.register_context(
            agent="claude-code",
            resource="module.inference",
            knowledge=["Knowledge from Claude"],
            confidence=0.9,
        )
        empty_registry.register_context(
            agent="codex",
            resource="module.inference",
            knowledge=["Knowledge from Codex"],
            confidence=0.8,
        )
        
        info = export_resource_context(
            registry=empty_registry,
            state=sample_state,
            resource="module.inference",
        )
        
        assert len(info["agents"]) == 2
        agents = [a["agent"] for a in info["agents"]]
        assert "claude-code" in agents
        assert "codex" in agents
    
    def test_export_nonexistent_resource(self, registry_with_contexts, sample_state):
        """Test exporting context for resource with no contexts."""
        info = export_resource_context(
            registry=registry_with_contexts,
            state=sample_state,
            resource="module.nonexistent",
        )
        
        assert info["resource"] == "module.nonexistent"
        assert info["status"] == "unknown"
        assert len(info["agents"]) == 0


# ========== Import Tests ==========


class TestImportHandoff:
    """Tests for import_handoff function."""
    
    def test_import_basic(self, empty_registry):
        """Test basic handoff import."""
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.inference": {
                    "status": "implemented",
                    "files": ["src/inference.py"],
                    "knowledge": ["Uses tree-sitter"],
                    "confidence": 0.95,
                }
            },
        )
        
        result = import_handoff(
            registry=empty_registry,
            handoff=handoff,
            importing_agent="codex",
        )
        
        assert result.success
        assert result.imported_count == 1
        assert result.skipped_count == 0
        
        # Check the imported context
        contexts = empty_registry.get_agent_contexts("codex")
        assert len(contexts) == 1
        assert contexts[0].resource == "module.inference"
    
    def test_import_confidence_decay(self, empty_registry):
        """Test that imported confidence decays."""
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.test": {
                    "confidence": 1.0,
                }
            },
        )
        
        result = import_handoff(
            registry=empty_registry,
            handoff=handoff,
            importing_agent="codex",
            confidence_decay=0.1,
        )
        
        contexts = empty_registry.get_agent_contexts("codex")
        assert contexts[0].confidence == 0.9  # 1.0 - 0.1
    
    def test_import_knowledge_attribution(self, empty_registry):
        """Test that imported knowledge is attributed to source."""
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.test": {
                    "knowledge": ["Original knowledge"],
                }
            },
        )
        
        import_handoff(
            registry=empty_registry,
            handoff=handoff,
            importing_agent="codex",
        )
        
        contexts = empty_registry.get_agent_contexts("codex")
        assert "[from claude-code] Original knowledge" in contexts[0].knowledge
    
    def test_import_merge_strategy(self, registry_with_contexts):
        """Test MERGE strategy combines knowledge."""
        # Codex already has context on module.cli
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.cli": {
                    "files": ["src/cli.py", "src/new.py"],
                    "knowledge": ["New knowledge"],
                    "confidence": 0.95,
                }
            },
        )
        
        result = import_handoff(
            registry=registry_with_contexts,
            handoff=handoff,
            importing_agent="codex",
            merge_strategy=MergeStrategy.MERGE,
        )
        
        contexts = registry_with_contexts.get_agent_contexts("codex")
        cli_context = next(c for c in contexts if c.resource == "module.cli")
        
        # Should have merged files
        assert "src/main.py" in cli_context.files_touched  # Original
        assert "src/new.py" in cli_context.files_touched  # New
        
        # Should have merged knowledge
        assert "Uses click library" in cli_context.knowledge  # Original
        assert any("New knowledge" in k for k in cli_context.knowledge)  # New
    
    def test_import_replace_strategy(self, registry_with_contexts):
        """Test REPLACE strategy overwrites existing."""
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.cli": {
                    "files": ["src/only-new.py"],
                    "knowledge": ["Only new knowledge"],
                    "confidence": 0.5,
                }
            },
        )
        
        result = import_handoff(
            registry=registry_with_contexts,
            handoff=handoff,
            importing_agent="codex",
            merge_strategy=MergeStrategy.REPLACE,
        )
        
        contexts = registry_with_contexts.get_agent_contexts("codex")
        cli_context = next(c for c in contexts if c.resource == "module.cli")
        
        # Should only have new files
        assert cli_context.files_touched == ["src/only-new.py"]
        
        # Should only have new knowledge (with attribution)
        assert len(cli_context.knowledge) == 1
    
    def test_import_skip_existing_strategy(self, registry_with_contexts):
        """Test SKIP_EXISTING strategy doesn't modify existing."""
        original_contexts = registry_with_contexts.get_agent_contexts("codex")
        original_cli = next(c for c in original_contexts if c.resource == "module.cli")
        original_knowledge = original_cli.knowledge.copy()
        
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.cli": {
                    "knowledge": ["Should not appear"],
                },
                "module.new": {
                    "knowledge": ["New resource"],
                },
            },
        )
        
        result = import_handoff(
            registry=registry_with_contexts,
            handoff=handoff,
            importing_agent="codex",
            merge_strategy=MergeStrategy.SKIP_EXISTING,
        )
        
        assert result.imported_count == 1
        assert result.skipped_count == 1
        
        # Original CLI context unchanged
        contexts = registry_with_contexts.get_agent_contexts("codex")
        cli_context = next(c for c in contexts if c.resource == "module.cli")
        assert cli_context.knowledge == original_knowledge
        
        # New resource was imported
        new_context = next((c for c in contexts if c.resource == "module.new"), None)
        assert new_context is not None
    
    def test_import_empty_handoff(self, empty_registry):
        """Test importing empty handoff."""
        handoff = ContextHandoff(from_agent="claude-code")
        
        result = import_handoff(
            registry=empty_registry,
            handoff=handoff,
            importing_agent="codex",
        )
        
        assert result.success
        assert result.imported_count == 0
        assert "No resources to import" in result.messages
    
    def test_import_detects_third_party_conflicts(self, registry_with_contexts):
        """Test that import detects conflicts with third-party agents."""
        # Add an active context from a third agent
        registry_with_contexts.register_context(
            agent="kimi",
            resource="module.inference",
            knowledge=["Kimi's knowledge"],
        )
        
        handoff = ContextHandoff(
            from_agent="claude-code",
            resources={
                "module.inference": {
                    "knowledge": ["Claude's knowledge"],
                }
            },
        )
        
        result = import_handoff(
            registry=registry_with_contexts,
            handoff=handoff,
            importing_agent="codex",
        )
        
        assert len(result.conflicts) > 0
        assert any(c["other_agent"] == "kimi" for c in result.conflicts)


# ========== Sync Tests ==========


class TestSyncContexts:
    """Tests for sync_contexts function."""
    
    def test_sync_all_contexts(self, registry_with_contexts, sample_state):
        """Test syncing all contexts from one agent to another."""
        result = sync_contexts(
            registry=registry_with_contexts,
            state=sample_state,
            from_agent="claude-code",
            to_agent="cursor",  # New agent
        )
        
        assert result.success
        assert result.imported_count == 2  # inference and analyzers
        
        # Check cursor now has contexts
        cursor_contexts = registry_with_contexts.get_agent_contexts("cursor")
        assert len(cursor_contexts) == 2
    
    def test_sync_specific_resources(self, registry_with_contexts, sample_state):
        """Test syncing specific resources only."""
        result = sync_contexts(
            registry=registry_with_contexts,
            state=sample_state,
            from_agent="claude-code",
            to_agent="cursor",
            resources=["module.inference"],
        )
        
        assert result.imported_count == 1
        
        cursor_contexts = registry_with_contexts.get_agent_contexts("cursor")
        assert len(cursor_contexts) == 1
        assert cursor_contexts[0].resource == "module.inference"
    
    def test_sync_with_confidence_decay(self, registry_with_contexts, sample_state):
        """Test that sync applies confidence decay."""
        result = sync_contexts(
            registry=registry_with_contexts,
            state=sample_state,
            from_agent="claude-code",
            to_agent="cursor",
            confidence_decay=0.2,
        )
        
        cursor_contexts = registry_with_contexts.get_agent_contexts("cursor")
        inference_ctx = next(c for c in cursor_contexts if c.resource == "module.inference")
        
        # Original was 0.95, after 0.2 decay should be 0.75
        assert inference_ctx.confidence == 0.75


# ========== Conflict Detection Tests ==========


class TestDetectConflicts:
    """Tests for detect_conflicts function."""
    
    def test_detect_conflicts_finds_overlaps(self, registry_with_contexts):
        """Test that conflicts are detected for overlapping files."""
        conflicts = detect_conflicts(
            registry=registry_with_contexts,
            agent="codex",
            modified_files=["src/inference.py"],
        )
        
        assert len(conflicts) == 1
        assert conflicts[0]["agent"] == "claude-code"
        assert conflicts[0]["resource"] == "module.inference"
        assert "src/inference.py" in conflicts[0]["files"]
    
    def test_detect_conflicts_ignores_self(self, registry_with_contexts):
        """Test that agent doesn't conflict with itself."""
        conflicts = detect_conflicts(
            registry=registry_with_contexts,
            agent="claude-code",
            modified_files=["src/inference.py"],
        )
        
        assert len(conflicts) == 0
    
    def test_detect_conflicts_no_overlaps(self, registry_with_contexts):
        """Test no conflicts for non-overlapping files."""
        conflicts = detect_conflicts(
            registry=registry_with_contexts,
            agent="codex",
            modified_files=["src/brand-new.py"],
        )
        
        assert len(conflicts) == 0
    
    def test_detect_conflicts_multiple_agents(self, registry_with_contexts):
        """Test conflicts from multiple agents."""
        # Add another agent touching the same file
        registry_with_contexts.register_context(
            agent="kimi",
            resource="module.kimi-stuff",
            files_touched=["src/inference.py", "src/kimi.py"],
        )
        
        conflicts = detect_conflicts(
            registry=registry_with_contexts,
            agent="codex",
            modified_files=["src/inference.py"],
        )
        
        assert len(conflicts) == 2
        agents = [c["agent"] for c in conflicts]
        assert "claude-code" in agents
        assert "kimi" in agents


class TestFormatConflictWarning:
    """Tests for format_conflict_warning function."""
    
    def test_format_empty_conflicts(self):
        """Test formatting empty conflicts list."""
        warning = format_conflict_warning([])
        assert warning == ""
    
    def test_format_single_conflict(self):
        """Test formatting a single conflict."""
        conflicts = [{
            "agent": "claude-code",
            "resource": "module.inference",
            "files": ["src/inference.py"],
            "their_timestamp": datetime.now(),
            "their_status": "active",
        }]
        
        warning = format_conflict_warning(conflicts)
        
        assert "⚠️  Context Conflict Warning:" in warning
        assert "claude-code" in warning
        assert "module.inference" in warning
        assert "src/inference.py" in warning
        assert "terra4mice contexts sync" in warning
    
    def test_format_multiple_conflicts(self):
        """Test formatting multiple conflicts."""
        conflicts = [
            {
                "agent": "claude-code",
                "resource": "module.inference",
                "files": ["src/inference.py"],
                "their_timestamp": datetime.now(),
                "their_status": "active",
            },
            {
                "agent": "kimi",
                "resource": "module.analyzers",
                "files": ["src/analyzers.py"],
                "their_timestamp": datetime.now(),
                "their_status": "stale",
            },
        ]
        
        warning = format_conflict_warning(conflicts)
        
        assert "claude-code" in warning
        assert "kimi" in warning
        assert "active" in warning
        assert "stale" in warning


# ========== Integration Tests ==========


class TestContextIOIntegration:
    """Integration tests for the full context I/O workflow."""
    
    def test_full_handoff_workflow(self, registry_with_contexts, sample_state):
        """Test complete handoff workflow: export -> save -> load -> import."""
        # Export
        handoff = export_agent_context(
            registry=registry_with_contexts,
            state=sample_state,
            agent="claude-code",
            project="terra4mice",
            notes="Handing off to Codex",
            recommendations=["Focus on CLI improvements"],
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "handoff.json"
            
            # Save
            handoff.save(path)
            
            # Load
            loaded = ContextHandoff.load(path)
            
            # Import into new registry
            new_registry = ContextRegistry()
            result = import_handoff(
                registry=new_registry,
                handoff=loaded,
                importing_agent="codex",
            )
        
        assert result.success
        assert result.imported_count == 2
        
        # Verify imported contexts
        codex_contexts = new_registry.get_agent_contexts("codex")
        assert len(codex_contexts) == 2
        
        resources = [c.resource for c in codex_contexts]
        assert "module.inference" in resources
        assert "module.analyzers" in resources
    
    def test_multi_agent_collaboration(self, empty_registry, sample_state):
        """Test multiple agents collaborating on same project."""
        # Claude works on inference
        empty_registry.register_context(
            agent="claude-code",
            resource="module.inference",
            files_touched=["src/inference.py"],
            knowledge=["Implemented base parser"],
        )
        
        # Sync to Codex
        sync_contexts(
            registry=empty_registry,
            state=sample_state,
            from_agent="claude-code",
            to_agent="codex",
        )
        
        # Codex adds more context
        empty_registry.register_context(
            agent="codex",
            resource="module.inference",
            knowledge=["Added error handling"],
        )
        
        # Kimi tries to modify - should see conflicts
        conflicts = detect_conflicts(
            registry=empty_registry,
            agent="kimi",
            modified_files=["src/inference.py"],
        )
        
        assert len(conflicts) == 2  # Both Claude and Codex
        
        # Kimi syncs from both
        sync_contexts(
            registry=empty_registry,
            state=sample_state,
            from_agent="claude-code",
            to_agent="kimi",
        )
        sync_contexts(
            registry=empty_registry,
            state=sample_state,
            from_agent="codex",
            to_agent="kimi",
        )
        
        # Kimi now has combined knowledge
        kimi_contexts = empty_registry.get_agent_contexts("kimi")
        kimi_inference = next(c for c in kimi_contexts if c.resource == "module.inference")
        
        assert len(kimi_inference.knowledge) >= 2
    
    def test_who_knows_after_sync(self, empty_registry, sample_state):
        """Test who_knows reflects synced contexts."""
        # Multiple agents get context on same resource
        empty_registry.register_context(
            agent="claude-code",
            resource="module.test",
        )
        
        sync_contexts(
            registry=empty_registry,
            state=sample_state,
            from_agent="claude-code",
            to_agent="codex",
        )
        sync_contexts(
            registry=empty_registry,
            state=sample_state,
            from_agent="claude-code",
            to_agent="kimi",
        )
        
        who = empty_registry.who_knows("module.test")
        
        assert len(who) == 3
        agents = [w[0] for w in who]
        assert "claude-code" in agents
        assert "codex" in agents
        assert "kimi" in agents
