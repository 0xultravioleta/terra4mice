"""
Agent backends for terra4mice Auto mode.

Provides pluggable AI agent dispatch — subprocess-based (Claude Code,
Codex, etc.) or custom callables.  Includes PromptBuilder for
context-rich implementation prompts.
"""

from __future__ import annotations

import os
import subprocess
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..models import PlanAction, Resource, ResourceStatus
from ..state_manager import StateManager
from ..contexts import ContextRegistry


# ── Agent Result ──────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Result of an agent attempting to implement a resource."""

    success: bool = False
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    output: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    exit_code: int = -1

    @property
    def all_files(self) -> list[str]:
        return self.files_created + self.files_modified


# ── Prompt Builder ────────────────────────────────────────────────────

class PromptBuilder:
    """
    Builds context-rich prompts for AI agents.

    Given a PlanAction and project context, produces a prompt that tells
    the agent exactly what to implement.
    """

    def __init__(
        self,
        project_root: str | Path,
        state_manager: Optional[StateManager] = None,
        context_registry: Optional[ContextRegistry] = None,
    ):
        self.project_root = Path(project_root)
        self.state_manager = state_manager
        self.context_registry = context_registry

    def build(self, action: PlanAction) -> str:
        """Build a full implementation prompt for the given action."""
        sections: list[str] = []

        # Header
        sections.append(self._header(action))

        # Resource details
        sections.append(self._resource_details(action.resource))

        # Dependencies
        deps_section = self._dependencies(action.resource)
        if deps_section:
            sections.append(deps_section)

        # Files context
        files_section = self._files_context(action.resource)
        if files_section:
            sections.append(files_section)

        # Agent context (who else has worked on this)
        agent_section = self._agent_context(action.resource)
        if agent_section:
            sections.append(agent_section)

        # Instructions
        sections.append(self._instructions(action))

        return "\n\n".join(sections)

    def _header(self, action: PlanAction) -> str:
        return (
            f"# Task: {action.action.upper()} {action.resource.address}\n\n"
            f"You are implementing a resource for a software project tracked "
            f"by terra4mice (Terraform for code).\n"
            f"Project root: {self.project_root}"
        )

    def _resource_details(self, resource: Resource) -> str:
        lines = [f"## Resource: {resource.address}"]
        lines.append(f"- **Type:** {resource.type}")
        lines.append(f"- **Name:** {resource.name}")
        lines.append(f"- **Current status:** {resource.status.value}")

        if resource.attributes:
            lines.append("\n### Attributes (spec requirements):")
            for key, val in resource.attributes.items():
                if key == "files":
                    continue  # Handled in files section
                lines.append(f"- **{key}:** {val}")

        return "\n".join(lines)

    def _dependencies(self, resource: Resource) -> str:
        if not resource.depends_on:
            return ""

        lines = ["## Dependencies"]
        for dep_addr in resource.depends_on:
            if self.state_manager:
                dep = self.state_manager.state.get(dep_addr)
                if dep:
                    status = dep.status.value
                    dep_files = ", ".join(dep.files[:3]) if dep.files else "none"
                    lines.append(
                        f"- **{dep_addr}**: {status} (files: {dep_files})"
                    )
                else:
                    lines.append(f"- **{dep_addr}**: not in state")
            else:
                lines.append(f"- **{dep_addr}**")

        return "\n".join(lines)

    def _files_context(self, resource: Resource) -> str:
        files: list[str] = list(resource.files)
        attr_files = resource.attributes.get("files", [])
        if isinstance(attr_files, list):
            files.extend(attr_files)

        # Deduplicate
        seen: set[str] = set()
        unique: list[str] = []
        for f in files:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        if not unique:
            return ""

        lines = ["## Suggested Files"]
        for f in unique:
            full = self.project_root / f
            if full.exists():
                size = full.stat().st_size
                lines.append(f"- `{f}` (exists, {size} bytes) — update")
            else:
                lines.append(f"- `{f}` (does not exist) — create")

        return "\n".join(lines)

    def _agent_context(self, resource: Resource) -> str:
        if not self.context_registry:
            return ""

        contexts = self.context_registry.get_resource_contexts(
            resource.address
        )
        if not contexts:
            return ""

        lines = ["## Prior Agent Context"]
        for ctx in contexts[:5]:
            status = ctx.status().value
            age = ctx.age_str()
            lines.append(f"- **{ctx.agent}**: {status} ({age})")
            if ctx.knowledge:
                for k in ctx.knowledge[:3]:
                    lines.append(f"  - {k}")
            if ctx.files_touched:
                lines.append(
                    f"  - Files: {', '.join(ctx.files_touched[:5])}"
                )

        return "\n".join(lines)

    def _instructions(self, action: PlanAction) -> str:
        lines = ["## Instructions"]

        if action.action == "create":
            lines.append("Implement this resource from scratch.")
        elif action.action == "update":
            lines.append(
                "Update the existing implementation to match the spec."
            )
        elif action.action == "delete":
            lines.append(
                "Remove this resource's implementation (clean up files)."
            )

        if action.reason:
            lines.append(f"\n**Reason:** {action.reason}")

        lines.append("\n**Requirements:**")
        lines.append("1. Create/modify the necessary files")
        lines.append("2. Follow existing code style and patterns")
        lines.append("3. Include appropriate error handling")
        lines.append("4. Add docstrings and comments where needed")
        lines.append(
            "5. If tests are expected, create them in the test directory"
        )

        return "\n".join(lines)


