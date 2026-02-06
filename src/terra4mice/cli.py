#!/usr/bin/env python3
"""
terra4mice CLI - State-Driven Development Framework

Usage:
    terra4mice init          Create spec and state files
    terra4mice plan          Show what needs to be done
    terra4mice refresh       Auto-detect resources from codebase
    terra4mice state list    List all resources in state
    terra4mice state show    Show details of a resource
    terra4mice mark          Mark a resource as created/partial/broken
    terra4mice apply         Interactive apply loop
    terra4mice ci            Run refresh + plan in CI mode
"""

import sys
import argparse
from pathlib import Path

from . import __version__
from .spec_parser import load_spec, validate_spec, create_example_spec, DEFAULT_SPEC_FILE
from .state_manager import StateManager, DEFAULT_STATE_FILE
from .planner import generate_plan, format_plan, check_dependencies
from .models import ResourceStatus
from .inference import InferenceEngine, InferenceConfig, format_inference_report
from .ci import format_plan_json, format_plan_markdown, strip_ansi


def cmd_init(args):
    """Initialize terra4mice in current directory."""
    spec_path = Path.cwd() / DEFAULT_SPEC_FILE
    state_path = Path.cwd() / DEFAULT_STATE_FILE

    if spec_path.exists() and not args.force:
        print(f"Spec file already exists: {spec_path}")
        print("Use --force to overwrite")
        return 1

    # Create example spec
    create_example_spec(spec_path)
    print(f"Created: {spec_path}")

    # Create empty state if doesn't exist
    if not state_path.exists():
        sm = StateManager(state_path)
        sm.save()
        print(f"Created: {state_path}")

    print()
    print("terra4mice initialized!")
    print()
    print("Next steps:")
    print(f"  1. Edit {DEFAULT_SPEC_FILE} to define your desired state")
    print("  2. Run 'terra4mice plan' to see what needs to be done")
    print("  3. Implement features and mark them with 'terra4mice mark'")

    return 0


def cmd_plan(args):
    """Show execution plan."""
    # Handle --ci shorthand
    fmt = getattr(args, 'format', 'text') or 'text'
    no_color = getattr(args, 'no_color', False)
    detailed_exitcode = getattr(args, 'detailed_exitcode', False)

    if getattr(args, 'ci', False):
        fmt = 'json'
        no_color = True
        detailed_exitcode = True

    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run 'terra4mice init' to create a spec file")
        return 1

    # Validate spec
    errors = validate_spec(spec)
    if errors:
        print("Spec validation errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    # Load state
    sm = StateManager(args.state)
    sm.load()

    # Generate plan
    plan = generate_plan(spec, sm.state)

    # Check dependencies
    if args.check_deps:
        blocked = check_dependencies(plan, sm.state)
        if blocked:
            print("\nBlocked resources (dependencies not met):")
            for b in blocked:
                print(f"  {b['resource']} blocked by {b['blocked_by']}: {b['reason']}")
            print()

    # Format output based on --format
    if fmt == 'json':
        output = format_plan_json(plan, spec, sm.state)
    elif fmt == 'markdown':
        output = format_plan_markdown(plan, spec, sm.state)
    else:
        output = format_plan(plan, verbose=args.verbose)
        if no_color:
            output = strip_ansi(output)

    print(output)

    # Return non-zero if there are changes (useful for CI)
    if detailed_exitcode and plan.has_changes:
        return 2

    return 0


def cmd_ci(args):
    """
    Run refresh + plan in CI mode.

    Combines refresh and plan into a single command optimized
    for CI/CD pipelines.
    """
    fmt = getattr(args, 'format', 'json') or 'json'

    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        if fmt == 'json':
            import json
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}")
        return 1

    # Validate spec
    errors = validate_spec(spec)
    if errors:
        if fmt == 'json':
            import json
            print(json.dumps({"error": "Spec validation failed", "details": errors}))
        else:
            print("Spec validation errors:")
            for error in errors:
                print(f"  - {error}")
        return 1

    # Load state
    sm = StateManager(args.state)
    sm.load()

    # Run refresh if root dir is available
    root_dir = Path(args.root) if args.root else Path.cwd()
    if root_dir.exists():
        config = InferenceConfig()
        config.root_dir = root_dir
        engine = InferenceEngine(config)
        results = engine.infer_all(spec)
        updated = engine.apply_to_state(results, sm.state, only_missing=True)
        if updated:
            sm.save()

    # Generate plan
    plan = generate_plan(spec, sm.state)

    # Format output
    if fmt == 'json':
        output = format_plan_json(plan, spec, sm.state)
    elif fmt == 'markdown':
        output = format_plan_markdown(plan, spec, sm.state)
    else:
        output = format_plan(plan, verbose=True)
        output = strip_ansi(output)  # Always strip in CI

    # Write to output file if specified
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')

    # Write PR comment markdown
    if args.comment:
        comment_output = format_plan_markdown(plan, spec, sm.state)
        Path(args.comment).write_text(comment_output, encoding='utf-8')

    # Print to stdout
    print(output)

    # Determine exit code
    from .ci import _compute_convergence
    stats = _compute_convergence(spec, sm.state)
    convergence = stats["convergence"]

    if args.fail_under is not None and convergence < args.fail_under:
        return 2

    if args.fail_on_incomplete and convergence < 100.0:
        return 2

    if plan.has_changes:
        return 2

    return 0


