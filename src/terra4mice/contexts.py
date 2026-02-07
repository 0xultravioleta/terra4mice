"""
Multi-AI Context Tracking for terra4mice.

Tracks which AI agents have context on which resources,
enabling handoffs, conflict detection, and onboarding.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
import json
import os


class ContextStatus(Enum):
    """Status of an agent's context on a resource."""
    ACTIVE = "active"       # Recently touched (< stale_threshold)
    STALE = "stale"         # Not recently touched (> stale, < expired)
    EXPIRED = "expired"     # Old context, likely outdated


# Default thresholds (configurable)
STALE_THRESHOLD = timedelta(hours=24)
EXPIRED_THRESHOLD = timedelta(days=7)


@dataclass
class ContextEntry:
    """
    Represents an AI agent's context on a specific resource.
    
    Think of this as "what the agent knows" about a resource.
    
    Attributes:
        agent: Agent identifier (e.g., "claude-code", "codex", "cursor")
        resource: Resource address (e.g., "module.inference")
        timestamp: When context was last updated
        files_touched: List of files the agent has worked on
        lines_modified: Map of file -> list of (start, end) line ranges
        knowledge: Key learnings about this resource
        confidence: 0.0-1.0, how confident is the context
        session_id: Unique session identifier
        session_start: When this session started
        contributed_status: Status when this agent contributed
    """
    agent: str
    resource: str
    timestamp: datetime
    
    # What the agent touched
    files_touched: List[str] = field(default_factory=list)
    lines_modified: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)
    
    # Agent's knowledge
    knowledge: List[str] = field(default_factory=list)
    confidence: float = 1.0
    
    # Session tracking
    session_id: Optional[str] = None
    session_start: Optional[datetime] = None
    
    # Relationship to state
    contributed_status: Optional[str] = None
    
    def status(
        self,
        now: Optional[datetime] = None,
        stale_threshold: timedelta = STALE_THRESHOLD,
        expired_threshold: timedelta = EXPIRED_THRESHOLD,
    ) -> ContextStatus:
        """
        Compute context status based on age.
        
        Args:
            now: Reference time (defaults to datetime.now())
            stale_threshold: Time after which context is stale
            expired_threshold: Time after which context is expired
            
        Returns:
            ContextStatus enum value
        """
        now = now or datetime.now()
        age = now - self.timestamp
        
        if age < stale_threshold:
            return ContextStatus.ACTIVE
        elif age < expired_threshold:
            return ContextStatus.STALE
        else:
            return ContextStatus.EXPIRED
    
    def age_str(self, now: Optional[datetime] = None) -> str:
        """
        Human-readable age string.
        
        Args:
            now: Reference time (defaults to datetime.now())
            
        Returns:
            String like "just now", "5min ago", "2hr ago", "3d ago"
        """
        now = now or datetime.now()
        age = now - self.timestamp
        
        if age < timedelta(minutes=1):
            return "just now"
        elif age < timedelta(hours=1):
            minutes = int(age.total_seconds() / 60)
            return f"{minutes}min ago"
        elif age < timedelta(days=1):
            hours = int(age.total_seconds() / 3600)
            return f"{hours}hr ago"
        else:
            days = age.days
            return f"{days}d ago"
    
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "agent": self.agent,
            "resource": self.resource,
            "timestamp": self.timestamp.isoformat(),
            "files_touched": self.files_touched,
            "lines_modified": {k: list(v) for k, v in self.lines_modified.items()},
            "knowledge": self.knowledge,
            "confidence": self.confidence,
            "session_id": self.session_id,
            "session_start": self.session_start.isoformat() if self.session_start else None,
            "contributed_status": self.contributed_status,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ContextEntry":
        """Deserialize from dict."""
        # Convert lines_modified back to tuples
        lines_modified = {}
        for k, v in data.get("lines_modified", {}).items():
            lines_modified[k] = [tuple(pair) for pair in v]
        
        return cls(
            agent=data["agent"],
            resource=data["resource"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            files_touched=data.get("files_touched", []),
            lines_modified=lines_modified,
            knowledge=data.get("knowledge", []),
            confidence=data.get("confidence", 1.0),
            session_id=data.get("session_id"),
            session_start=datetime.fromisoformat(data["session_start"]) if data.get("session_start") else None,
            contributed_status=data.get("contributed_status"),
        )


@dataclass
class AgentProfile:
    """
    Profile of an AI agent with metadata.
    
    Used for identification and handoff metadata.
    
    Attributes:
        id: Agent identifier (e.g., "claude-code", "codex", "cursor")
        name: Human-friendly name
        model: Model name (e.g., "claude-opus-4", "gpt-4o")
        platform: Platform name (e.g., "openclaw", "github", "cursor")
        version: Agent/model version
        capabilities: List of capabilities (e.g., ["python", "typescript"])
        current_session: Current session identifier
        last_seen: When agent was last active
    """
    id: str
    name: str = ""
    model: Optional[str] = None
    platform: Optional[str] = None
    version: Optional[str] = None
    
    # Capabilities (for intelligent handoffs)
    capabilities: List[str] = field(default_factory=list)
    
    # Session info
    current_session: Optional[str] = None
    last_seen: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name or self.id,
            "model": self.model,
            "platform": self.platform,
            "version": self.version,
            "capabilities": self.capabilities,
            "current_session": self.current_session,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentProfile":
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            model=data.get("model"),
            platform=data.get("platform"),
            version=data.get("version"),
            capabilities=data.get("capabilities", []),
            current_session=data.get("current_session"),
            last_seen=datetime.fromisoformat(data["last_seen"]) if data.get("last_seen") else None,
        )


