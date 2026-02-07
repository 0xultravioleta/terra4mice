"""
Tests for terra4mice.contexts module.

Tests the multi-AI context tracking functionality including:
- ContextStatus enum
- ContextEntry dataclass
- AgentProfile dataclass
- ContextRegistry class
- infer_agent_from_env function
"""

import json
import os
import pytest
from datetime import datetime, timedelta

from terra4mice.contexts import (
    ContextStatus,
    ContextEntry,
    AgentProfile,
    ContextRegistry,
    infer_agent_from_env,
    STALE_THRESHOLD,
    EXPIRED_THRESHOLD,
)


# ========== ContextStatus Tests ==========

class TestContextStatus:
    """Tests for ContextStatus enum."""
    
    def test_status_values(self):
        """Test that all expected status values exist."""
        assert ContextStatus.ACTIVE.value == "active"
        assert ContextStatus.STALE.value == "stale"
        assert ContextStatus.EXPIRED.value == "expired"
    
    def test_status_count(self):
        """Test that we have exactly 3 statuses."""
        assert len(ContextStatus) == 3


# ========== ContextEntry Tests ==========

class TestContextEntry:
    """Tests for ContextEntry dataclass."""
    
    def test_create_minimal(self):
        """Test creating entry with minimal fields."""
        entry = ContextEntry(
            agent="claude-code",
            resource="module.inference",
            timestamp=datetime.now(),
        )
        assert entry.agent == "claude-code"
        assert entry.resource == "module.inference"
        assert entry.files_touched == []
        assert entry.knowledge == []
        assert entry.confidence == 1.0
    
    def test_create_full(self):
        """Test creating entry with all fields."""
        now = datetime.now()
        entry = ContextEntry(
            agent="claude-code",
            resource="module.inference",
            timestamp=now,
            files_touched=["src/inference.py", "src/models.py"],
            lines_modified={"src/inference.py": [(10, 50), (100, 150)]},
            knowledge=["Uses tree-sitter", "Has 5-level fallback"],
            confidence=0.9,
            session_id="session-123",
            session_start=now,
            contributed_status="implemented",
        )
        assert entry.files_touched == ["src/inference.py", "src/models.py"]
        assert entry.lines_modified == {"src/inference.py": [(10, 50), (100, 150)]}
        assert len(entry.knowledge) == 2
        assert entry.confidence == 0.9
        assert entry.session_id == "session-123"
        assert entry.contributed_status == "implemented"
    
    def test_status_active(self):
        """Test status returns ACTIVE for recent entries."""
        entry = ContextEntry(
            agent="claude-code",
            resource="module.test",
            timestamp=datetime.now(),
        )
        assert entry.status() == ContextStatus.ACTIVE
    
    def test_status_stale(self):
        """Test status returns STALE for old entries."""
        old_time = datetime.now() - timedelta(hours=25)
        entry = ContextEntry(
            agent="claude-code",
            resource="module.test",
            timestamp=old_time,
        )
        assert entry.status() == ContextStatus.STALE
    
    def test_status_expired(self):
        """Test status returns EXPIRED for very old entries."""
        very_old = datetime.now() - timedelta(days=8)
        entry = ContextEntry(
            agent="claude-code",
            resource="module.test",
            timestamp=very_old,
        )
        assert entry.status() == ContextStatus.EXPIRED
    
    def test_status_custom_thresholds(self):
        """Test status with custom thresholds."""
        old_time = datetime.now() - timedelta(hours=2)
        entry = ContextEntry(
            agent="claude-code",
            resource="module.test",
            timestamp=old_time,
        )
        # With default thresholds, 2 hours is active
        assert entry.status() == ContextStatus.ACTIVE
        # With custom 1-hour threshold, it's stale
        assert entry.status(
            stale_threshold=timedelta(hours=1),
            expired_threshold=timedelta(hours=3)
        ) == ContextStatus.STALE
    
    def test_age_str_just_now(self):
        """Test age_str for very recent entries."""
        entry = ContextEntry(
            agent="test",
            resource="test",
            timestamp=datetime.now(),
        )
        assert entry.age_str() == "just now"
    
    def test_age_str_minutes(self):
        """Test age_str for entries minutes old."""
        entry = ContextEntry(
            agent="test",
            resource="test",
            timestamp=datetime.now() - timedelta(minutes=15),
        )
        assert entry.age_str() == "15min ago"
    
    def test_age_str_hours(self):
        """Test age_str for entries hours old."""
        entry = ContextEntry(
            agent="test",
            resource="test",
            timestamp=datetime.now() - timedelta(hours=3),
        )
        assert entry.age_str() == "3hr ago"
    
    def test_age_str_days(self):
        """Test age_str for entries days old."""
        entry = ContextEntry(
            agent="test",
            resource="test",
            timestamp=datetime.now() - timedelta(days=5),
        )
        assert entry.age_str() == "5d ago"
    
    def test_serialization_roundtrip(self):
        """Test to_dict and from_dict preserve data."""
        now = datetime.now()
        original = ContextEntry(
            agent="claude-code",
            resource="module.inference",
            timestamp=now,
            files_touched=["src/inference.py"],
            lines_modified={"src/inference.py": [(10, 50)]},
            knowledge=["Important learning"],
            confidence=0.85,
            session_id="session-456",
            session_start=now,
            contributed_status="partial",
        )
        
        data = original.to_dict()
        restored = ContextEntry.from_dict(data)
        
        assert restored.agent == original.agent
        assert restored.resource == original.resource
        assert restored.files_touched == original.files_touched
        assert restored.lines_modified == original.lines_modified
        assert restored.knowledge == original.knowledge
        assert restored.confidence == original.confidence
        assert restored.session_id == original.session_id
        assert restored.contributed_status == original.contributed_status
    
    def test_to_dict_json_serializable(self):
        """Test that to_dict output is JSON serializable."""
        entry = ContextEntry(
            agent="test",
            resource="test",
            timestamp=datetime.now(),
            session_start=datetime.now(),
        )
        data = entry.to_dict()
        # Should not raise
        json_str = json.dumps(data)
        assert isinstance(json_str, str)


