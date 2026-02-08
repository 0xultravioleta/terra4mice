"""
Apply modes — handlers for interactive, auto, hybrid, and market execution.

Each mode implements an ``execute(ordered_actions)`` method that returns
an ``ApplyResult``.
"""

from __future__ import annotations

from typing import Optional

from ..models import PlanAction, ResourceStatus
from ..state_manager import StateManager
from ..contexts import ContextRegistry
from .runner import ApplyConfig, ApplyResult


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


# ── Stub Modes ────────────────────────────────────────────────────────

class AutoMode(BaseMode):
    """Automated mode — dispatches to AI agents. Coming in Phase 5.1."""

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        raise NotImplementedError("Coming in Phase 5.1")


class HybridMode(BaseMode):
    """Hybrid mode — AI suggests, human approves. Coming in Phase 5.1."""

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        raise NotImplementedError("Coming in Phase 5.1")


class MarketMode(BaseMode):
    """Market mode — post tasks to Execution Market. Coming in Phase 5.1."""

    def execute(self, ordered_actions: list[PlanAction]) -> ApplyResult:
        raise NotImplementedError("Coming in Phase 5.1")