def cmd_state_list(args):
    """List all resources in state."""
    sm = StateManager(args.state)
    sm.load()

    resources = sm.list(args.type)

    if not resources:
        print("No resources in state.")
        print("Use 'terra4mice mark <address>' to add resources.")
        return 0

    for resource in resources:
        status_color = {
            ResourceStatus.IMPLEMENTED: "\033[32m",  # Green
            ResourceStatus.PARTIAL: "\033[33m",      # Yellow
            ResourceStatus.BROKEN: "\033[31m",       # Red
            ResourceStatus.MISSING: "\033[90m",      # Gray
            ResourceStatus.DEPRECATED: "\033[90m",   # Gray
        }
        color = status_color.get(resource.status, "")
        reset = "\033[0m" if color else ""

        print(f"{color}{resource.address}{reset}")
        if args.verbose:
            print(f"    status: {resource.status.value}")
            if resource.files:
                print(f"    files: {', '.join(resource.files)}")
            if resource.symbols:
                impl = sum(1 for s in resource.symbols.values() if s.status == "implemented")
                print(f"    symbols: {impl}/{len(resource.symbols)}")

    return 0


def cmd_state_show(args):
    """Show details of a specific resource."""
    sm = StateManager(args.state)
    sm.load()

    resource = sm.show(args.address)

    if resource is None:
        print(f"Resource not found: {args.address}")
        return 1

    print(f"# {resource.address}")
    print(f"type     = \"{resource.type}\"")
    print(f"name     = \"{resource.name}\"")
    print(f"status   = \"{resource.status.value}\"")

    if resource.files:
        print(f"files    = {resource.files}")
    if resource.tests:
        print(f"tests    = {resource.tests}")
    if resource.depends_on:
        print(f"depends_on = {resource.depends_on}")
    if resource.attributes:
        print(f"attributes = {resource.attributes}")

    if resource.symbols:
        implemented = sum(1 for s in resource.symbols.values() if s.status == "implemented")
        missing_count = sum(1 for s in resource.symbols.values() if s.status == "missing")
        total = len(resource.symbols)
        print(f"symbols  = {total} ({implemented} implemented, {missing_count} missing)")
        for qname, sym in sorted(resource.symbols.items()):
            status_indicator = "" if sym.status == "implemented" else " [MISSING]"
            loc = f"lines {sym.line_start}-{sym.line_end}" if sym.line_start else ""
            file_info = f" ({sym.file})" if sym.file else ""
            print(f"  {qname:<35} {sym.kind:<10} {loc}{file_info}{status_indicator}")

    if resource.created_at:
        print(f"created_at = \"{resource.created_at.isoformat()}\"")
    if resource.updated_at:
        print(f"updated_at = \"{resource.updated_at.isoformat()}\"")

    return 0


def cmd_state_rm(args):
    """Remove a resource from state."""
    sm = StateManager(args.state)
    sm.load()

    resource = sm.remove(args.address)

    if resource is None:
        print(f"Resource not found: {args.address}")
        return 1

    sm.save()
    print(f"Removed: {args.address}")

    return 0


def cmd_mark(args):
    """Mark a resource status."""
    sm = StateManager(args.state)
    sm.load()

    address = args.address
    status = args.status or "implemented"

    # Parse files if provided
    files = args.files.split(",") if args.files else []
    tests = args.tests.split(",") if args.tests else []

    if status == "implemented":
        resource = sm.mark_created(address, files=files, tests=tests)
    elif status == "partial":
        resource = sm.mark_partial(address, reason=args.reason or "")
    elif status == "broken":
        resource = sm.mark_broken(address, reason=args.reason or "")
    else:
        print(f"Unknown status: {status}")
        return 1

    sm.save()

    print(f"Marked {resource.address} as {resource.status.value}")

    return 0