# ========== AgentProfile Tests ==========

class TestAgentProfile:
    """Tests for AgentProfile dataclass."""
    
    def test_create_minimal(self):
        """Test creating profile with minimal fields."""
        profile = AgentProfile(id="claude-code")
        assert profile.id == "claude-code"
        assert profile.name == ""
        assert profile.model is None
        assert profile.capabilities == []
    
    def test_create_full(self):
        """Test creating profile with all fields."""
        now = datetime.now()
        profile = AgentProfile(
            id="claude-code",
            name="Claude Code",
            model="claude-opus-4",
            platform="openclaw",
            version="1.0.0",
            capabilities=["python", "typescript", "rust"],
            current_session="session-789",
            last_seen=now,
        )
        assert profile.name == "Claude Code"
        assert profile.model == "claude-opus-4"
        assert profile.platform == "openclaw"
        assert "python" in profile.capabilities
        assert profile.current_session == "session-789"
    
    def test_serialization_roundtrip(self):
        """Test to_dict and from_dict preserve data."""
        now = datetime.now()
        original = AgentProfile(
            id="codex",
            name="Codex",
            model="codex-2025",
            platform="github",
            capabilities=["python", "javascript"],
            last_seen=now,
        )
        
        data = original.to_dict()
        restored = AgentProfile.from_dict(data)
        
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.model == original.model
        assert restored.platform == original.platform
        assert restored.capabilities == original.capabilities
    
    def test_to_dict_uses_id_for_empty_name(self):
        """Test that to_dict uses id when name is empty."""
        profile = AgentProfile(id="cursor")
        data = profile.to_dict()
        assert data["name"] == "cursor"


