"""
ApplyRunner — Core execution engine for terra4mice apply.

Generates a plan, orders actions by dependency DAG, and dispatches
to the appropriate mode handler (interactive, auto, hybrid, market).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..models import Plan, PlanAction, ResourceStatus, Spec
from ..planner import generate_plan
from ..state_manager import StateManager
from ..contexts import ContextRegistry


@dataclass
class ApplyConfig:
    """Configuration for an apply run."""

    mode: str = "interactive"          # interactive | auto | hybrid | market
    agent: Optional[str] = None        # Agent ID for context tracking
    parallel: int = 1                  # Max parallel implementations
    timeout_minutes: int = 0           # 0 = no timeout
    require_tests: bool = False        # Require tests before marking done
    auto_commit: bool = False          # Git commit after each resource
    dry_run: bool = False              # Show plan without executing
    enhanced: bool = True              # Use enhanced interactive mode

    def validate(self) -> list[str]:
        """Return list of validation errors (empty if valid)."""
        errors: list[str] = []
        if self.mode not in ("interactive", "auto", "hybrid", "market"):
            errors.append(f"Invalid mode: {self.mode!r}. "
                          "Must be interactive, auto, hybrid, or market.")
        if self.parallel < 1:
            errors.append(f"parallel must be >= 1, got {self.parallel}")
        if self.timeout_minutes < 0:
            errors.append(f"timeout_minutes must be >= 0, got {self.timeout_minutes}")
        return errors


@dataclass
class ApplyResult:
    """Result of an apply run."""

    implemented: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    market_pending: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return (len(self.implemented) + len(self.skipped)
                + len(self.failed) + len(self.market_pending))

    def summary(self) -> str:
        """Human-readable summary."""
        parts: list[str] = []
        if self.implemented:
            parts.append(f"{len(self.implemented)} implemented")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        if self.market_pending:
            parts.append(f"{len(self.market_pending)} pending on market")
        if not parts:
            return "No actions taken."
        return f"Apply result: {', '.join(parts)} ({self.duration_seconds:.1f}s)"


class CyclicDependencyError(Exception):
    """Raised when a dependency cycle is detected."""


class ApplyRunner:
    """
    Core execution engine for ``terra4mice apply``.

    Usage::

        runner = ApplyRunner(spec, state_manager, config=ApplyConfig())
        result = runner.run()
    """

    def __init__(
        self,
        spec: Spec,
        state_manager: StateManager,
        context_registry: Optional[ContextRegistry] = None,
        config: Optional[ApplyConfig] = None,
    ):
        self.spec = spec
        self.state_manager = state_manager
        self.context_registry = context_registry
        self.config = config or ApplyConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, resource: Optional[str] = None) -> ApplyResult:
        """
        Execute the apply workflow.

        Args:
            resource: Optional resource address to apply a single resource.

        Returns:
            ApplyResult with summary of actions taken.
        """
        start = time.time()

        plan = generate_plan(self.spec, self.state_manager.state)

        if not plan.has_changes:
            return ApplyResult(duration_seconds=time.time() - start)

        # Filter to actionable items (skip no-ops)
        actions = [a for a in plan.actions if a.action != "no-op"]

        # Filter to specific resource if requested
        if resource:
            actions = [a for a in actions if a.resource.address == resource]
            if not actions:
                return ApplyResult(duration_seconds=time.time() - start)

        # Topological sort based on dependencies
        ordered = self._topological_sort(actions)

        # Dry-run: just return empty result
        if self.config.dry_run:
            return ApplyResult(duration_seconds=time.time() - start)

        # Dispatch to mode handler
        mode_handler = self._get_mode_handler()
        result = mode_handler.execute(ordered)

        result.duration_seconds = time.time() - start
        return result

    # ------------------------------------------------------------------
    # DAG Ordering
    # ------------------------------------------------------------------

    def _topological_sort(self, actions: list[PlanAction]) -> list[PlanAction]:
        """
        Order actions respecting dependency DAG.

        Resources whose dependencies are already implemented (in state)
        are considered satisfied. Only dependencies *within* the action
        set create ordering constraints.

        Raises:
            CyclicDependencyError: If a cycle is detected.
        """
        # Build lookup: address -> PlanAction
        action_map: dict[str, PlanAction] = {
            a.resource.address: a for a in actions
        }

        # Addresses already implemented in state (satisfied deps)
        implemented = {
            addr
            for addr, res in self.state_manager.state.resources.items()
            if res.status == ResourceStatus.IMPLEMENTED
        }

        # Build adjacency list (only for deps that are in the action set)
        # edge: dep_addr -> addr  means "dep must come before addr"
        graph: dict[str, list[str]] = {a.resource.address: [] for a in actions}
        in_degree: dict[str, int] = {a.resource.address: 0 for a in actions}

        for action in actions:
            addr = action.resource.address
            for dep in action.resource.depends_on:
                if dep in action_map and dep not in implemented:
                    # dep must come before addr
                    graph[dep].append(addr)
                    in_degree[addr] += 1

        # Kahn's algorithm
        queue = [addr for addr, deg in in_degree.items() if deg == 0]
        sorted_addrs: list[str] = []

        while queue:
            # Sort the queue for deterministic output
            queue.sort()
            node = queue.pop(0)
            sorted_addrs.append(node)
            for neighbour in graph[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(sorted_addrs) != len(actions):
            # Find the cycle participants
            remaining = set(action_map.keys()) - set(sorted_addrs)
            raise CyclicDependencyError(
                f"Dependency cycle detected among: {', '.join(sorted(remaining))}"
            )

        return [action_map[addr] for addr in sorted_addrs]

    # ------------------------------------------------------------------
    # Mode Dispatch
    # ------------------------------------------------------------------

    def _get_mode_handler(self):
        """Return the appropriate mode handler based on config."""
        # Import here to avoid circular imports
        from .modes import InteractiveMode, AutoMode, HybridMode, MarketMode

        handlers = {
            "interactive": InteractiveMode,
            "auto": AutoMode,
            "hybrid": HybridMode,
            "market": MarketMode,
        }

        handler_cls = handlers.get(self.config.mode, InteractiveMode)
        return handler_cls(
            state_manager=self.state_manager,
            context_registry=self.context_registry,
            config=self.config,
        )