class ContextRegistry:
    """
    Registry of all agent contexts for a project.
    
    This is the central data structure for multi-AI context tracking.
    Persisted alongside state (or in a separate .contexts.json file).
    
    Example:
        >>> registry = ContextRegistry()
        >>> registry.register_context("claude-code", "module.inference", 
        ...                           files_touched=["src/inference.py"])
        >>> registry.who_knows("module.inference")
        [('claude-code', 'active', 'just now')]
    """
    
    def __init__(self):
        # Contexts indexed by (agent, resource) tuple
        self._contexts: Dict[Tuple[str, str], ContextEntry] = {}
        
        # Agent profiles
        self._agents: Dict[str, AgentProfile] = {}
        
        # Version for migrations
        self.version: str = "1"
        self.last_updated: Optional[datetime] = None
    
    # ========== Agent Management ==========
    
    def register_agent(self, profile: AgentProfile) -> None:
        """
        Register or update an agent profile.
        
        Args:
            profile: AgentProfile to register
        """
        profile.last_seen = datetime.now()
        self._agents[profile.id] = profile
        self.last_updated = datetime.now()
    
    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        """
        Get agent profile by ID.
        
        Args:
            agent_id: Agent identifier
            
        Returns:
            AgentProfile or None if not found
        """
        return self._agents.get(agent_id)
    
    def list_agents(self) -> List[AgentProfile]:
        """
        List all registered agents.
        
        Returns:
            List of AgentProfile objects
        """
        return list(self._agents.values())
    
    # ========== Context Management ==========
    
    def register_context(
        self,
        agent: str,
        resource: str,
        files_touched: Optional[List[str]] = None,
        knowledge: Optional[List[str]] = None,
        confidence: float = 1.0,
        session_id: Optional[str] = None,
        contributed_status: Optional[str] = None,
    ) -> ContextEntry:
        """
        Record that an agent has context on a resource.
        
        This is called when:
        - An agent marks a resource (terra4mice mark --agent=X)
        - An agent modifies files mapped to a resource
        - An agent imports context from another agent
        
        Args:
            agent: Agent identifier
            resource: Resource address
            files_touched: List of files the agent worked on
            knowledge: Key learnings about this resource
            confidence: Confidence level (0.0-1.0)
            session_id: Session identifier
            contributed_status: Status when contributing
            
        Returns:
            The created or updated ContextEntry
        """
        key = (agent, resource)
        
        # Get or create entry
        existing = self._contexts.get(key)
        if existing:
            entry = existing
            entry.timestamp = datetime.now()
            # Merge files and knowledge
            if files_touched:
                entry.files_touched = list(set(entry.files_touched + files_touched))
            if knowledge:
                entry.knowledge = list(set(entry.knowledge + knowledge))
            entry.confidence = max(entry.confidence, confidence)
            if session_id:
                entry.session_id = session_id
            if contributed_status:
                entry.contributed_status = contributed_status
        else:
            entry = ContextEntry(
                agent=agent,
                resource=resource,
                timestamp=datetime.now(),
                files_touched=files_touched or [],
                knowledge=knowledge or [],
                confidence=confidence,
                session_id=session_id,
                contributed_status=contributed_status,
            )
        
        self._contexts[key] = entry
        self.last_updated = datetime.now()
        
        # Update agent last_seen
        if agent in self._agents:
            self._agents[agent].last_seen = datetime.now()
        
        return entry
    
    def get_agent_contexts(self, agent: str) -> List[ContextEntry]:
        """
        Get all resources an agent has context on.
        
        Args:
            agent: Agent identifier
            
        Returns:
            List of ContextEntry objects for this agent
        """
        return [
            entry for (a, r), entry in self._contexts.items()
            if a == agent
        ]
    
    def get_resource_contexts(self, resource: str) -> List[ContextEntry]:
        """
        Get all agents' contexts for a resource.
        
        Args:
            resource: Resource address
            
        Returns:
            List of ContextEntry objects for this resource
        """
        return [
            entry for (a, r), entry in self._contexts.items()
            if r == resource
        ]
    
    def who_knows(self, resource: str) -> List[Tuple[str, str, str]]:
        """
        Who has context on this resource?
        
        Args:
            resource: Resource address to query
            
        Returns:
            List of (agent, status, age_str) tuples, sorted by recency.
            
        Example:
            >>> registry.who_knows("module.inference")
            [('claude-code', 'active', '5min ago'), ('codex', 'stale', '2d ago')]
        """
        entries = self.get_resource_contexts(resource)
        now = datetime.now()
        # Sort by recency (most recent first)
        entries_sorted = sorted(entries, key=lambda e: e.timestamp, reverse=True)
        return [
            (e.agent, e.status(now).value, e.age_str(now))
            for e in entries_sorted
        ]
    
    def find_conflicts(self, agent: str, files: List[str]) -> List[dict]:
        """
        Find potential conflicts where other agents touched the same files.
        
        Args:
            agent: Current agent identifier
            files: Files the agent is about to modify
            
        Returns:
            List of conflict dicts with agent, resource, file info.
            
        Example:
            >>> registry.find_conflicts("codex", ["src/inference.py"])
            [{'agent': 'claude-code', 'resource': 'module.inference', 
              'files': ['src/inference.py'], 'their_status': 'active', ...}]
        """
        conflicts = []
        files_set = set(files)
        
        for (other_agent, resource), entry in self._contexts.items():
            if other_agent == agent:
                continue  # Skip self
            
            overlapping_files = files_set & set(entry.files_touched)
            if overlapping_files:
                conflicts.append({
                    "agent": other_agent,
                    "resource": resource,
                    "files": list(overlapping_files),
                    "their_timestamp": entry.timestamp,
                    "their_status": entry.status().value,
                })
        
        return conflicts
    
    def update_status(
        self,
        agent: str,
        resource: str,
        confidence: Optional[float] = None,
        knowledge: Optional[List[str]] = None,
        files_touched: Optional[List[str]] = None,
    ) -> Optional[ContextEntry]:
        """
        Update an existing context entry.
        
        Args:
            agent: Agent identifier
            resource: Resource address
            confidence: New confidence value (optional)
            knowledge: Additional knowledge to add (optional)
            files_touched: Additional files to add (optional)
            
        Returns:
            Updated ContextEntry or None if not found
        """
        key = (agent, resource)
        entry = self._contexts.get(key)
        
        if not entry:
            return None
        
        entry.timestamp = datetime.now()
        
        if confidence is not None:
            entry.confidence = confidence
        
        if knowledge:
            entry.knowledge = list(set(entry.knowledge + knowledge))
        
        if files_touched:
            entry.files_touched = list(set(entry.files_touched + files_touched))
        
        self.last_updated = datetime.now()
        return entry
    
    def remove(self, agent: str, resource: str) -> Optional[ContextEntry]:
        """
        Remove a context entry.
        
        Args:
            agent: Agent identifier
            resource: Resource address
            
        Returns:
            Removed ContextEntry or None if not found
        """
        key = (agent, resource)
        if key in self._contexts:
            entry = self._contexts.pop(key)
            self.last_updated = datetime.now()
            return entry
        return None
    
    def clear_agent(self, agent: str) -> int:
        """
        Clear all contexts for an agent.
        
        Args:
            agent: Agent identifier
            
        Returns:
            Count of removed contexts
        """
        keys_to_remove = [k for k in self._contexts if k[0] == agent]
        for key in keys_to_remove:
            del self._contexts[key]
        if keys_to_remove:
            self.last_updated = datetime.now()
        return len(keys_to_remove)
    
    def expire_old(
        self,
        threshold: timedelta = EXPIRED_THRESHOLD,
        now: Optional[datetime] = None,
    ) -> int:
        """
        Remove contexts older than threshold.
        
        Args:
            threshold: Age threshold for expiration
            now: Reference time (defaults to datetime.now())
            
        Returns:
            Count of removed contexts
        """
        now = now or datetime.now()
        keys_to_remove = [
            k for k, entry in self._contexts.items()
            if (now - entry.timestamp) > threshold
        ]
        for key in keys_to_remove:
            del self._contexts[key]
        if keys_to_remove:
            self.last_updated = datetime.now()
        return len(keys_to_remove)
    
    def list_all(self) -> List[ContextEntry]:
        """
        List all context entries.
        
        Returns:
            List of all ContextEntry objects
        """
        return list(self._contexts.values())
    
    def coverage_summary(self) -> Dict[str, int]:
        """
        Summary of context coverage by status.
        
        Returns:
            Dict with counts: {"active": N, "stale": M, "expired": K}
        """
        now = datetime.now()
        summary = {"active": 0, "stale": 0, "expired": 0}
        for entry in self._contexts.values():
            status = entry.status(now)
            summary[status.value] += 1
        return summary
    
    # ========== Serialization ==========
    
    def to_dict(self) -> dict:
        """Serialize registry to JSON-compatible dict."""
        return {
            "version": self.version,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "agents": {k: v.to_dict() for k, v in self._agents.items()},
            "contexts": [entry.to_dict() for entry in self._contexts.values()],
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ContextRegistry":
        """Deserialize from dict."""
        registry = cls()
        registry.version = data.get("version", "1")
        if data.get("last_updated"):
            registry.last_updated = datetime.fromisoformat(data["last_updated"])
        
        # Load agents
        for agent_id, agent_data in data.get("agents", {}).items():
            registry._agents[agent_id] = AgentProfile.from_dict(agent_data)
        
        # Load contexts
        for ctx_data in data.get("contexts", []):
            entry = ContextEntry.from_dict(ctx_data)
            registry._contexts[(entry.agent, entry.resource)] = entry
        
        return registry
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)
    
    @classmethod
    def from_json(cls, json_str: str) -> "ContextRegistry":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))