# ========== ContextRegistry Tests ==========

class TestContextRegistry:
    """Tests for ContextRegistry class."""
    
    def test_create_empty(self):
        """Test creating empty registry."""
        registry = ContextRegistry()
        assert registry.list_all() == []
        assert registry.list_agents() == []
        assert registry.version == "1"
    
    def test_register_agent(self):
        """Test registering an agent."""
        registry = ContextRegistry()
        profile = AgentProfile(id="claude-code", model="claude-opus-4")
        
        registry.register_agent(profile)
        
        assert registry.get_agent("claude-code") is not None
        assert registry.get_agent("claude-code").model == "claude-opus-4"
        assert len(registry.list_agents()) == 1
    
    def test_register_context(self):
        """Test registering a context."""
        registry = ContextRegistry()
        
        entry = registry.register_context(
            agent="claude-code",
            resource="module.inference",
            files_touched=["src/inference.py"],
            knowledge=["Uses tree-sitter"],
        )
        
        assert entry.agent == "claude-code"
        assert entry.resource == "module.inference"
        assert "src/inference.py" in entry.files_touched
        assert "Uses tree-sitter" in entry.knowledge
    
    def test_register_context_updates_existing(self):
        """Test that registering same context updates it."""
        registry = ContextRegistry()
        
        registry.register_context(
            agent="claude-code",
            resource="module.inference",
            files_touched=["src/inference.py"],
            knowledge=["Learning 1"],
        )
        
        # Register again with more data
        entry = registry.register_context(
            agent="claude-code",
            resource="module.inference",
            files_touched=["src/models.py"],
            knowledge=["Learning 2"],
        )
        
        # Should merge files and knowledge
        assert "src/inference.py" in entry.files_touched
        assert "src/models.py" in entry.files_touched
        assert "Learning 1" in entry.knowledge
        assert "Learning 2" in entry.knowledge
        
        # Should only have one entry
        assert len(registry.list_all()) == 1
    
    def test_get_agent_contexts(self):
        """Test getting all contexts for an agent."""
        registry = ContextRegistry()
        
        registry.register_context("claude-code", "module.a")
        registry.register_context("claude-code", "module.b")
        registry.register_context("codex", "module.c")
        
        contexts = registry.get_agent_contexts("claude-code")
        assert len(contexts) == 2
        resources = {c.resource for c in contexts}
        assert "module.a" in resources
        assert "module.b" in resources
    
    def test_get_resource_contexts(self):
        """Test getting all contexts for a resource."""
        registry = ContextRegistry()
        
        registry.register_context("claude-code", "module.shared")
        registry.register_context("codex", "module.shared")
        registry.register_context("cursor", "module.other")
        
        contexts = registry.get_resource_contexts("module.shared")
        assert len(contexts) == 2
        agents = {c.agent for c in contexts}
        assert "claude-code" in agents
        assert "codex" in agents
    
    def test_who_knows(self):
        """Test who_knows returns sorted list."""
        registry = ContextRegistry()
        
        # Register in order: old, older, newest
        registry.register_context("codex", "module.test")
        registry._contexts[("codex", "module.test")].timestamp = datetime.now() - timedelta(hours=2)
        
        registry.register_context("claude-code", "module.test")
        registry._contexts[("claude-code", "module.test")].timestamp = datetime.now() - timedelta(days=2)
        
        registry.register_context("cursor", "module.test")
        # cursor is newest (just registered)
        
        result = registry.who_knows("module.test")
        
        # Should be sorted by recency (newest first)
        assert len(result) == 3
        assert result[0][0] == "cursor"  # Most recent
        assert result[1][0] == "codex"
        assert result[2][0] == "claude-code"  # Oldest
        
        # Check status
        assert result[0][1] == "active"  # cursor
        assert result[1][1] == "active"  # codex (2 hours)
        assert result[2][1] == "stale"   # claude-code (2 days)
    
    def test_find_conflicts(self):
        """Test finding conflicts with other agents."""
        registry = ContextRegistry()
        
        registry.register_context(
            "claude-code",
            "module.inference",
            files_touched=["src/inference.py", "src/models.py"],
        )
        registry.register_context(
            "codex",
            "module.cli",
            files_touched=["src/cli.py", "src/models.py"],
        )
        
        # cursor wants to modify models.py
        conflicts = registry.find_conflicts("cursor", ["src/models.py"])
        
        assert len(conflicts) == 2
        agents = {c["agent"] for c in conflicts}
        assert "claude-code" in agents
        assert "codex" in agents
        
        # Both should flag models.py
        for c in conflicts:
            assert "src/models.py" in c["files"]
    
    def test_find_conflicts_ignores_self(self):
        """Test that find_conflicts ignores the querying agent."""
        registry = ContextRegistry()
        
        registry.register_context(
            "claude-code",
            "module.inference",
            files_touched=["src/inference.py"],
        )
        
        conflicts = registry.find_conflicts("claude-code", ["src/inference.py"])
        assert len(conflicts) == 0
    
    def test_update_status(self):
        """Test updating an existing context."""
        registry = ContextRegistry()
        
        registry.register_context(
            "claude-code",
            "module.test",
            confidence=0.5,
        )
        
        entry = registry.update_status(
            "claude-code",
            "module.test",
            confidence=0.9,
            knowledge=["New learning"],
        )
        
        assert entry is not None
        assert entry.confidence == 0.9
        assert "New learning" in entry.knowledge
    
    def test_update_status_nonexistent(self):
        """Test updating nonexistent context returns None."""
        registry = ContextRegistry()
        result = registry.update_status("ghost", "module.phantom")
        assert result is None
    
    def test_remove(self):
        """Test removing a context."""
        registry = ContextRegistry()
        
        registry.register_context("claude-code", "module.test")
        assert len(registry.list_all()) == 1
        
        removed = registry.remove("claude-code", "module.test")
        
        assert removed is not None
        assert removed.agent == "claude-code"
        assert len(registry.list_all()) == 0
    
    def test_remove_nonexistent(self):
        """Test removing nonexistent context returns None."""
        registry = ContextRegistry()
        result = registry.remove("ghost", "module.phantom")
        assert result is None
    
    def test_clear_agent(self):
        """Test clearing all contexts for an agent."""
        registry = ContextRegistry()
        
        registry.register_context("claude-code", "module.a")
        registry.register_context("claude-code", "module.b")
        registry.register_context("codex", "module.c")
        
        count = registry.clear_agent("claude-code")
        
        assert count == 2
        assert len(registry.get_agent_contexts("claude-code")) == 0
        assert len(registry.get_agent_contexts("codex")) == 1
    
    def test_expire_old(self):
        """Test expiring old contexts."""
        registry = ContextRegistry()
        
        # Add fresh context
        registry.register_context("claude-code", "module.fresh")
        
        # Add old context
        registry.register_context("codex", "module.old")
        registry._contexts[("codex", "module.old")].timestamp = datetime.now() - timedelta(days=10)
        
        count = registry.expire_old()
        
        assert count == 1
        assert len(registry.list_all()) == 1
        assert registry.list_all()[0].agent == "claude-code"
    
    def test_coverage_summary(self):
        """Test coverage summary counts."""
        registry = ContextRegistry()
        now = datetime.now()
        
        # Active
        registry.register_context("a", "r1")
        
        # Stale (25 hours old)
        registry.register_context("b", "r2")
        registry._contexts[("b", "r2")].timestamp = now - timedelta(hours=25)
        
        # Expired (10 days old)
        registry.register_context("c", "r3")
        registry._contexts[("c", "r3")].timestamp = now - timedelta(days=10)
        
        summary = registry.coverage_summary()
        
        assert summary["active"] == 1
        assert summary["stale"] == 1
        assert summary["expired"] == 1
    
    def test_serialization_roundtrip(self):
        """Test full registry serialization."""
        registry = ContextRegistry()
        
        # Add agent
        registry.register_agent(AgentProfile(
            id="claude-code",
            model="claude-opus-4",
        ))
        
        # Add contexts
        registry.register_context(
            "claude-code",
            "module.inference",
            files_touched=["src/inference.py"],
            knowledge=["Important info"],
            confidence=0.95,
        )
        
        # Serialize and restore
        data = registry.to_dict()
        restored = ContextRegistry.from_dict(data)
        
        # Verify agents
        assert len(restored.list_agents()) == 1
        assert restored.get_agent("claude-code").model == "claude-opus-4"
        
        # Verify contexts
        assert len(restored.list_all()) == 1
        ctx = restored.list_all()[0]
        assert ctx.agent == "claude-code"
        assert ctx.resource == "module.inference"
        assert "src/inference.py" in ctx.files_touched
        assert "Important info" in ctx.knowledge
        assert ctx.confidence == 0.95
    
    def test_to_json_from_json(self):
        """Test JSON string serialization."""
        registry = ContextRegistry()
        registry.register_context("test", "module.test")
        
        json_str = registry.to_json()
        assert isinstance(json_str, str)
        
        restored = ContextRegistry.from_json(json_str)
        assert len(restored.list_all()) == 1