def cmd_refresh(args):
    """
    Auto-detect resources from codebase and update state.

    This scans the codebase looking for evidence that resources
    defined in the spec have been implemented.
    """
    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Run 'terra4mice init' to create a spec file")
        return 1

    # Load existing state
    sm = StateManager(args.state)
    sm.load()

    # Configure inference
    config = InferenceConfig()
    config.root_dir = Path(args.root) if args.root else Path.cwd()

    if args.source_dirs:
        config.source_dirs = args.source_dirs.split(",")

    # Run inference
    import sys as _sys

    def _progress(current, total, resource):
        _sys.stderr.write(f"\rScanning... [{current}/{total}] {resource.address:<40}")
        _sys.stderr.flush()

    print(f"Scanning {config.root_dir} for resources...", file=_sys.stderr)

    engine = InferenceEngine(config)
    results = engine.infer_all(spec, progress_callback=_progress)
    _sys.stderr.write("\r" + " " * 70 + "\r")
    _sys.stderr.flush()

    # Show report
    print(format_inference_report(results))

    # Apply to state if not dry-run
    if not args.dry_run:
        updated = engine.apply_to_state(
            results,
            sm.state,
            only_missing=not args.force
        )

        if updated:
            sm.save()
            print(f"\nUpdated {len(updated)} resources in state:")
            for addr in updated:
                print(f"  - {addr}")
        else:
            print("\nNo changes to state.")

        # Show updated plan
        if args.show_plan:
            print()
            plan = generate_plan(spec, sm.state)
            print(format_plan(plan))
    else:
        print("\n(Dry run - state not modified)")

    return 0


def cmd_apply(args):
    """Interactive apply loop."""
    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1

    sm = StateManager(args.state)
    sm.load()

    plan = generate_plan(spec, sm.state)

    if not plan.has_changes:
        print("\033[32mNo changes. State matches spec.\033[0m")
        return 0

    print(format_plan(plan))

    # Interactive loop
    for action in plan.actions:
        if action.action == "no-op":
            continue

        print(f"\n{'='*60}")
        print(f"Next: {action.symbol} {action.resource.address}")
        print(f"      {action.reason}")
        print()

        if action.resource.depends_on:
            print(f"Dependencies: {action.resource.depends_on}")

        if action.resource.attributes:
            print(f"Attributes: {action.resource.attributes}")

        print()
        response = input("Action: [i]mplement, [p]artial, [s]kip, [q]uit? ").lower()

        if response == 'q':
            print("Aborted.")
            return 0
        elif response == 's':
            print("Skipped.")
            continue
        elif response == 'i':
            files = input("Files that implement this (comma-separated, or empty): ")
            files_list = [f.strip() for f in files.split(",") if f.strip()]
            sm.mark_created(action.resource.address, files=files_list)
            sm.save()
            print(f"\033[32mMarked as implemented: {action.resource.address}\033[0m")
        elif response == 'p':
            reason = input("Why is it partial? ")
            sm.mark_partial(action.resource.address, reason=reason)
            sm.save()
            print(f"\033[33mMarked as partial: {action.resource.address}\033[0m")

    # Final plan
    print(f"\n{'='*60}")
    print("Apply complete. Final state:")
    plan = generate_plan(spec, sm.state)
    print(format_plan(plan))

    return 0