# ========== Convenience Functions ==========

def infer_agent_from_env() -> Optional[str]:
    """
    Attempt to infer the current AI agent from environment.
    
    Checks common environment variables set by AI platforms.
    
    Returns:
        Agent identifier string or None if not detected
        
    Environment Variables Checked:
        - OPENCLAW_SESSION, CLAUDE_SESSION_ID -> "claude-code"
        - CURSOR_SESSION -> "cursor"
        - CODEX_SESSION -> "codex"
        - GITHUB_COPILOT_SESSION -> "copilot"
        - KIMI_SESSION -> "kimi"
        - AI_AGENT_ID -> (value of variable)
    """
    # OpenClaw / Claude Code
    if os.environ.get("OPENCLAW_SESSION"):
        return "claude-code"
    if os.environ.get("CLAUDE_SESSION_ID"):
        return "claude-code"
    
    # Cursor
    if os.environ.get("CURSOR_SESSION"):
        return "cursor"
    
    # Codex / GitHub Copilot
    if os.environ.get("CODEX_SESSION"):
        return "codex"
    if os.environ.get("GITHUB_COPILOT_SESSION"):
        return "copilot"
    
    # Kimi
    if os.environ.get("KIMI_SESSION"):
        return "kimi"
    
    # Generic fallback
    if os.environ.get("AI_AGENT_ID"):
        return os.environ.get("AI_AGENT_ID")
    
    return None
