"""
CI/CD Output Formatters for terra4mice.

Provides structured output formats suitable for CI systems,
PR comments, and status badges.

Formats:
- JSON: Machine-readable output for CI pipelines
- Markdown: Human-readable tables for PR comments
- Badge: Shields.io compatible JSON for convergence badges
"""

import json
import re
from typing import Optional

from .models import Plan, Spec, State, ResourceStatus


def strip_ansi(text: str) -> str:
    """
    Strip ANSI escape codes from text.

    Args:
        text: String potentially containing ANSI codes

    Returns:
        Clean string without ANSI escape sequences
    """
    ansi_pattern = re.compile(r'\033\[[0-9;]*m')
    return ansi_pattern.sub('', text)


def _compute_convergence(spec: Spec, state: State) -> dict:
    """
    Compute convergence statistics from spec and state.

    Args:
        spec: The desired state specification
        state: The current state

    Returns:
        Dict with convergence metrics
    """
    total = len(spec.resources)
    if total == 0:
        return {
            "convergence": 100.0,
            "total_resources": 0,
            "implemented": 0,
            "partial": 0,
            "missing": 0,
        }

    implemented = 0
    partial = 0
    missing = 0

    for resource in spec.list():
        state_resource = state.get(resource.address)
        if state_resource is None or state_resource.status in (
            ResourceStatus.MISSING, ResourceStatus.BROKEN
        ):
            missing += 1
        elif state_resource.status == ResourceStatus.PARTIAL:
            partial += 1
        elif state_resource.status == ResourceStatus.IMPLEMENTED:
            implemented += 1
        elif state_resource.status == ResourceStatus.DEPRECATED:
            # Deprecated counts as implemented for convergence
            implemented += 1

    # Convergence: implemented = 100%, partial = 50%, missing = 0%
    convergence = ((implemented * 100.0) + (partial * 50.0)) / total
    convergence = round(convergence, 1)

    return {
        "convergence": convergence,
        "total_resources": total,
        "implemented": implemented,
        "partial": partial,
        "missing": missing,
    }


def format_plan_json(plan: Plan, spec: Spec, state: State) -> str:
    """
    Format plan as JSON for CI systems.

    Produces a structured JSON document with convergence metrics,
    resource counts, and action list.

    Args:
        plan: The execution plan
        spec: The desired state specification
        state: The current state

    Returns:
        JSON string
    """
    stats = _compute_convergence(spec, state)

    actions = []
    for action in plan.actions:
        if action.action == "no-op":
            continue
        actions.append({
            "action": action.action,
            "address": action.resource.address,
            "reason": action.reason,
        })

    output = {
        "version": "1",
        "convergence": stats["convergence"],
        "total_resources": stats["total_resources"],
        "implemented": stats["implemented"],
        "partial": stats["partial"],
        "missing": stats["missing"],
        "has_changes": plan.has_changes,
        "actions": actions,
    }

    return json.dumps(output, indent=2)


def format_plan_markdown(plan: Plan, spec: Spec, state: State) -> str:
    """
    Format plan as Markdown for PR comments.

    Produces a table of resources with status and actions,
    plus a convergence summary.

    Args:
        plan: The execution plan
        spec: The desired state specification
        state: The current state

    Returns:
        Markdown string
    """
    stats = _compute_convergence(spec, state)
    lines = []

    lines.append("## 🐭 terra4mice Plan")
    lines.append("")
    lines.append("| Resource | Status | Action |")
    lines.append("|----------|--------|--------|")

    # Build table rows from all spec resources (including no-ops)
    for action in plan.actions:
        if action.action == "delete":
            # Deleted resources aren't in spec, show separately
            continue

        address = action.resource.address
        state_resource = state.get(address)

        if state_resource and state_resource.status == ResourceStatus.IMPLEMENTED:
            status = "✅ implemented"
            action_text = "-"
        elif state_resource and state_resource.status == ResourceStatus.PARTIAL:
            status = "⚠️ partial"
            action_text = "~ complete"
        elif state_resource and state_resource.status == ResourceStatus.BROKEN:
            status = "🔴 broken"
            action_text = "~ fix"
        else:
            status = "❌ missing"
            action_text = "+ implement"

        lines.append(f"| {address} | {status} | {action_text} |")

    # Handle deletions separately
    for action in plan.deletes:
        lines.append(f"| {action.resource.address} | 🗑️ extra | - remove |")

    lines.append("")

    # Convergence summary
    conv = stats["convergence"]
    impl = stats["implemented"]
    total = stats["total_resources"]
    partial = stats["partial"]

    partial_text = f", {partial} partial" if partial > 0 else ""
    lines.append(
        f"**Convergence**: {conv}% ({impl}/{total} implemented{partial_text})"
    )
    lines.append("")

    # Action summary
    creates = len(plan.creates)
    updates = len(plan.updates)
    deletes = len(plan.deletes)

    if not plan.has_changes:
        lines.append("> No changes. State matches spec. ✅")
    else:
        parts = []
        if creates:
            parts.append(f"{creates} to create")
        if updates:
            parts.append(f"{updates} to update")
        if deletes:
            parts.append(f"{deletes} to delete")
        lines.append(f"> Plan: {', '.join(parts)}")

    lines.append("")
    return "\n".join(lines)


def format_convergence_badge(plan: Plan, spec: Spec, state: State) -> str:
    """
    Generate Shields.io compatible JSON badge data.

    The badge shows the convergence percentage with appropriate
    color coding:
    - Green (brightgreen): >= 90%
    - Yellow: >= 70%
    - Orange: >= 50%
    - Red: < 50%

    Args:
        plan: The execution plan
        spec: The desired state specification
        state: The current state

    Returns:
        JSON string for Shields.io endpoint badge
    """
    stats = _compute_convergence(spec, state)
    conv = stats["convergence"]

    if conv >= 90:
        color = "brightgreen"
    elif conv >= 70:
        color = "yellow"
    elif conv >= 50:
        color = "orange"
    else:
        color = "red"

    badge = {
        "schemaVersion": 1,
        "label": "convergence",
        "message": f"{conv}%",
        "color": color,
    }

    return json.dumps(badge, indent=2)