def cmd_diff(args):
    """Show what changed between two state files or since last refresh."""
    import json as _json
    from datetime import datetime

    state_a_path = args.old
    state_b_path = args.new or args.state

    # Load state A (old)
    if not state_a_path:
        print("Error: --old is required (path to previous state file)")
        print("Tip: copy terra4mice.state.json before running refresh")
        return 1

    try:
        with open(state_a_path, 'r', encoding='utf-8') as f:
            data_a = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError) as e:
        print(f"Error reading old state: {e}")
        return 1

    # Load state B (new / current)
    try:
        sm = StateManager(state_b_path)
        sm.load()
        data_b_resources = {}
        for r in sm.state.list():
            data_b_resources[r.address] = r
    except Exception as e:
        print(f"Error reading current state: {e}")
        return 1

    # Build state A resource dict
    resources_a = {}
    for r_data in data_a.get("resources", []):
        addr = f"{r_data['type']}.{r_data['name']}"
        resources_a[addr] = r_data.get("status", "missing")

    # Compute diff
    all_addrs = sorted(set(list(resources_a.keys()) + list(data_b_resources.keys())))

    upgraded = []
    downgraded = []
    new_resources = []
    removed = []

    status_rank = {"missing": 0, "broken": 0, "partial": 1, "implemented": 2, "deprecated": 0}

    for addr in all_addrs:
        old_status = resources_a.get(addr)
        new_resource = data_b_resources.get(addr)
        new_status = new_resource.status.value if new_resource else None

        if old_status is None and new_status is not None:
            new_resources.append((addr, new_status))
        elif old_status is not None and new_status is None:
            removed.append((addr, old_status))
        elif old_status != new_status:
            old_rank = status_rank.get(old_status, 0)
            new_rank = status_rank.get(new_status, 0)
            if new_rank > old_rank:
                upgraded.append((addr, old_status, new_status))
            else:
                downgraded.append((addr, old_status, new_status))

    # Compute convergence delta
    def _convergence(resources_dict):
        if not resources_dict:
            return 0.0
        scores = {"implemented": 100, "partial": 50, "missing": 0, "broken": 0, "deprecated": 0}
        total = sum(scores.get(s, 0) for s in resources_dict.values())
        return total / len(resources_dict)

    conv_a = _convergence(resources_a)
    conv_b_dict = {addr: r.status.value for addr, r in data_b_resources.items()}
    conv_b = _convergence(conv_b_dict)
    delta = conv_b - conv_a

    # Output
    colors = {"implemented": "\033[32m", "partial": "\033[33m", "missing": "\033[31m", "broken": "\033[31m"}
    reset = "\033[0m"

    print("terra4mice diff")
    print("=" * 50)
    print(f"  Old: {state_a_path} (serial {data_a.get('serial', '?')})")
    print(f"  New: {state_b_path or 'terra4mice.state.json'} (serial {sm.state.serial})")
    print()

    if upgraded:
        print(f"\033[32mUpgraded ({len(upgraded)}):\033[0m")
        for addr, old, new in upgraded:
            c = colors.get(new, "")
            print(f"  {addr}: {old} -> {c}{new}{reset}")
        print()

    if downgraded:
        print(f"\033[31mDowngraded ({len(downgraded)}):\033[0m")
        for addr, old, new in downgraded:
            c = colors.get(new, "")
            print(f"  {addr}: {old} -> {c}{new}{reset}")
        print()

    if new_resources:
        print(f"\033[32mNew ({len(new_resources)}):\033[0m")
        for addr, status in new_resources:
            c = colors.get(status, "")
            print(f"  + {addr} ({c}{status}{reset})")
        print()

    if removed:
        print(f"\033[31mRemoved ({len(removed)}):\033[0m")
        for addr, status in removed:
            print(f"  - {addr} (was {status})")
        print()

    if not (upgraded or downgraded or new_resources or removed):
        print("  No changes.")
        print()

    # Convergence summary
    delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
    delta_color = "\033[32m" if delta >= 0 else "\033[31m"
    print(f"Convergence: {conv_a:.1f}% -> {conv_b:.1f}% ({delta_color}{delta_str}%{reset})")

    return 0