# ========== infer_agent_from_env Tests ==========

class TestInferAgentFromEnv:
    """Tests for infer_agent_from_env function."""
    
    def test_no_env_vars(self):
        """Test returns None when no relevant env vars set."""
        # Save and clear relevant vars
        saved = {}
        env_vars = [
            "OPENCLAW_SESSION", "CLAUDE_SESSION_ID", "CURSOR_SESSION",
            "CODEX_SESSION", "GITHUB_COPILOT_SESSION", "KIMI_SESSION", "AI_AGENT_ID"
        ]
        for var in env_vars:
            if var in os.environ:
                saved[var] = os.environ.pop(var)
        
        try:
            result = infer_agent_from_env()
            assert result is None
        finally:
            # Restore
            os.environ.update(saved)
    
    def test_openclaw_session(self):
        """Test detects OpenClaw/Claude Code."""
        os.environ["OPENCLAW_SESSION"] = "test-session"
        try:
            result = infer_agent_from_env()
            assert result == "claude-code"
        finally:
            del os.environ["OPENCLAW_SESSION"]
    
    def test_claude_session_id(self):
        """Test detects Claude session."""
        os.environ["CLAUDE_SESSION_ID"] = "test-session"
        try:
            result = infer_agent_from_env()
            assert result == "claude-code"
        finally:
            del os.environ["CLAUDE_SESSION_ID"]
    
    def test_cursor_session(self):
        """Test detects Cursor."""
        os.environ["CURSOR_SESSION"] = "test-session"
        try:
            result = infer_agent_from_env()
            assert result == "cursor"
        finally:
            del os.environ["CURSOR_SESSION"]
    
    def test_codex_session(self):
        """Test detects Codex."""
        os.environ["CODEX_SESSION"] = "test-session"
        try:
            result = infer_agent_from_env()
            assert result == "codex"
        finally:
            del os.environ["CODEX_SESSION"]
    
    def test_copilot_session(self):
        """Test detects GitHub Copilot."""
        os.environ["GITHUB_COPILOT_SESSION"] = "test-session"
        try:
            result = infer_agent_from_env()
            assert result == "copilot"
        finally:
            del os.environ["GITHUB_COPILOT_SESSION"]
    
    def test_kimi_session(self):
        """Test detects Kimi."""
        os.environ["KIMI_SESSION"] = "test-session"
        try:
            result = infer_agent_from_env()
            assert result == "kimi"
        finally:
            del os.environ["KIMI_SESSION"]
    
    def test_generic_ai_agent_id(self):
        """Test uses AI_AGENT_ID as fallback."""
        os.environ["AI_AGENT_ID"] = "custom-agent"
        try:
            result = infer_agent_from_env()
            assert result == "custom-agent"
        finally:
            del os.environ["AI_AGENT_ID"]
    
    def test_priority_openclaw_over_generic(self):
        """Test that specific vars take priority over generic."""
        os.environ["OPENCLAW_SESSION"] = "oc-session"
        os.environ["AI_AGENT_ID"] = "generic-agent"
        try:
            result = infer_agent_from_env()
            assert result == "claude-code"  # OpenClaw takes priority
        finally:
            del os.environ["OPENCLAW_SESSION"]
            del os.environ["AI_AGENT_ID"]


