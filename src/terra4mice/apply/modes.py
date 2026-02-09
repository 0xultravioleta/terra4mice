"""
Apply modes — handlers for interactive, auto, hybrid, and market execution.

Each mode implements an ``execute(ordered_actions)`` method that returns
an ``ApplyResult``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from ..models import PlanAction, ResourceStatus
from ..state_manager import StateManager
from ..contexts import ContextRegistry
from .runner import ApplyConfig, ApplyResult
from .agents import AgentBackend, AgentResult, PromptBuilder, get_agent
from .verify import verify_implementation


# ── ANSI helpers ──────────────────────────────────────────────────────

class _C:
    """ANSI colour shortcuts."""

    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    MAGENTA = "\033[35m"
    WHITE  = "\033[37m"


def _status_icon(status: ResourceStatus) -> str:
    if status == ResourceStatus.IMPLEMENTED:
        return f"{_C.GREEN}✓{_C.RESET}"
    elif status == ResourceStatus.PARTIAL:
        return f"{_C.YELLOW}~{_C.RESET}"
    else:
        return f"{_C.RED}✗{_C.RESET}"


# ── Base class ────────────────────────────────────────────────────────

class BaseMode:
    """Base class for apply modes."""

    def __init__(
        self,
        state_manager: StateManager,
        context_registry: Optional[ContextRegistry] = None,
        config: Optional[ApplyConfig] = None,
    ):
        self.state_manager = state_manager
        self.context_registry = context_registry
        self.config = config or ApplyConfig()

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        raise NotImplementedError


# ── Interactive Mode (Enhanced) ───────────────────────────────────────

class InteractiveMode(BaseMode):
    """
    Enhanced interactive apply mode.

    Shows dependency status, attributes, suggested files, and context
    from other agents before prompting for action.
    """

    # Allow tests to inject an input function
    _input_fn = staticmethod(input)

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        result = ApplyResult()
        total = len(ordered_actions)

        for idx, action in enumerate(ordered_actions, 1):
            self._print_header(idx, total, action)
            self._print_dependencies(action)
            self._print_attributes(action)
            self._print_files(action)
            self._print_context(action)
            self._print_options()

            response = self._prompt()

            if response == "q":
                print(f"\n{_C.YELLOW}Aborted.{_C.RESET}")
                break
            elif response == "s":
                result.skipped.append(action.resource.address)
                print(f"{_C.DIM}Skipped.{_C.RESET}")
            elif response == "i":
                self._do_implement(action, result)
            elif response == "p":
                self._do_partial(action, result)
            elif response == "a":
                print(f"{_C.MAGENTA}AI-assist coming in Phase 5.1{_C.RESET}")
                result.skipped.append(action.resource.address)
            elif response == "m":
                print(f"{_C.MAGENTA}Market posting coming in Phase 5.1{_C.RESET}")
                result.market_pending.append(action.resource.address)
            else:
                # Unknown input → skip
                result.skipped.append(action.resource.address)

        self._print_summary(result)
        return result

    # ── Display helpers ───────────────────────────────────────────────

    def _print_header(self, idx: int, total: int, action: PlanAction) -> None:
        sym_colours = {
            "create": _C.GREEN,
            "update": _C.YELLOW,
            "delete": _C.RED,
        }
        colour = sym_colours.get(action.action, _C.WHITE)
        print(f"\n{'═' * 60}")
        print(
            f" {_C.BOLD}Action {idx}/{total}:{_C.RESET} "
            f"{colour}{action.symbol} {action.action} "
            f"{action.resource.address}{_C.RESET}"
        )
        print(f"{'═' * 60}")
        if action.reason:
            print(f"\n {_C.DIM}{action.reason}{_C.RESET}")

    def _print_dependencies(self, action: PlanAction) -> None:
        deps = action.resource.depends_on
        if not deps:
            return
        print(f"\n {_C.BOLD}Dependencies:{_C.RESET}")
        for dep_addr in deps:
            dep = self.state_manager.state.get(dep_addr)
            if dep is None:
                icon = f"{_C.RED}✗{_C.RESET}"
                status_str = "not in state"
            else:
                icon = _status_icon(dep.status)
                status_str = dep.status.value
            print(f"   {icon} {dep_addr} ({status_str})")

    def _print_attributes(self, action: PlanAction) -> None:
        attrs = action.resource.attributes
        if not attrs:
            return
        print(f"\n {_C.BOLD}Attributes:{_C.RESET}")
        for key, val in attrs.items():
            print(f"   - {key}: {val}")

    def _print_files(self, action: PlanAction) -> None:
        files = action.resource.files
        if not files:
            # Try attributes for file hints
            suggested = action.resource.attributes.get("files", [])
            if isinstance(suggested, list) and suggested:
                files = suggested
        if not files:
            return
        print(f"\n {_C.BOLD}Suggested files:{_C.RESET}")
        for f in files:
            print(f"   - {f}")

    def _print_context(self, action: PlanAction) -> None:
        if self.context_registry is None:
            return
        contexts = self.context_registry.get_resource_contexts(
            action.resource.address
        )
        if not contexts:
            return
        print(f"\n {_C.BOLD}Who knows about this:{_C.RESET}")
        for ctx in contexts:
            status = ctx.status().value
            age = ctx.age_str()
            print(f"   {_C.CYAN}{ctx.agent}{_C.RESET}: {status} ({age})")
            if ctx.knowledge:
                for k in ctx.knowledge[:3]:
                    print(f"     💡 {k}")

    def _print_options(self) -> None:
        print(f"\n{'─' * 60}")
        print(
            f" [{_C.GREEN}i{_C.RESET}]mplement  "
            f"[{_C.YELLOW}p{_C.RESET}]artial  "
            f"[{_C.DIM}s{_C.RESET}]kip  "
            f"[{_C.CYAN}a{_C.RESET}]i-assist  "
            f"[{_C.MAGENTA}m{_C.RESET}]arket  "
            f"[{_C.RED}q{_C.RESET}]uit"
        )

    def _prompt(self) -> str:
        try:
            return self._input_fn("→ ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"

    # ── Action handlers ───────────────────────────────────────────────

    def _do_implement(self, action: PlanAction, result: ApplyResult) -> None:
        try:
            files_raw = self._input_fn(
                "Files that implement this (comma-separated, or empty): "
            )
        except (EOFError, KeyboardInterrupt):
            files_raw = ""
        files_list = [f.strip() for f in files_raw.split(",") if f.strip()]
        self.state_manager.mark_created(
            action.resource.address, files=files_list
        )
        self.state_manager.save()
        result.implemented.append(action.resource.address)
        print(
            f"{_C.GREEN}✓ Marked as implemented: "
            f"{action.resource.address}{_C.RESET}"
        )
        self._update_context(action, "implemented", files_list)

    def _do_partial(self, action: PlanAction, result: ApplyResult) -> None:
        try:
            reason = self._input_fn("Why is it partial? ")
        except (EOFError, KeyboardInterrupt):
            reason = ""
        self.state_manager.mark_partial(
            action.resource.address, reason=reason
        )
        self.state_manager.save()
        result.implemented.append(action.resource.address)
        print(
            f"{_C.YELLOW}~ Marked as partial: "
            f"{action.resource.address}{_C.RESET}"
        )
        self._update_context(action, "partial")

    def _update_context(
        self,
        action: PlanAction,
        status: str,
        files: list[str] | None = None,
    ) -> None:
        if self.context_registry is None or self.config.agent is None:
            return
        self.context_registry.register_context(
            agent=self.config.agent,
            resource=action.resource.address,
            files_touched=files or [],
            contributed_status=status,
        )

    # ── Summary ───────────────────────────────────────────────────────

    def _print_summary(self, result: ApplyResult) -> None:
        print(f"\n{'═' * 60}")
        print(f" {_C.BOLD}Apply Summary{_C.RESET}")
        print(f"{'═' * 60}")
        if result.implemented:
            print(f" {_C.GREEN}Implemented: {len(result.implemented)}{_C.RESET}")
            for addr in result.implemented:
                print(f"   ✓ {addr}")
        if result.skipped:
            print(f" {_C.DIM}Skipped: {len(result.skipped)}{_C.RESET}")
        if result.failed:
            print(f" {_C.RED}Failed: {len(result.failed)}{_C.RESET}")
        if result.market_pending:
            print(f" {_C.MAGENTA}Market pending: {len(result.market_pending)}{_C.RESET}")
        print()


# ── Auto Mode ─────────────────────────────────────────────────────────

class AutoMode(BaseMode):
    """
    Automated mode — dispatches resources to AI coding agents.

    For each action in DAG order:
    1. Build a context-rich prompt (resource details, deps, files, agent context)
    2. Dispatch to the configured AI agent (claude-code, codex, etc.)
    3. Verify the result (check files exist, state updated)
    4. Update state + context tracking
    5. Continue or abort on failure
    """

    def __init__(
        self,
        state_manager: StateManager,
        context_registry: Optional[ContextRegistry] = None,
        config: Optional[ApplyConfig] = None,
        agent: Optional[AgentBackend] = None,
        project_root: Optional[str | Path] = None,
    ):
        super().__init__(state_manager, context_registry, config)
        self._agent = agent
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._prompt_builder = PromptBuilder(
            project_root=self._project_root,
            state_manager=state_manager,
            context_registry=context_registry,
        )

    @property
    def agent(self) -> AgentBackend:
        """Resolve the agent backend (lazy init from config)."""
        if self._agent is None:
            agent_name = self.config.agent or "claude-code"
            self._agent = get_agent(agent_name)
        return self._agent

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        result = ApplyResult()
        total = len(ordered_actions)
        timeout = self.config.timeout_minutes * 60 if self.config.timeout_minutes else 0

        for idx, action in enumerate(ordered_actions, 1):
            self._print_auto_header(idx, total, action)

            # Build prompt
            prompt = self._prompt_builder.build(action)

            # Dispatch to agent
            print(f"  {_C.CYAN}⚡ Dispatching to {self.agent.name}...{_C.RESET}")
            agent_timeout = timeout if timeout else 300  # 5 min default
            agent_result = self.agent.execute(
                prompt=prompt,
                project_root=self._project_root,
                timeout_seconds=agent_timeout,
            )

            # Process result
            if agent_result.success:
                self._handle_success(action, agent_result, result)
            else:
                self._handle_failure(action, agent_result, result)

                # In auto mode, continue on failure (don't abort)
                if self.config.parallel < 1:
                    break

        self._print_auto_summary(result)
        return result

    def _print_auto_header(
        self, idx: int, total: int, action: PlanAction
    ) -> None:
        sym_colours = {
            "create": _C.GREEN,
            "update": _C.YELLOW,
            "delete": _C.RED,
        }
        colour = sym_colours.get(action.action, _C.WHITE)
        print(
            f"\n{_C.BOLD}[{idx}/{total}]{_C.RESET} "
            f"{colour}{action.symbol} {action.action} "
            f"{action.resource.address}{_C.RESET}"
        )
        if action.reason:
            print(f"  {_C.DIM}{action.reason}{_C.RESET}")

    def _handle_success(
        self,
        action: PlanAction,
        agent_result: AgentResult,
        apply_result: ApplyResult,
    ) -> None:
        addr = action.resource.address

        # Verify implementation
        from .verify import VerificationLevel
        verify_level = VerificationLevel.BASIC
        if hasattr(self.config, 'verify_level'):
            verify_level = VerificationLevel(self.config.verify_level)
        verification = verify_implementation(
            action.resource, self._project_root, verify_level
        )

        if verification.passed:
            # Mark as implemented in state
            self.state_manager.mark_created(
                addr, files=agent_result.all_files
            )
            self.state_manager.save()
            apply_result.implemented.append(addr)
            print(
                f"  {_C.GREEN}✓ Implemented{_C.RESET} "
                f"({agent_result.duration_seconds:.1f}s, "
                f"verified: {verification.score:.0%})"
            )
        else:
            # Agent succeeded but verification failed → partial
            self.state_manager.mark_partial(
                addr, reason="Agent completed but verification failed"
            )
            self.state_manager.save()
            apply_result.implemented.append(addr)
            print(
                f"  {_C.YELLOW}~ Partial{_C.RESET} "
                f"({agent_result.duration_seconds:.1f}s, "
                f"verified: {verification.score:.0%})"
            )
            if verification.missing_attributes:
                for miss in verification.missing_attributes[:3]:
                    print(f"    {_C.DIM}⚠ {miss}{_C.RESET}")

        # Update context tracking
        self._update_context(action, agent_result)

        # Auto-commit if configured
        if self.config.auto_commit:
            self._git_commit(addr, action.action)

    def _handle_failure(
        self,
        action: PlanAction,
        agent_result: AgentResult,
        apply_result: ApplyResult,
    ) -> None:
        addr = action.resource.address
        apply_result.failed.append(addr)
        print(
            f"  {_C.RED}✗ Failed{_C.RESET} "
            f"({agent_result.duration_seconds:.1f}s)"
        )
        if agent_result.error:
            # Truncate error for display
            err = agent_result.error[:200]
            print(f"    {_C.DIM}{err}{_C.RESET}")

    def _update_context(
        self, action: PlanAction, agent_result: AgentResult
    ) -> None:
        if self.context_registry is None:
            return
        agent_name = self.agent.name
        self.context_registry.register_context(
            agent=agent_name,
            resource=action.resource.address,
            files_touched=agent_result.all_files,
            contributed_status="implemented" if agent_result.success else "failed",
        )

    def _git_commit(self, addr: str, action_type: str) -> None:
        """Auto-commit after successful implementation."""
        import subprocess

        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self._project_root),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m",
                 f"terra4mice auto: {action_type} {addr}"],
                cwd=str(self._project_root),
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _print_auto_summary(self, result: ApplyResult) -> None:
        print(f"\n{'═' * 60}")
        print(f" {_C.BOLD}Auto Apply Summary{_C.RESET}")
        print(f"{'═' * 60}")
        if result.implemented:
            print(
                f" {_C.GREEN}✓ Implemented: "
                f"{len(result.implemented)}{_C.RESET}"
            )
        if result.failed:
            print(
                f" {_C.RED}✗ Failed: {len(result.failed)}{_C.RESET}"
            )
        if result.skipped:
            print(f" {_C.DIM}Skipped: {len(result.skipped)}{_C.RESET}")
        print()


# ── Hybrid Mode ───────────────────────────────────────────────────────

class HybridMode(BaseMode):
    """
    Hybrid mode — AI generates implementation, human reviews.

    For each action:
    1. AI agent implements the resource
    2. Show diff/output to human
    3. Human approves (accept), rejects (revert), or edits
    4. State updated based on decision
    """

    # Allow tests to inject an input function
    _input_fn = staticmethod(input)

    def __init__(
        self,
        state_manager: StateManager,
        context_registry: Optional[ContextRegistry] = None,
        config: Optional[ApplyConfig] = None,
        agent: Optional[AgentBackend] = None,
        project_root: Optional[str | Path] = None,
    ):
        super().__init__(state_manager, context_registry, config)
        self._agent = agent
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._prompt_builder = PromptBuilder(
            project_root=self._project_root,
            state_manager=state_manager,
            context_registry=context_registry,
        )

    @property
    def agent(self) -> AgentBackend:
        if self._agent is None:
            agent_name = self.config.agent or "claude-code"
            self._agent = get_agent(agent_name)
        return self._agent

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        result = ApplyResult()
        total = len(ordered_actions)

        for idx, action in enumerate(ordered_actions, 1):
            self._print_hybrid_header(idx, total, action)

            # Build and dispatch
            prompt = self._prompt_builder.build(action)
            print(f"  {_C.CYAN}⚡ AI implementing...{_C.RESET}")

            agent_result = self.agent.execute(
                prompt=prompt,
                project_root=self._project_root,
                timeout_seconds=300,
            )

            if not agent_result.success:
                print(
                    f"  {_C.RED}AI failed:{_C.RESET} "
                    f"{agent_result.error[:100]}"
                )
                self._print_hybrid_options(failed=True)
                resp = self._prompt_hybrid(failed=True)

                if resp == "s":
                    result.skipped.append(action.resource.address)
                elif resp == "m":
                    # Fall back to manual (interactive mode for this action)
                    self._do_manual(action, result)
                elif resp == "q":
                    break
                continue

            # Show AI output
            if agent_result.output:
                print(f"\n  {_C.BOLD}AI Output:{_C.RESET}")
                for line in agent_result.output.splitlines()[:20]:
                    print(f"  {_C.DIM}│ {line}{_C.RESET}")
                if len(agent_result.output.splitlines()) > 20:
                    remaining = len(agent_result.output.splitlines()) - 20
                    print(f"  {_C.DIM}│ ... ({remaining} more lines){_C.RESET}")

            # Verify
            from .verify import VerificationLevel
            verify_level = VerificationLevel.BASIC
            if hasattr(self.config, 'verify_level'):
                verify_level = VerificationLevel(self.config.verify_level)
            verification = verify_implementation(
                action.resource, self._project_root, verify_level
            )
            score_str = f"verification: {verification.score:.0%}"
            print(f"  {_C.CYAN}📋 {score_str}{_C.RESET}")

            # Ask human
            self._print_hybrid_options(failed=False)
            resp = self._prompt_hybrid(failed=False)

            if resp == "a":
                # Accept AI implementation
                self.state_manager.mark_created(
                    action.resource.address,
                    files=agent_result.all_files,
                )
                self.state_manager.save()
                result.implemented.append(action.resource.address)
                print(f"  {_C.GREEN}✓ Accepted{_C.RESET}")
            elif resp == "e":
                # Accept but mark as partial (human will edit)
                self.state_manager.mark_partial(
                    action.resource.address,
                    reason="AI implementation accepted with edits needed",
                )
                self.state_manager.save()
                result.implemented.append(action.resource.address)
                print(f"  {_C.YELLOW}~ Accepted (needs edits){_C.RESET}")
            elif resp == "r":
                # Reject — revert changes
                result.failed.append(action.resource.address)
                print(f"  {_C.RED}✗ Rejected{_C.RESET}")
            elif resp == "s":
                result.skipped.append(action.resource.address)
                print(f"  {_C.DIM}Skipped{_C.RESET}")
            elif resp == "q":
                break

        self._print_hybrid_summary(result)
        return result

    def _print_hybrid_header(
        self, idx: int, total: int, action: PlanAction
    ) -> None:
        sym_colours = {"create": _C.GREEN, "update": _C.YELLOW, "delete": _C.RED}
        colour = sym_colours.get(action.action, _C.WHITE)
        print(f"\n{'═' * 60}")
        print(
            f" {_C.BOLD}[{idx}/{total}] Hybrid:{_C.RESET} "
            f"{colour}{action.symbol} {action.action} "
            f"{action.resource.address}{_C.RESET}"
        )
        print(f"{'═' * 60}")

    def _print_hybrid_options(self, failed: bool = False) -> None:
        print(f"\n{'─' * 60}")
        if failed:
            print(
                f" [{_C.DIM}s{_C.RESET}]kip  "
                f"[{_C.GREEN}m{_C.RESET}]anual  "
                f"[{_C.RED}q{_C.RESET}]uit"
            )
        else:
            print(
                f" [{_C.GREEN}a{_C.RESET}]ccept  "
                f"[{_C.YELLOW}e{_C.RESET}]dit (accept+partial)  "
                f"[{_C.RED}r{_C.RESET}]eject  "
                f"[{_C.DIM}s{_C.RESET}]kip  "
                f"[{_C.RED}q{_C.RESET}]uit"
            )

    def _prompt_hybrid(self, failed: bool = False) -> str:
        try:
            return self._input_fn("→ ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"

    def _do_manual(self, action: PlanAction, result: ApplyResult) -> None:
        """Fall back to manual implementation for one action."""
        addr = action.resource.address
        try:
            files_raw = self._input_fn(
                f"Implement {addr} manually. Files (comma-sep): "
            )
        except (EOFError, KeyboardInterrupt):
            result.skipped.append(addr)
            return
        files_list = [f.strip() for f in files_raw.split(",") if f.strip()]
        self.state_manager.mark_created(addr, files=files_list)
        self.state_manager.save()
        result.implemented.append(addr)
        print(f"  {_C.GREEN}✓ Manually implemented{_C.RESET}")

    def _print_hybrid_summary(self, result: ApplyResult) -> None:
        print(f"\n{'═' * 60}")
        print(f" {_C.BOLD}Hybrid Apply Summary{_C.RESET}")
        print(f"{'═' * 60}")
        if result.implemented:
            print(
                f" {_C.GREEN}✓ Implemented: "
                f"{len(result.implemented)}{_C.RESET}"
            )
        if result.failed:
            print(f" {_C.RED}✗ Rejected: {len(result.failed)}{_C.RESET}")
        if result.skipped:
            print(f" {_C.DIM}Skipped: {len(result.skipped)}{_C.RESET}")
        print()


# ── Market Mode ───────────────────────────────────────────────────────

class MarketMode(BaseMode):
    """
    Market mode — post implementation tasks to Execution Market.

    Converts resources into bounty tasks that human developers can claim
    and implement. Integrates with x402 for payment.
    """

    def __init__(
        self,
        state_manager: StateManager,
        context_registry: Optional[ContextRegistry] = None,
        config: Optional[ApplyConfig] = None,
        market_url: Optional[str] = None,
        api_key: Optional[str] = None,
        dry_run: bool = False,
        bounty: Optional[float] = None,
        project_root: Optional[str | Path] = None,
    ):
        super().__init__(state_manager, context_registry, config)
        self._market_url = market_url or "https://api.execution.market"
        self._api_key = api_key
        self._dry_run = dry_run
        self._bounty = bounty
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._prompt_builder = PromptBuilder(
            project_root=self._project_root,
            state_manager=state_manager,
            context_registry=context_registry,
        )
        
        # Initialize market client
        from .market_client import MarketClient
        self._market_client = MarketClient(
            api_key=self._api_key,
            base_url=self._market_url,
            dry_run=self._dry_run,
        )

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        result = ApplyResult()
        total = len(ordered_actions)

        for idx, action in enumerate(ordered_actions, 1):
            addr = action.resource.address
            print(
                f"\n{_C.BOLD}[{idx}/{total}]{_C.RESET} "
                f"{_C.MAGENTA}📋 Posting to market: {addr}{_C.RESET}"
            )

            # Build task description from prompt
            prompt = self._prompt_builder.build(action)

            task = self._build_market_task(action, prompt)
            posted = self._post_to_market(task)

            if posted:
                result.market_pending.append(addr)
                print(
                    f"  {_C.MAGENTA}✓ Posted to market{_C.RESET}"
                )
            else:
                result.failed.append(addr)
                print(f"  {_C.RED}✗ Failed to post{_C.RESET}")

        self._print_market_summary(result)
        return result

    def _build_market_task(
        self, action: PlanAction, prompt: str
    ) -> dict:
        """Build an Execution Market task from a plan action."""
        resource = action.resource
        
        # Start with base task
        task = {
            "title": f"[terra4mice] {action.action} {resource.address}",
            "description": prompt,
            "task_type": "code_implementation",
            "tags": [
                "terra4mice",
                resource.type,
                action.action,
            ],
            "metadata": {
                "resource_address": resource.address,
                "resource_type": resource.type,
                "action": action.action,
                "attributes": resource.attributes,
                "dependencies": resource.depends_on,
            },
        }
        
        # Add bounty if configured
        if self._bounty is not None:
            task["bounty"] = self._bounty
        elif "bounty" in resource.attributes:
            task["bounty"] = resource.attributes["bounty"]
            
        # Add requirements from resource attributes
        if "requirements" in resource.attributes:
            task["requirements"] = resource.attributes["requirements"]
        
        return task

    def _post_to_market(self, task: dict) -> bool:
        """
        Post a task to Execution Market.

        Returns True if posted successfully.
        """
        try:
            market_task = self._market_client.create_task(task)
            print(f"    {_C.DIM}Task ID: {market_task.id}{_C.RESET}")
            print(f"    {_C.DIM}Status: {market_task.status}{_C.RESET}")
            if "bounty" in task:
                print(f"    {_C.DIM}Bounty: ${task['bounty']}{_C.RESET}")
            return True
        except Exception as e:
            print(f"    {_C.RED}Error: {e}{_C.RESET}")
            return False

    def _print_market_summary(self, result: ApplyResult) -> None:
        print(f"\n{'═' * 60}")
        print(f" {_C.BOLD}Market Apply Summary{_C.RESET}")
        print(f"{'═' * 60}")
        if result.market_pending:
            print(
                f" {_C.MAGENTA}📋 Posted: "
                f"{len(result.market_pending)}{_C.RESET}"
            )
        if result.failed:
            print(f" {_C.RED}✗ Failed: {len(result.failed)}{_C.RESET}")
        print()
