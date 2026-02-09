"""
ApplyRunner — Core execution engine for terra4mice apply.

Generates a plan, orders actions by dependency DAG, and dispatches
to the appropriate mode handler (interactive, auto, hybrid, market).
"""

from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from typing import Optional, Set, Dict

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
    max_workers: int = 1               # Max parallel workers (when parallel execution enabled)
    timeout_minutes: int = 0           # 0 = no timeout
    require_tests: bool = False        # Require tests before marking done
    auto_commit: bool = False          # Git commit after each resource
    dry_run: bool = False              # Show plan without executing
    enhanced: bool = True              # Use enhanced interactive mode
    verify_level: str = "basic"        # Verification level: basic | git_diff | full
    market_url: Optional[str] = None   # Execution Market API URL
    market_api_key: Optional[str] = None  # Execution Market API key
    bounty: Optional[float] = None     # Default bounty for market tasks (USD)

    def validate(self) -> list[str]:
        """Return list of validation errors (empty if valid)."""
        errors: list[str] = []
        if self.mode not in ("interactive", "auto", "hybrid", "market"):
            errors.append(f"Invalid mode: {self.mode!r}. "
                          "Must be interactive, auto, hybrid, or market.")
        if self.parallel < 1:
            errors.append(f"parallel must be >= 1, got {self.parallel}")
        if self.max_workers < 1:
            errors.append(f"max_workers must be >= 1, got {self.max_workers}")
        if self.timeout_minutes < 0:
            errors.append(f"timeout_minutes must be >= 0, got {self.timeout_minutes}")
        if self.verify_level not in ("basic", "git_diff", "full"):
            errors.append(f"Invalid verify_level: {self.verify_level!r}. "
                          "Must be basic, git_diff, or full.")
        if self.bounty is not None and self.bounty <= 0:
            errors.append(f"bounty must be positive, got {self.bounty}")
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
        project_root: Optional[str] = None,
    ):
        self.spec = spec
        self.state_manager = state_manager
        self.context_registry = context_registry
        self.config = config or ApplyConfig()
        self.project_root = project_root

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

        # Execute with parallel execution if enabled
        if self.config.max_workers > 1:
            result = self.execute_parallel(ordered)
        else:
            # Dispatch to mode handler for sequential execution
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
    # Parallel Execution
    # ------------------------------------------------------------------

    def execute_parallel(self, ordered: list[PlanAction]) -> ApplyResult:
        """
        Execute actions in parallel while respecting dependency DAG.

        Resources with no dependencies between them run concurrently.
        Uses ThreadPoolExecutor with max_workers from config.
        """
        if self.config.max_workers == 1:
            # Fall back to sequential execution
            mode_handler = self._get_mode_handler()
            return mode_handler.execute(ordered)

        # Track resource states for dependency resolution
        completed: Set[str] = set()
        failed: Set[str] = set()
        running: Set[str] = set()
        futures: Dict[str, Future] = {}
        
        # Build dependency map for quick lookup
        dep_map = {
            action.resource.address: set(action.resource.depends_on)
            for action in ordered
        }
        
        # Add already implemented resources to completed set
        for addr, resource in self.state_manager.state.resources.items():
            if resource.status == ResourceStatus.IMPLEMENTED:
                completed.add(addr)
        
        result = ApplyResult()
        lock = threading.Lock()
        
        def process_action(action: PlanAction) -> str:
            """Execute a single action and return the result status."""
            try:
                mode_handler = self._get_mode_handler()
                # Execute single action
                single_result = mode_handler.execute([action])
                
                with lock:
                    result.implemented.extend(single_result.implemented)
                    result.skipped.extend(single_result.skipped)
                    result.failed.extend(single_result.failed)
                    result.market_pending.extend(single_result.market_pending)
                
                if single_result.failed:
                    return "failed"
                elif single_result.implemented:
                    return "implemented"
                else:
                    return "skipped"
                    
            except Exception:
                with lock:
                    result.failed.append(action.resource.address)
                return "failed"
        
        def is_ready(action: PlanAction) -> bool:
            """Check if all dependencies for this action are satisfied."""
            deps = dep_map[action.resource.address]
            return deps.issubset(completed)
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            remaining_actions = list(ordered)
            
            while remaining_actions or futures:
                # Submit ready actions
                ready_actions = [
                    action for action in remaining_actions
                    if is_ready(action) and action.resource.address not in running
                ]
                
                for action in ready_actions:
                    addr = action.resource.address
                    running.add(addr)
                    futures[addr] = executor.submit(process_action, action)
                    remaining_actions.remove(action)
                
                # Wait for at least one to complete if we have futures
                if futures:
                    done_futures = {}
                    for addr, future in futures.items():
                        if future.done():
                            done_futures[addr] = future
                    
                    # If nothing is done yet, wait for the first one
                    if not done_futures:
                        next_future = next(iter(futures.values()))
                        next_future.result()  # Wait for it to complete
                        done_futures = {
                            addr: future for addr, future in futures.items()
                            if future.done()
                        }
                    
                    # Process completed futures
                    for addr, future in done_futures.items():
                        status = future.result()
                        running.remove(addr)
                        del futures[addr]
                        
                        if status == "implemented":
                            completed.add(addr)
                        elif status == "failed":
                            failed.add(addr)
                            # Skip dependent actions
                            self._skip_dependents(addr, remaining_actions, result, dep_map)
                        # "skipped" doesn't block dependents
                
                # Break if no progress can be made
                # Re-check readiness since completed set may have been updated
                if not futures and remaining_actions:
                    new_ready = [
                        action for action in remaining_actions
                        if is_ready(action) and action.resource.address not in running
                    ]
                    if not new_ready:
                        # Truly stuck — no futures running, nothing ready
                        for action in remaining_actions:
                            result.skipped.append(action.resource.address)
                        break
        
        return result
    
    def _skip_dependents(
        self, 
        failed_addr: str, 
        remaining_actions: list[PlanAction], 
        result: ApplyResult,
        dep_map: Dict[str, Set[str]]
    ) -> None:
        """Skip all actions that depend on the failed address."""
        to_skip = []
        for action in remaining_actions:
            if self._depends_on_transitively(action.resource.address, failed_addr, dep_map):
                to_skip.append(action)
                result.skipped.append(action.resource.address)
        
        for action in to_skip:
            remaining_actions.remove(action)
    
    def _depends_on_transitively(
        self, 
        addr: str, 
        target: str, 
        dep_map: Dict[str, Set[str]]
    ) -> bool:
        """Check if addr depends on target transitively."""
        if addr == target:
            return False  # A node doesn't depend on itself
            
        visited = set()
        
        def check_deps(current: str) -> bool:
            if current in visited:
                return False
            if current == target:
                return True
            visited.add(current)
            
            for dep in dep_map.get(current, set()):
                if check_deps(dep):
                    return True
            return False
        
        return check_deps(addr)

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

        # Auto, Hybrid, and Market modes accept project_root
        kwargs: dict = {
            "state_manager": self.state_manager,
            "context_registry": self.context_registry,
            "config": self.config,
        }
        if handler_cls in (AutoMode, HybridMode, MarketMode) and self.project_root:
            kwargs["project_root"] = self.project_root
            
        # MarketMode-specific parameters
        if handler_cls == MarketMode:
            kwargs.update({
                "market_url": self.config.market_url,
                "api_key": self.config.market_api_key,
                "dry_run": self.config.dry_run,
                "bounty": self.config.bounty,
            })

        return handler_cls(**kwargs)
