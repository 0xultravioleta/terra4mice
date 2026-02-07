"""
Context I/O - Export, Import, and Handoff for multi-AI collaboration.

Provides structured formats for passing context between AI agents,
with support for merging, conflict resolution, and partial imports.

This module enables:
- Exporting an agent's context as a handoff document
- Importing context from another agent
- Syncing contexts between agents
- Detecting potential conflicts

Example:
    >>> from terra4mice.context_io import export_agent_context, import_handoff
    >>> from terra4mice.contexts import ContextRegistry
    >>> 
    >>> registry = ContextRegistry()
    >>> # ... register some contexts ...
    >>> 
    >>> # Export for handoff
    >>> handoff = export_agent_context(registry, state, "claude-code")
    >>> handoff.save(Path("handoff.json"))
    >>> 
    >>> # Import in another session
    >>> handoff = ContextHandoff.load(Path("handoff.json"))
    >>> result = import_handoff(registry, handoff, "codex")
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from .contexts import ContextEntry, ContextRegistry, AgentProfile
from .models import State


class MergeStrategy(Enum):
    """
    Strategy for merging imported contexts with existing ones.
    
    Attributes:
        MERGE: Combine knowledge, use max confidence
        REPLACE: Overwrite existing with imported
        SKIP_EXISTING: Only import new resources
    """
    MERGE = "merge"
    REPLACE = "replace"
    SKIP_EXISTING = "skip_existing"


@dataclass
class ContextHandoff:
    """
    A structured handoff of context from one agent to another.
    
    This is the primary format for transferring knowledge between agents.
    It captures not just what resources an agent worked on, but also
    their accumulated knowledge, recommendations, and warnings.
    
    Attributes:
        format_version: Version of the handoff format
        created_at: When the handoff was created
        from_agent: Source agent identifier
        from_model: Model used by source agent (e.g., "claude-opus-4")
        from_session: Session ID of source agent
        to_agent: Target agent identifier (optional, can be generic)
        project: Project name
        project_root: Path to project root
        resources: Dict mapping resource address to context details
        state_snapshot: Full state snapshot (optional)
        notes: Freeform notes from the handing-off agent
        recommendations: What to work on next
        warnings: What to be careful about
        
    Example:
        >>> handoff = ContextHandoff(
        ...     from_agent="claude-code",
        ...     from_model="claude-opus-4",
        ...     project="terra4mice",
        ...     resources={"module.inference": {...}},
        ...     notes="Phase 2 complete",
        ...     recommendations=["Focus on contexts.py next"]
        ... )
        >>> handoff.save(Path("handoff.json"))
    """
    # Metadata
    format_version: str = "1.0"
    created_at: Optional[datetime] = None
    
    # Source agent
    from_agent: str = ""
    from_model: Optional[str] = None
    from_session: Optional[str] = None
    
    # Target agent (optional, can be generic handoff)
    to_agent: Optional[str] = None
    
    # Project info
    project: str = ""
    project_root: Optional[str] = None
    
    # The actual context being handed off
    resources: Optional[Dict[str, dict]] = None
    
    # State snapshot (optional, for full handoffs)
    state_snapshot: Optional[dict] = None
    
    # Notes from the handing-off agent
    notes: str = ""
    recommendations: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.resources is None:
            self.resources = {}
        if self.recommendations is None:
            self.recommendations = []
        if self.warnings is None:
            self.warnings = []
    
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "format_version": self.format_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "from_agent": self.from_agent,
            "from_model": self.from_model,
            "from_session": self.from_session,
            "to_agent": self.to_agent,
            "project": self.project,
            "project_root": self.project_root,
            "resources": self.resources,
            "state_snapshot": self.state_snapshot,
            "notes": self.notes,
            "recommendations": self.recommendations,
            "warnings": self.warnings,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ContextHandoff":
        """Deserialize from dict."""
        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])
        
        return cls(
            format_version=data.get("format_version", "1.0"),
            created_at=created_at,
            from_agent=data.get("from_agent", ""),
            from_model=data.get("from_model"),
            from_session=data.get("from_session"),
            to_agent=data.get("to_agent"),
            project=data.get("project", ""),
            project_root=data.get("project_root"),
            resources=data.get("resources", {}),
            state_snapshot=data.get("state_snapshot"),
            notes=data.get("notes", ""),
            recommendations=data.get("recommendations", []),
            warnings=data.get("warnings", []),
        )
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)
    
    @classmethod
    def from_json(cls, json_str: str) -> "ContextHandoff":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    def save(self, path: Path) -> None:
        """
        Save handoff to file.
        
        Args:
            path: Path to save the handoff JSON
        """
        path.write_text(self.to_json(), encoding="utf-8")
    
    @classmethod
    def load(cls, path: Path) -> "ContextHandoff":
        """
        Load handoff from file.
        
        Args:
            path: Path to the handoff JSON file
            
        Returns:
            ContextHandoff instance
        """
        return cls.from_json(path.read_text(encoding="utf-8"))


@dataclass
class ImportResult:
    """
    Result of importing a context handoff.
    
    Attributes:
        success: Whether import succeeded
        imported_count: Number of resources imported
        skipped_count: Number of resources skipped
        conflicts: List of conflict dictionaries
        messages: List of informational messages
    """
    success: bool
    imported_count: int
    skipped_count: int
    conflicts: List[dict] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)


def export_agent_context(
    registry: ContextRegistry,
    state: State,
    agent: str,
    project: str = "",
    include_state: bool = False,
    notes: str = "",
    recommendations: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    to_agent: Optional[str] = None,
) -> ContextHandoff:
    """
    Export an agent's context as a handoff document.
    
    This creates a structured handoff that can be saved to a file and
    imported by another agent. The handoff includes all resources the
    agent has context on, along with their knowledge and metadata.
    
    Args:
        registry: The context registry containing agent contexts
        state: Current state (for resource details)
        agent: Agent ID to export
        project: Project name (defaults to empty string)
        include_state: Include full state snapshot in handoff
        notes: Freeform handoff notes
        recommendations: List of recommended next steps
        warnings: List of things to be careful about
        to_agent: Target agent identifier (optional)
    
    Returns:
        ContextHandoff ready for serialization
        
    Example:
        >>> handoff = export_agent_context(
        ...     registry, state, "claude-code",
        ...     project="terra4mice",
        ...     notes="Phase 2 done, focus on Phase 3"
        ... )
        >>> handoff.save(Path("claude-handoff.json"))
    """
    agent_profile = registry.get_agent(agent)
    contexts = registry.get_agent_contexts(agent)
    
    # Build resource context map
    resources: Dict[str, dict] = {}
    for ctx in contexts:
        resource = state.get(ctx.resource) if state else None
        
        resource_info: dict = {
            "status": resource.status.value if resource else "unknown",
            "files": ctx.files_touched,
            "knowledge": ctx.knowledge,
            "confidence": ctx.confidence,
            "last_touched": ctx.timestamp.isoformat(),
            "context_status": ctx.status().value,
        }
        
        # Add symbol info if available
        if resource and resource.symbols:
            resource_info["symbols_count"] = len(resource.symbols)
            implemented = sum(
                1 for s in resource.symbols.values() 
                if s.status == "implemented"
            )
            resource_info["symbols_implemented"] = implemented
        
        resources[ctx.resource] = resource_info
    
    handoff = ContextHandoff(
        from_agent=agent,
        from_model=agent_profile.model if agent_profile else None,
        from_session=agent_profile.current_session if agent_profile else None,
        to_agent=to_agent,
        project=project,
        resources=resources,
        notes=notes,
        recommendations=recommendations or [],
        warnings=warnings or [],
    )
    
    if include_state and state:
        # Include serialized state snapshot
        handoff.state_snapshot = {
            "version": state.version,
            "serial": state.serial,
            "resources": {
                addr: {
                    "type": r.type,
                    "name": r.name,
                    "status": r.status.value,
                    "files": r.files,
                    "tests": r.tests,
                }
                for addr, r in state.resources.items()
            },
        }
    
    return handoff


def export_resource_context(
    registry: ContextRegistry,
    state: State,
    resource: str,
    project: str = "",
) -> dict:
    """
    Export all agents' context for a specific resource.
    
    Useful for seeing who knows what about a particular resource,
    for example when deciding who to ask for help.
    
    Args:
        registry: The context registry
        state: Current state
        resource: Resource address to export
        project: Project name (optional)
        
    Returns:
        Dict with resource info and list of agents with context
        
    Example:
        >>> info = export_resource_context(registry, state, "module.inference")
        >>> print(info["agents"])
        [{'agent': 'claude-code', 'confidence': 1.0, ...}]
    """
    contexts = registry.get_resource_contexts(resource)
    resource_obj = state.get(resource) if state else None
    
    return {
        "resource": resource,
        "project": project,
        "status": resource_obj.status.value if resource_obj else "unknown",
        "files": resource_obj.files if resource_obj else [],
        "agents": [
            {
                "agent": ctx.agent,
                "knowledge": ctx.knowledge,
                "confidence": ctx.confidence,
                "last_touched": ctx.timestamp.isoformat(),
                "status": ctx.status().value,
            }
            for ctx in sorted(contexts, key=lambda c: c.timestamp, reverse=True)
        ],
    }


def import_handoff(
    registry: ContextRegistry,
    handoff: ContextHandoff,
    importing_agent: str,
    merge_strategy: MergeStrategy = MergeStrategy.MERGE,
    confidence_decay: float = 0.1,
) -> ImportResult:
    """
    Import a context handoff into the registry.
    
    This allows an agent to acquire context from another agent's handoff
    document. The merge strategy controls how to handle resources where
    the importing agent already has context.
    
    Args:
        registry: Target context registry
        handoff: The handoff to import
        importing_agent: The agent doing the import
        merge_strategy: How to handle existing contexts
            - MERGE: Combine knowledge, use max confidence
            - REPLACE: Overwrite existing with imported
            - SKIP_EXISTING: Only import new resources
        confidence_decay: Reduce imported confidence by this amount
            (default 0.1, so 1.0 becomes 0.9)
    
    Returns:
        ImportResult with statistics and any conflicts
        
    Example:
        >>> handoff = ContextHandoff.load(Path("handoff.json"))
        >>> result = import_handoff(registry, handoff, "codex")
        >>> print(f"Imported {result.imported_count} resources")
    """
    imported = 0
    skipped = 0
    conflicts: List[dict] = []
    messages: List[str] = []
    
    if not handoff.resources:
        return ImportResult(
            success=True,
            imported_count=0,
            skipped_count=0,
            conflicts=[],
            messages=["No resources to import"],
        )
    
    for resource, ctx_data in handoff.resources.items():
        existing = None
        for entry in registry.get_agent_contexts(importing_agent):
            if entry.resource == resource:
                existing = entry
                break
        
        if existing and merge_strategy == MergeStrategy.SKIP_EXISTING:
            skipped += 1
            messages.append(f"Skipped {resource} (already exists)")
            continue
        
        # Calculate new confidence (decay on import)
        original_confidence = ctx_data.get("confidence", 1.0)
        new_confidence = max(0.1, original_confidence - confidence_decay)
        
        # Prepare knowledge with source attribution
        knowledge = ctx_data.get("knowledge", [])
        if knowledge:
            # Tag imported knowledge with source
            knowledge = [f"[from {handoff.from_agent}] {k}" for k in knowledge]
        
        files = ctx_data.get("files", [])
        
        if existing and merge_strategy == MergeStrategy.MERGE:
            # Merge with existing
            merged_knowledge = list(set(existing.knowledge + knowledge))
            merged_files = list(set(existing.files_touched + files))
            merged_confidence = max(existing.confidence, new_confidence)
            
            registry.register_context(
                agent=importing_agent,
                resource=resource,
                files_touched=merged_files,
                knowledge=merged_knowledge,
                confidence=merged_confidence,
            )
            messages.append(f"Merged context for {resource}")
        elif existing and merge_strategy == MergeStrategy.REPLACE:
            # Replace: remove existing first, then create new
            registry.remove(importing_agent, resource)
            registry.register_context(
                agent=importing_agent,
                resource=resource,
                files_touched=files,
                knowledge=knowledge,
                confidence=new_confidence,
            )
            messages.append(f"Replaced context for {resource}")
        else:
            # New import
            registry.register_context(
                agent=importing_agent,
                resource=resource,
                files_touched=files,
                knowledge=knowledge,
                confidence=new_confidence,
            )
            messages.append(f"Imported context for {resource}")
        
        imported += 1
        
        # Check for potential conflicts (other agents with active context)
        other_contexts = [
            c for c in registry.get_resource_contexts(resource)
            if c.agent != importing_agent and c.agent != handoff.from_agent
        ]
        for other in other_contexts:
            if other.status().value == "active":
                conflicts.append({
                    "resource": resource,
                    "other_agent": other.agent,
                    "other_last_seen": other.timestamp.isoformat(),
                    "warning": f"{other.agent} also has active context on this resource",
                })
    
    return ImportResult(
        success=True,
        imported_count=imported,
        skipped_count=skipped,
        conflicts=conflicts,
        messages=messages,
    )


def sync_contexts(
    registry: ContextRegistry,
    state: State,
    from_agent: str,
    to_agent: str,
    resources: Optional[List[str]] = None,
    confidence_decay: float = 0.1,
) -> ImportResult:
    """
    Sync context from one agent to another.
    
    This is a convenience wrapper around export + import that performs
    a direct agent-to-agent context sync without creating intermediate files.
    
    Args:
        registry: Context registry
        state: Current state
        from_agent: Source agent
        to_agent: Target agent
        resources: Specific resources to sync (None = all)
        confidence_decay: Confidence decay on sync
        
    Returns:
        ImportResult with statistics
        
    Example:
        >>> result = sync_contexts(
        ...     registry, state,
        ...     from_agent="claude-code",
        ...     to_agent="codex"
        ... )
        >>> print(f"Synced {result.imported_count} resources")
    """
    # Export from source
    handoff = export_agent_context(
        registry=registry,
        state=state,
        agent=from_agent,
        to_agent=to_agent,
    )
    
    # Filter resources if specified
    if resources and handoff.resources:
        handoff.resources = {
            r: ctx for r, ctx in handoff.resources.items()
            if r in resources
        }
    
    # Import to target
    return import_handoff(
        registry=registry,
        handoff=handoff,
        importing_agent=to_agent,
        merge_strategy=MergeStrategy.MERGE,
        confidence_decay=confidence_decay,
    )


def detect_conflicts(
    registry: ContextRegistry,
    agent: str,
    modified_files: List[str],
) -> List[dict]:
    """
    Detect potential conflicts before an agent modifies files.
    
    Call this before making changes to warn about other agents' contexts.
    This helps prevent stepping on another agent's work without coordination.
    
    Args:
        registry: Context registry
        agent: Current agent identifier
        modified_files: Files the agent is about to modify
        
    Returns:
        List of conflict warnings with agent, resource, and file details.
        
    Example:
        >>> conflicts = detect_conflicts(registry, "codex", ["src/inference.py"])
        >>> if conflicts:
        ...     print(format_conflict_warning(conflicts))
    """
    return registry.find_conflicts(agent, modified_files)


def format_conflict_warning(conflicts: List[dict]) -> str:
    """
    Format conflicts as a human-readable warning.
    
    Args:
        conflicts: List of conflict dicts from detect_conflicts()
        
    Returns:
        Formatted warning string, or empty string if no conflicts
        
    Example:
        >>> warning = format_conflict_warning(conflicts)
        >>> print(warning)
        ⚠️  Context Conflict Warning:
        
          Agent 'claude-code' has active context on module.inference
            Overlapping files: src/inference.py
            Last touched: 2026-02-07T00:30:00
    """
    if not conflicts:
        return ""
    
    lines = ["⚠️  Context Conflict Warning:", ""]
    for c in conflicts:
        lines.append(f"  Agent '{c['agent']}' has {c['their_status']} context on {c['resource']}")
        lines.append(f"    Overlapping files: {', '.join(c['files'])}")
        lines.append(f"    Last touched: {c['their_timestamp']}")
        lines.append("")
    
    lines.append("  Consider syncing contexts before proceeding:")
    lines.append("    terra4mice contexts sync --from=<other-agent> --to=<your-agent>")
    
    return "\n".join(lines)
