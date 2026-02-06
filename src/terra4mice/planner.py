"""
Planner - Compare spec vs state and generate execution plan.

The planner answers: "What needs to change to make state match spec?"

Similar to terraform plan
"""

from typing import Optional

from .models import Spec, State, Plan, PlanAction, Resource, ResourceStatus


def generate_plan(spec: Spec, state: State) -> Plan:
    """
    Generate a plan by comparing spec to state.

    Args:
        spec: Desired state (from spec file)
        state: Current state (from state file)

    Returns:
        Plan with actions needed
    """
    plan = Plan()

    # Find resources that need to be created or updated
    for spec_resource in spec.list():
        state_resource = state.get(spec_resource.address)

        if state_resource is None:
            # Resource doesn't exist in state - needs to be created
            action = PlanAction(
                action="create",
                resource=spec_resource,
                reason="Resource declared in spec but not in state"
            )
            plan.actions.append(action)

        elif state_resource.status == ResourceStatus.MISSING:
            # Resource exists in state but marked as missing
            action = PlanAction(
                action="create",
                resource=spec_resource,
                reason="Resource exists in state but is missing"
            )
            plan.actions.append(action)

        elif state_resource.status == ResourceStatus.PARTIAL:
            # Resource is partially implemented - needs completion
            action = PlanAction(
                action="update",
                resource=spec_resource,
                reason=state_resource.attributes.get(
                    "partial_reason",
                    "Resource is partially implemented"
                )
            )
            plan.actions.append(action)

        elif state_resource.status == ResourceStatus.BROKEN:
            # Resource is broken - needs fixing
            action = PlanAction(
                action="update",
                resource=spec_resource,
                reason=state_resource.attributes.get(
                    "broken_reason",
                    "Resource is broken and needs fixing"
                )
            )
            plan.actions.append(action)

        elif state_resource.status == ResourceStatus.IMPLEMENTED:
            # Resource is implemented - no action needed
            action = PlanAction(
                action="no-op",
                resource=spec_resource,
                reason="Resource is fully implemented"
            )
            plan.actions.append(action)

    # Find resources in state that are not in spec (might need deletion)
    for state_resource in state.list():
        if spec.get(state_resource.address) is None:
            action = PlanAction(
                action="delete",
                resource=state_resource,
                reason="Resource in state but not declared in spec"
            )
            plan.actions.append(action)

    # Sort actions: deletes first, then creates, then updates
    priority = {"delete": 0, "create": 1, "update": 2, "no-op": 3}
    plan.actions.sort(key=lambda a: (priority.get(a.action, 4), a.resource.address))

    return plan


def format_plan(plan: Plan, verbose: bool = False) -> str:
    """
    Format plan for human-readable output.

    Args:
        plan: The plan to format
        verbose: Include no-op resources

    Returns:
        Formatted string
    """
    lines = []
    lines.append("")
    lines.append("terra4mice will perform the following actions:")
    lines.append("")

    for action in plan.actions:
        if action.action == "no-op" and not verbose:
            continue

        symbol = action.symbol
        color_start = ""
        color_end = ""

        # ANSI colors for terminal
        if action.action == "create":
            color_start = "\033[32m"  # Green
            color_end = "\033[0m"
        elif action.action == "update":
            color_start = "\033[33m"  # Yellow
            color_end = "\033[0m"
        elif action.action == "delete":
            color_start = "\033[31m"  # Red
            color_end = "\033[0m"

        lines.append(f"{color_start}  {symbol} {action.resource.address}{color_end}")

        if action.reason and action.action != "no-op":
            lines.append(f"      # {action.reason}")

        # Show symbol summary in verbose mode
        if verbose and action.resource.symbols:
            syms = action.resource.symbols
            implemented = sum(1 for s in syms.values() if s.status == "implemented")
            missing_syms = [s for s in syms.values() if s.status == "missing"]
            total = len(syms)
            lines.append(f"      Symbols: {implemented}/{total} found")
            if missing_syms:
                for ms in missing_syms[:5]:
                    lines.append(f"        - {ms.qualified_name} (missing)")
                if len(missing_syms) > 5:
                    lines.append(f"        ... and {len(missing_syms) - 5} more")

    lines.append("")

    # Summary
    creates = len(plan.creates)
    updates = len(plan.updates)
    deletes = len(plan.deletes)

    if not plan.has_changes:
        lines.append("\033[32mNo changes. State matches spec.\033[0m")
    else:
        summary_parts = []
        if creates:
            summary_parts.append(f"\033[32m{creates} to create\033[0m")
        if updates:
            summary_parts.append(f"\033[33m{updates} to update\033[0m")
        if deletes:
            summary_parts.append(f"\033[31m{deletes} to delete\033[0m")

        lines.append(f"Plan: {', '.join(summary_parts)}.")

    lines.append("")

    return "\n".join(lines)


def check_dependencies(plan: Plan, state: State) -> list:
    """
    Check if dependencies are satisfied for resources to be created.

    Returns:
        List of blocked resources with reasons
    """
    blocked = []

    for action in plan.creates:
        resource = action.resource
        for dep_address in resource.depends_on:
            dep_resource = state.get(dep_address)

            if dep_resource is None:
                blocked.append({
                    "resource": resource.address,
                    "blocked_by": dep_address,
                    "reason": "Dependency not in state"
                })
            elif dep_resource.status != ResourceStatus.IMPLEMENTED:
                blocked.append({
                    "resource": resource.address,
                    "blocked_by": dep_address,
                    "reason": f"Dependency is {dep_resource.status.value}"
                })

    return blocked