# ── Agent Backends ────────────────────────────────────────────────────

class AgentBackend(ABC):
    """Abstract base class for AI agent backends."""

    name: str = "base"

    @abstractmethod
    def execute(
        self,
        prompt: str,
        project_root: str | Path,
        timeout_seconds: int = 300,
    ) -> AgentResult:
        """
        Execute an implementation task.

        Args:
            prompt: The full implementation prompt.
            project_root: Root directory of the project.
            timeout_seconds: Max time for the agent to work.

        Returns:
            AgentResult with success/failure and details.
        """
        ...

    def is_available(self) -> bool:
        """Check if this agent backend is available on the system."""
        return True


class SubprocessAgent(AgentBackend):
    """
    Run an AI coding agent as a subprocess.

    Supports any CLI agent that accepts a prompt on stdin or as an
    argument and works on files in the current directory.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        name: str = "subprocess",
    ):
        self.command = command
        self.args = args or []
        self.env = env
        self.name = name

    def execute(
        self,
        prompt: str,
        project_root: str | Path,
        timeout_seconds: int = 300,
    ) -> AgentResult:
        start = time.time()
        project_root = Path(project_root)

        # Build command
        cmd = [self.command] + self.args

        # Merge environment
        run_env = os.environ.copy()
        if self.env:
            run_env.update(self.env)

        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                cwd=str(project_root),
                timeout=timeout_seconds,
                env=run_env,
            )

            duration = time.time() - start

            return AgentResult(
                success=proc.returncode == 0,
                output=proc.stdout,
                error=proc.stderr,
                duration_seconds=duration,
                exit_code=proc.returncode,
            )

        except subprocess.TimeoutExpired:
            return AgentResult(
                success=False,
                error=f"Agent timed out after {timeout_seconds}s",
                duration_seconds=time.time() - start,
                exit_code=-1,
            )
        except FileNotFoundError:
            return AgentResult(
                success=False,
                error=f"Agent command not found: {self.command}",
                duration_seconds=time.time() - start,
                exit_code=-1,
            )

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None


class ClaudeCodeAgent(SubprocessAgent):
    """Claude Code (claude-code CLI) backend."""

    def __init__(self, model: str = "sonnet", **kwargs):
        super().__init__(
            command="claude",
            args=["--print", "--model", model],
            name="claude-code",
            **kwargs,
        )


class CodexAgent(SubprocessAgent):
    """OpenAI Codex CLI backend."""

    def __init__(self, **kwargs):
        super().__init__(
            command="codex",
            args=["--quiet"],
            name="codex",
            **kwargs,
        )


class CallableAgent(AgentBackend):
    """
    Agent backend that wraps a Python callable.

    Useful for testing, custom integrations, or API-based agents.
    """

    def __init__(
        self,
        fn: Callable[[str, Path, int], AgentResult],
        name: str = "callable",
    ):
        self._fn = fn
        self.name = name

    def execute(
        self,
        prompt: str,
        project_root: str | Path,
        timeout_seconds: int = 300,
    ) -> AgentResult:
        return self._fn(prompt, Path(project_root), timeout_seconds)


class ChainedAgent(AgentBackend):
    """
    Agent backend that tries multiple agents in order, falling back on failure.
    
    Useful for implementing fallback strategies where you want to try
    a preferred agent first, but fall back to alternatives if it fails.
    """

    def __init__(
        self,
        agents: list[AgentBackend],
        name: str = "chained",
        stop_on_first_success: bool = True,
    ):
        """
        Initialize ChainedAgent.
        
        Args:
            agents: List of agent backends to try in order.
            name: Name of this chained agent.
            stop_on_first_success: If True, stop on first successful agent.
        """
        if not agents:
            raise ValueError("ChainedAgent requires at least one agent")
        
        self.agents = agents
        self.name = name
        self.stop_on_first_success = stop_on_first_success
        
        # Track which agent succeeded for the last execution
        self.last_successful_agent: Optional[str] = None
        self.attempt_count: int = 0

    def execute(
        self,
        prompt: str,
        project_root: str | Path,
        timeout_seconds: int = 300,
    ) -> AgentResult:
        """
        Try each agent in order until one succeeds or all fail.
        
        Returns the result from the first successful agent, or a combined
        error result if all agents fail.
        """
        start = time.time()
        self.attempt_count += 1
        self.last_successful_agent = None
        
        errors: list[str] = []
        all_outputs: list[str] = []
        
        for i, agent in enumerate(self.agents):
            try:
                result = agent.execute(prompt, project_root, timeout_seconds)
                
                # Track outputs regardless of success
                if result.output:
                    all_outputs.append(f"Agent {agent.name}: {result.output}")
                
                if result.success:
                    # Success! Track which agent worked and return result
                    self.last_successful_agent = agent.name
                    result.output = f"[ChainedAgent] Success with {agent.name} (attempt {i+1}/{len(self.agents)})\n" + result.output
                    return result
                else:
                    # Failed, add to errors and continue
                    errors.append(f"Agent {agent.name}: {result.error}")
                    
            except Exception as e:
                errors.append(f"Agent {agent.name}: Exception: {e}")
        
        # All agents failed
        duration = time.time() - start
        combined_error = "\n".join([
            f"[ChainedAgent] All {len(self.agents)} agents failed:",
            *errors
        ])
        
        combined_output = "\n".join(all_outputs) if all_outputs else ""
        
        return AgentResult(
            success=False,
            output=combined_output,
            error=combined_error,
            duration_seconds=duration,
            exit_code=-1,
        )

    def is_available(self) -> bool:
        """Check if at least one agent in the chain is available."""
        return any(agent.is_available() for agent in self.agents)


# ── Agent Registry ────────────────────────────────────────────────────

_KNOWN_AGENTS: dict[str, type[AgentBackend]] = {
    "claude-code": ClaudeCodeAgent,
    "codex": CodexAgent,
    "chained": ChainedAgent,
}


def get_agent(name: str, **kwargs) -> AgentBackend:
    """
    Get an agent backend by name.

    Args:
        name: Agent identifier (e.g., "claude-code", "codex") or comma-separated 
              list for chained agents (e.g., "claude-code,codex").
        **kwargs: Passed to the agent constructor.

    Returns:
        An AgentBackend instance.

    Raises:
        ValueError: If agent name is unknown.
    """
    # Check if this is a chained agent specification (comma-separated)
    if "," in name:
        agent_names = [n.strip() for n in name.split(",") if n.strip()]  # Filter empty strings
        agents = []
        for agent_name in agent_names:
            if agent_name not in _KNOWN_AGENTS:
                raise ValueError(
                    f"Unknown agent in chain: {agent_name!r}. "
                    f"Available: {', '.join(sorted(_KNOWN_AGENTS.keys() - {'chained'}))}"
                )
            # Don't pass kwargs to individual agents in chain
            agents.append(_KNOWN_AGENTS[agent_name]())
        
        # Create ChainedAgent with the list of agents
        return ChainedAgent(agents, name=f"chained({','.join(agent_names)})", **kwargs)
    
    cls = _KNOWN_AGENTS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown agent: {name!r}. "
            f"Available: {', '.join(sorted(_KNOWN_AGENTS))}"
        )
    
    # Special handling for ChainedAgent - it needs a list of agents
    if cls == ChainedAgent:
        if "agents" not in kwargs:
            raise ValueError("ChainedAgent requires 'agents' parameter")
    
    return cls(**kwargs)


def register_agent(name: str, cls: type[AgentBackend]) -> None:
    """Register a custom agent backend."""
    _KNOWN_AGENTS[name] = cls


def list_agents() -> list[str]:
    """List available agent names."""
    return sorted(_KNOWN_AGENTS.keys())