# ========== Integration Tests ==========

class TestIntegration:
    """Integration tests for context tracking workflows."""
    
    def test_multi_agent_workflow(self):
        """Test a typical multi-agent workflow."""
        registry = ContextRegistry()
        
        # Claude Code starts working
        registry.register_agent(AgentProfile(
            id="claude-code",
            model="claude-opus-4",
            platform="openclaw",
        ))
        registry.register_context(
            "claude-code",
            "module.inference",
            files_touched=["src/inference.py"],
            knowledge=["Implemented tree-sitter parsing"],
        )
        
        # Codex takes over later
        registry.register_agent(AgentProfile(
            id="codex",
            model="codex-2025",
            platform="github",
        ))
        
        # Codex checks who worked on this before
        who = registry.who_knows("module.inference")
        assert len(who) == 1
        assert who[0][0] == "claude-code"
        
        # Codex registers its own context
        registry.register_context(
            "codex",
            "module.inference",
            files_touched=["src/inference.py"],
            knowledge=["Added caching layer"],
        )
        
        # Now both know about it
        who = registry.who_knows("module.inference")
        assert len(who) == 2
        
        # Coverage summary
        summary = registry.coverage_summary()
        assert summary["active"] == 2
    
    def test_conflict_detection_workflow(self):
        """Test detecting and handling conflicts."""
        registry = ContextRegistry()
        
        # Claude worked on models
        registry.register_context(
            "claude-code",
            "module.models",
            files_touched=["src/models.py"],
        )
        
        # Cursor wants to modify models.py
        conflicts = registry.find_conflicts("cursor", ["src/models.py", "src/new_file.py"])
        
        assert len(conflicts) == 1
        assert conflicts[0]["agent"] == "claude-code"
        assert conflicts[0]["resource"] == "module.models"
        assert "src/models.py" in conflicts[0]["files"]
        # new_file.py should not be in conflicts since no one touched it
        assert "src/new_file.py" not in conflicts[0]["files"]
    
    def test_full_serialization_cycle(self):
        """Test complete save/load cycle."""
        registry = ContextRegistry()
        
        # Build up state
        registry.register_agent(AgentProfile(id="agent1", model="model1"))
        registry.register_agent(AgentProfile(id="agent2", model="model2"))
        
        registry.register_context("agent1", "res1", knowledge=["k1"])
        registry.register_context("agent1", "res2", knowledge=["k2"])
        registry.register_context("agent2", "res1", knowledge=["k3"])
        
        # Serialize to JSON
        json_str = registry.to_json()
        
        # Parse and verify structure
        data = json.loads(json_str)
        assert data["version"] == "1"
        assert len(data["agents"]) == 2
        assert len(data["contexts"]) == 3
        
        # Restore and verify
        restored = ContextRegistry.from_json(json_str)
        assert len(restored.list_agents()) == 2
        assert len(restored.list_all()) == 3
        assert len(restored.get_agent_contexts("agent1")) == 2
        assert len(restored.get_resource_contexts("res1")) == 2