def main():
    """Entry point for terra4mice CLI."""
    parser = argparse.ArgumentParser(
        prog="terra4mice",
        description="State-Driven Development Framework"
    )
    parser.add_argument(
        "--version", action="version",
        version=f"terra4mice {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize terra4mice")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing files")

    # plan
    plan_parser = subparsers.add_parser("plan", help="Show execution plan")
    plan_parser.add_argument("--spec", default=None, help="Path to spec file")
    plan_parser.add_argument("--state", default=None, help="Path to state file")
    plan_parser.add_argument("--verbose", "-v", action="store_true", help="Show all resources")
    plan_parser.add_argument("--check-deps", action="store_true", help="Check dependencies")
    plan_parser.add_argument("--detailed-exitcode", action="store_true",
                            help="Return 2 if there are changes (for CI)")
    plan_parser.add_argument("--format", choices=["text", "json", "markdown"],
                            default="text", help="Output format (default: text)")
    plan_parser.add_argument("--no-color", action="store_true",
                            help="Strip ANSI escape codes from output")
    plan_parser.add_argument("--ci", action="store_true",
                            help="Shorthand for --format json --no-color --detailed-exitcode")

    # ci
    ci_parser = subparsers.add_parser("ci", help="Run refresh + plan in CI mode")
    ci_parser.add_argument("--spec", default=None, help="Path to spec file")
    ci_parser.add_argument("--state", default=None, help="Path to state file")
    ci_parser.add_argument("--root", default=None, help="Root directory to scan")
    ci_parser.add_argument("--format", choices=["text", "json", "markdown"],
                          default="json", help="Output format (default: json)")
    ci_parser.add_argument("--output", default=None,
                          help="Write result to file (for artifact upload)")
    ci_parser.add_argument("--comment", default=None,
                          help="Write PR comment markdown to file")
    ci_parser.add_argument("--fail-on-incomplete", action="store_true",
                          help="Fail (exit 2) if convergence < 100%%")
    ci_parser.add_argument("--fail-under", type=float, default=None,
                          help="Fail if convergence < N%%")

    # state
    state_parser = subparsers.add_parser("state", help="State management commands")
    state_subparsers = state_parser.add_subparsers(dest="state_command")

    # state list
    state_list = state_subparsers.add_parser("list", help="List resources in state")
    state_list.add_argument("--state", default=None, help="Path to state file")
    state_list.add_argument("--type", default=None, help="Filter by resource type")
    state_list.add_argument("--verbose", "-v", action="store_true", help="Show details")

    # state show
    state_show = state_subparsers.add_parser("show", help="Show resource details")
    state_show.add_argument("address", help="Resource address (type.name)")
    state_show.add_argument("--state", default=None, help="Path to state file")

    # state rm
    state_rm = state_subparsers.add_parser("rm", help="Remove resource from state")
    state_rm.add_argument("address", help="Resource address (type.name)")
    state_rm.add_argument("--state", default=None, help="Path to state file")

    # mark
    mark_parser = subparsers.add_parser("mark", help="Mark resource status")
    mark_parser.add_argument("address", help="Resource address (type.name)")
    mark_parser.add_argument("--status", "-s", choices=["implemented", "partial", "broken"],
                            default="implemented", help="Status to set")
    mark_parser.add_argument("--files", "-f", default="", help="Files that implement (comma-separated)")
    mark_parser.add_argument("--tests", "-t", default="", help="Tests that cover (comma-separated)")
    mark_parser.add_argument("--reason", "-r", default="", help="Reason (for partial/broken)")
    mark_parser.add_argument("--state", default=None, help="Path to state file")

    # apply
    apply_parser = subparsers.add_parser("apply", help="Interactive apply loop")
    apply_parser.add_argument("--spec", default=None, help="Path to spec file")
    apply_parser.add_argument("--state", default=None, help="Path to state file")

    # refresh (auto-inference)
    refresh_parser = subparsers.add_parser("refresh", help="Auto-detect resources from codebase")
    refresh_parser.add_argument("--spec", default=None, help="Path to spec file")
    refresh_parser.add_argument("--state", default=None, help="Path to state file")
    refresh_parser.add_argument("--root", default=None, help="Root directory to scan")
    refresh_parser.add_argument("--source-dirs", default=None,
                               help="Source directories to scan (comma-separated)")
    refresh_parser.add_argument("--dry-run", action="store_true",
                               help="Show what would be detected without updating state")
    refresh_parser.add_argument("--force", action="store_true",
                               help="Update all resources, not just missing ones")
    refresh_parser.add_argument("--show-plan", action="store_true",
                               help="Show plan after refresh")

    # diff
    diff_parser = subparsers.add_parser("diff", help="Show changes between two state files")
    diff_parser.add_argument("--old", required=True,
                            help="Path to old state file (e.g., state.json.bak)")
    diff_parser.add_argument("--new", default=None,
                            help="Path to new state file (defaults to current)")
    diff_parser.add_argument("--state", default=None, help="Path to state file")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "plan":
        return cmd_plan(args)
    elif args.command == "ci":
        return cmd_ci(args)
    elif args.command == "state":
        if args.state_command == "list":
            return cmd_state_list(args)
        elif args.state_command == "show":
            return cmd_state_show(args)
        elif args.state_command == "rm":
            return cmd_state_rm(args)
        else:
            state_parser.print_help()
            return 0
    elif args.command == "mark":
        return cmd_mark(args)
    elif args.command == "apply":
        return cmd_apply(args)
    elif args.command == "refresh":
        return cmd_refresh(args)
    elif args.command == "diff":
        return cmd_diff(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
