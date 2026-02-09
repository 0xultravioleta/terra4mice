#!/usr/bin/env python3
"""
terra4mice CLI - State-Driven Development Framework

Usage:
    terra4mice init                Create spec and state files
    terra4mice init --migrate-state  Migrate local state to remote backend
    terra4mice plan                Show what needs to be done
    terra4mice refresh             Auto-detect resources from codebase
    terra4mice state list          List all resources in state
    terra4mice state show          Show details of a resource
    terra4mice state pull          Download remote state to local file
    terra4mice state push          Upload local state to remote backend
    terra4mice mark                Mark a resource as created/partial/broken
    terra4mice mark --agent X      Mark and track context for agent X
    terra4mice mark --lock         Mark and lock (survives refresh)
    terra4mice lock                Lock a resource (prevent refresh overwrite)
    terra4mice unlock              Unlock a resource (allow refresh)
    terra4mice apply               Interactive apply loop
    terra4mice ci                  Run refresh + plan in CI mode
    terra4mice force-unlock ID     Force-release a stuck state lock
    
    # Multi-agent context tracking
    terra4mice contexts list                    List all agents and contexts
    terra4mice contexts show <agent>            Show agent's context details
    terra4mice contexts sync --from A --to B    Sync contexts between agents
    terra4mice contexts export -a <agent> -o F  Export agent context to file
    terra4mice contexts import -i <file>        Import context from file
"""

import sys
import argparse
from pathlib import Path

from . import __version__
from .spec_parser import load_spec, load_spec_with_backend, validate_spec, create_example_spec, DEFAULT_SPEC_FILE
from .state_manager import StateManager, DEFAULT_STATE_FILE
from .backends import create_backend, StateLockError, LocalBackend
from .planner import generate_plan, format_plan, check_dependencies
from .models import ResourceStatus
from .inference import InferenceEngine, InferenceConfig, format_inference_report
from .ci import format_plan_json, format_plan_markdown, strip_ansi
from .contexts import ContextRegistry, infer_agent_from_env
from .context_io import (
    export_agent_context, import_handoff, sync_contexts,
    ContextHandoff, MergeStrategy
)

DEFAULT_CONTEXTS_FILE = "terra4mice.contexts.json"


def _load_context_registry(args) -> ContextRegistry:
    """Load context registry from file or create new one."""
    contexts_path = getattr(args, "contexts", None) or DEFAULT_CONTEXTS_FILE
    path = Path.cwd() / contexts_path
    
    if path.exists():
        try:
            return ContextRegistry.from_json(path.read_text(encoding="utf-8"))
        except Exception:
            return ContextRegistry()
    return ContextRegistry()


def _save_context_registry(registry: ContextRegistry, args) -> None:
    """Save context registry to file."""
    contexts_path = getattr(args, "contexts", None) or DEFAULT_CONTEXTS_FILE
    path = Path.cwd() / contexts_path
    path.write_text(registry.to_json(), encoding="utf-8")


def _create_state_manager(args) -> StateManager:
    """Create StateManager with the correct backend.

    Priority: --state flag > spec backend config > default local.
    """
    state_path = getattr(args, "state", None)
    if state_path is not None:
        return StateManager(path=state_path)

    spec_path = getattr(args, "spec", None)
    try:
        _, backend_config = load_spec_with_backend(spec_path)
        if backend_config:
            backend = create_backend(backend_config)
            return StateManager(backend=backend)
    except (FileNotFoundError, Exception):
        pass

    return StateManager()


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
    sm = _create_state_manager(args)
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
    sm = _create_state_manager(args)
    sm.load()

    # Run refresh if root dir is available
    root_dir = Path(args.root) if args.root else Path.cwd()
    if root_dir.exists():
        config = InferenceConfig()
        config.root_dir = root_dir
        config.parallelism = getattr(args, 'parallelism', 0)
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
    sm = _create_state_manager(args)
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

        lock_icon = " \033[36m[locked]\033[0m" if resource.locked else ""
        print(f"{color}{resource.address}{reset}{lock_icon}")
        if args.verbose:
            print(f"    status: {resource.status.value}")
            if resource.locked:
                print(f"    locked: true (source: {resource.source})")
            if resource.files:
                print(f"    files: {', '.join(resource.files)}")
            if resource.symbols:
                impl = sum(1 for s in resource.symbols.values() if s.status == "implemented")
                print(f"    symbols: {impl}/{len(resource.symbols)}")

    return 0


def cmd_state_show(args):
    """Show details of a specific resource."""
    sm = _create_state_manager(args)
    sm.load()

    resource = sm.show(args.address)

    if resource is None:
        print(f"Resource not found: {args.address}")
        return 1

    print(f"# {resource.address}")
    print(f"type     = \"{resource.type}\"")
    print(f"name     = \"{resource.name}\"")
    print(f"status   = \"{resource.status.value}\"")
    if resource.locked:
        print(f"locked   = true")
        print(f"source   = \"{resource.source}\"")

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
            file_info = f" ({sym.file})" if sym.file else ""
            print(f"  {qname:<35} {sym.kind:<10}{file_info}{status_indicator}")

    if resource.created_at:
        print(f"created_at = \"{resource.created_at.isoformat()}\"")
    if resource.updated_at:
        print(f"updated_at = \"{resource.updated_at.isoformat()}\"")

    return 0


def cmd_state_rm(args):
    """Remove a resource from state."""
    sm = _create_state_manager(args)
    try:
        with sm:
            resource = sm.remove(args.address)

            if resource is None:
                print(f"Resource not found: {args.address}")
                return 1

            sm.save()
            print(f"Removed: {args.address}")
            return 0
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


def cmd_mark(args):
    """Mark a resource status."""
    sm = _create_state_manager(args)
    try:
        with sm:
            address = args.address
            status = args.status or "implemented"
            lock = getattr(args, 'lock', False)

            files = args.files.split(",") if args.files else []
            tests = args.tests.split(",") if args.tests else []

            if status == "implemented":
                resource = sm.mark_created(address, files=files, tests=tests, lock=lock)
            elif status == "partial":
                resource = sm.mark_partial(address, reason=args.reason or "", lock=lock)
            elif status == "broken":
                resource = sm.mark_broken(address, reason=args.reason or "", lock=lock)
            else:
                print(f"Unknown status: {status}")
                return 1

            sm.save()

            # Track context if --agent is provided
            agent = getattr(args, 'agent', None) or infer_agent_from_env()
            if agent:
                registry = _load_context_registry(args)
                knowledge = []
                if args.reason:
                    knowledge.append(args.reason)
                registry.register_context(
                    agent=agent,
                    resource=address,
                    files_touched=files,
                    knowledge=knowledge,
                    contributed_status=status,
                )
                _save_context_registry(registry, args)
                print(f"Context tracked for agent: {agent}")

            lock_indicator = " (locked)" if resource.locked else ""
            print(f"Marked {resource.address} as {resource.status.value}{lock_indicator}")
            return 0
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


def cmd_lock(args):
    """Lock a resource to prevent refresh from overwriting it."""
    sm = _create_state_manager(args)
    try:
        with sm:
            address = args.address
            resource = sm.mark_locked(address, locked=True)

            if resource is None:
                print(f"Resource not found: {address}")
                return 1

            sm.save()
            print(f"Locked: {resource.address} ({resource.status.value})")
            return 0
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


def cmd_unlock(args):
    """Unlock a resource so refresh can update it."""
    sm = _create_state_manager(args)
    try:
        with sm:
            address = args.address
            resource = sm.mark_locked(address, locked=False)

            if resource is None:
                print(f"Resource not found: {address}")
                return 1

            sm.save()
            print(f"Unlocked: {resource.address} ({resource.status.value})")
            return 0
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


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
    sm = _create_state_manager(args)

    try:
        with sm:
            # Configure inference
            config = InferenceConfig()
            config.root_dir = Path(args.root) if args.root else Path.cwd()
            config.parallelism = getattr(args, 'parallelism', 0)

            if args.source_dirs:
                config.source_dirs = args.source_dirs.split(",")

            # Run inference
            import sys as _sys

            def _progress(current, total, resource):
                _sys.stderr.write(f"\rScanning... [{current}/{total}] {resource.address:<40}")
                _sys.stderr.flush()

            engine = InferenceEngine(config)
            workers = engine._effective_parallelism()
            par_info = f" (parallelism={workers})" if workers > 1 else ""
            print(f"Scanning {config.root_dir} for resources...{par_info}", file=_sys.stderr)

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
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


def _use_enhanced_apply(args) -> bool:
    """Determine if we should use the enhanced apply runner."""
    if getattr(args, "enhanced", False):
        return True
    # Any of the new flags activates enhanced mode
    for flag in ("mode", "agent", "resource", "dry_run",
                 "require_tests", "auto_commit"):
        val = getattr(args, flag, None)
        if val not in (None, False):
            return True
    if getattr(args, "parallel", 1) > 1:
        return True
    if getattr(args, "timeout", 0) > 0:
        return True
    return False


def cmd_apply(args):
    """Interactive apply loop (classic or enhanced)."""
    # ── Enhanced mode ──
    if _use_enhanced_apply(args):
        return _cmd_apply_enhanced(args)

    # ── Classic mode (backward-compatible) ──
    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1

    sm = _create_state_manager(args)
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


def _cmd_apply_enhanced(args):
    """Enhanced apply using ApplyRunner with DAG ordering and context."""
    from .apply import ApplyRunner, ApplyConfig

    try:
        spec = load_spec(args.spec)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1

    sm = _create_state_manager(args)
    sm.load()

    # Load context registry if available
    context_registry = None
    try:
        context_registry = _load_context_registry(args)
    except Exception:
        pass

    config = ApplyConfig(
        mode=getattr(args, "mode", None) or "interactive",
        agent=getattr(args, "agent", None),
        parallel=getattr(args, "parallel", 1),
        max_workers=getattr(args, "max_workers", 1),
        timeout_minutes=getattr(args, "timeout", 0),
        require_tests=getattr(args, "require_tests", False),
        auto_commit=getattr(args, "auto_commit", False),
        dry_run=getattr(args, "dry_run", False),
        verify_level=getattr(args, "verify_level", "basic"),
        market_url=getattr(args, "market_url", None),
        market_api_key=getattr(args, "market_api_key", None),
        bounty=getattr(args, "bounty", None),
    )

    errors = config.validate()
    if errors:
        for err in errors:
            print(f"Error: {err}")
        return 1

    project_root = getattr(args, "project_root", None)
    runner = ApplyRunner(
        spec=spec,
        state_manager=sm,
        context_registry=context_registry,
        config=config,
        project_root=project_root,
    )

    resource_filter = getattr(args, "resource", None)

    try:
        result = runner.run(resource=resource_filter)
    except Exception as e:
        print(f"Error during apply: {e}")
        return 1

    # Save context registry if we used one
    if context_registry is not None:
        try:
            _save_context_registry(context_registry, args)
        except Exception:
            pass

    # Show final plan
    plan = generate_plan(spec, sm.state)
    print(format_plan(plan))
    print(result.summary())

    return 0 if not result.failed else 1


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
        sm = StateManager(path=state_b_path)
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


def cmd_state_pull(args):
    """Download remote state to a local file."""
    sm = _create_state_manager(args)
    sm.load()

    output = getattr(args, "output", None) or "terra4mice.state.json"
    data = sm._serialize_state(sm.state)
    import json as _json
    Path(output).write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"State pulled to: {output}")
    print(f"  Backend: {sm.backend.backend_type}")
    print(f"  Resources: {len(sm.state.list())}")
    print(f"  Serial: {sm.state.serial}")
    return 0


def cmd_state_push(args):
    """Upload a local state file to the remote backend."""
    sm = _create_state_manager(args)

    input_file = getattr(args, "input", None) or "terra4mice.state.json"
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: file not found: {input_file}")
        return 1

    import json as _json
    data = _json.loads(input_path.read_text(encoding="utf-8"))
    sm.state = sm._parse_state(data)

    try:
        with sm:
            sm.save()
            print(f"State pushed from: {input_file}")
            print(f"  Backend: {sm.backend.backend_type}")
            print(f"  Resources: {len(sm.state.list())}")
            print(f"  Serial: {sm.state.serial}")
            return 0
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


def cmd_contexts_list(args):
    """List all agents and their contexts."""
    registry = _load_context_registry(args)
    
    agents = registry.list_agents()
    contexts = registry.list_all()
    
    if not contexts and not agents:
        print("No contexts tracked yet.")
        print("Use 'terra4mice mark --agent=<agent> <address>' to track context.")
        return 0
    
    # Group contexts by agent
    by_agent: dict = {}
    for entry in contexts:
        if entry.agent not in by_agent:
            by_agent[entry.agent] = []
        by_agent[entry.agent].append(entry)
    
    # Also include agents with no contexts
    for agent in agents:
        if agent.id not in by_agent:
            by_agent[agent.id] = []
    
    verbose = getattr(args, 'verbose', False)
    
    for agent_id in sorted(by_agent.keys()):
        entries = by_agent[agent_id]
        profile = registry.get_agent(agent_id)
        
        # Header
        model_str = f" ({profile.model})" if profile and profile.model else ""
        print(f"\033[1m{agent_id}\033[0m{model_str}")
        
        if not entries:
            print("  (no contexts)")
        else:
            for entry in sorted(entries, key=lambda e: e.timestamp, reverse=True):
                status = entry.status()
                status_color = {
                    "active": "\033[32m",   # Green
                    "stale": "\033[33m",    # Yellow
                    "expired": "\033[90m",  # Gray
                }
                color = status_color.get(status.value, "")
                reset = "\033[0m"
                
                age = entry.age_str()
                conf = f" conf={entry.confidence:.1f}" if verbose else ""
                print(f"  {color}{entry.resource}{reset} [{status.value}] {age}{conf}")
                
                if verbose and entry.files_touched:
                    print(f"    files: {', '.join(entry.files_touched)}")
                if verbose and entry.knowledge:
                    for k in entry.knowledge[:2]:  # Limit to 2 knowledge items
                        print(f"    - {k}")
        print()
    
    # Summary
    summary = registry.coverage_summary()
    print(f"Summary: {summary['active']} active, {summary['stale']} stale, {summary['expired']} expired")
    
    return 0


def cmd_contexts_show(args):
    """Show detailed view of an agent's context."""
    registry = _load_context_registry(args)
    agent_id = args.agent
    
    profile = registry.get_agent(agent_id)
    entries = registry.get_agent_contexts(agent_id)
    
    if not profile and not entries:
        print(f"Agent not found: {agent_id}")
        return 1
    
    # Agent header
    print(f"# Agent: {agent_id}")
    if profile:
        if profile.name and profile.name != agent_id:
            print(f"name       = \"{profile.name}\"")
        if profile.model:
            print(f"model      = \"{profile.model}\"")
        if profile.platform:
            print(f"platform   = \"{profile.platform}\"")
        if profile.capabilities:
            print(f"capabilities = {profile.capabilities}")
        if profile.last_seen:
            print(f"last_seen  = \"{profile.last_seen.isoformat()}\"")
        if profile.current_session:
            print(f"session    = \"{profile.current_session}\"")
    
    print()
    print(f"## Resources ({len(entries)})")
    print()
    
    for entry in sorted(entries, key=lambda e: e.timestamp, reverse=True):
        status = entry.status()
        status_color = {
            "active": "\033[32m",   # Green
            "stale": "\033[33m",    # Yellow
            "expired": "\033[90m",  # Gray
        }
        color = status_color.get(status.value, "")
        reset = "\033[0m"
        
        print(f"### {entry.resource}")
        print(f"  status     = {color}{status.value}{reset}")
        print(f"  confidence = {entry.confidence}")
        print(f"  timestamp  = {entry.timestamp.isoformat()}")
        print(f"  age        = {entry.age_str()}")
        
        if entry.files_touched:
            print(f"  files      = {entry.files_touched}")
        if entry.knowledge:
            print(f"  knowledge:")
            for k in entry.knowledge:
                print(f"    - {k}")
        if entry.contributed_status:
            print(f"  contributed_status = \"{entry.contributed_status}\"")
        print()
    
    return 0


def cmd_contexts_sync(args):
    """Sync contexts from one agent to another."""
    registry = _load_context_registry(args)
    sm = _create_state_manager(args)
    sm.load()
    
    from_agent = args.from_agent
    to_agent = args.to_agent
    
    # Validate agents exist
    from_contexts = registry.get_agent_contexts(from_agent)
    if not from_contexts:
        print(f"Error: No contexts found for agent '{from_agent}'")
        return 1
    
    # Optional resource filter
    resources = None
    if args.resources:
        resources = [r.strip() for r in args.resources.split(",")]
    
    confidence_decay = getattr(args, 'decay', 0.1)
    
    result = sync_contexts(
        registry=registry,
        state=sm.state,
        from_agent=from_agent,
        to_agent=to_agent,
        resources=resources,
        confidence_decay=confidence_decay,
    )
    
    _save_context_registry(registry, args)
    
    print(f"Synced contexts from '{from_agent}' to '{to_agent}'")
    print(f"  Imported: {result.imported_count}")
    print(f"  Skipped:  {result.skipped_count}")
    
    if result.conflicts:
        print()
        print("\033[33mWarning: Potential conflicts detected:\033[0m")
        for c in result.conflicts:
            print(f"  - {c['resource']}: {c['warning']}")
    
    if getattr(args, 'verbose', False) and result.messages:
        print()
        print("Details:")
        for msg in result.messages:
            print(f"  {msg}")
    
    return 0


def cmd_contexts_export(args):
    """Export agent context to file."""
    registry = _load_context_registry(args)
    sm = _create_state_manager(args)
    sm.load()
    
    agent = args.agent
    
    # Validate agent has contexts
    entries = registry.get_agent_contexts(agent)
    if not entries:
        print(f"Error: No contexts found for agent '{agent}'")
        return 1
    
    # Build handoff
    notes = getattr(args, 'notes', '') or ''
    recommendations = []
    if args.recommend:
        recommendations = [r.strip() for r in args.recommend.split(",")]
    
    include_state = getattr(args, 'include_state', False)
    
    handoff = export_agent_context(
        registry=registry,
        state=sm.state,
        agent=agent,
        project=getattr(args, 'project', '') or '',
        include_state=include_state,
        notes=notes,
        recommendations=recommendations,
        to_agent=getattr(args, 'to', None),
    )
    
    # Output
    output_path = Path(args.output)
    handoff.save(output_path)
    
    print(f"Exported context for '{agent}' to {output_path}")
    print(f"  Resources: {len(handoff.resources)}")
    if handoff.notes:
        print(f"  Notes: {handoff.notes[:50]}...")
    
    return 0


def cmd_contexts_import(args):
    """Import context from handoff file."""
    registry = _load_context_registry(args)
    
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1
    
    try:
        handoff = ContextHandoff.load(input_path)
    except Exception as e:
        print(f"Error loading handoff: {e}")
        return 1
    
    # Determine importing agent
    agent = args.agent or infer_agent_from_env()
    if not agent:
        print("Error: Could not determine importing agent.")
        print("Use --agent=<agent-id> or set AI_AGENT_ID environment variable.")
        return 1
    
    # Determine merge strategy
    strategy_map = {
        "merge": MergeStrategy.MERGE,
        "replace": MergeStrategy.REPLACE,
        "skip": MergeStrategy.SKIP_EXISTING,
    }
    strategy_name = getattr(args, 'strategy', 'merge') or 'merge'
    strategy = strategy_map.get(strategy_name, MergeStrategy.MERGE)
    
    confidence_decay = getattr(args, 'decay', 0.1)
    
    result = import_handoff(
        registry=registry,
        handoff=handoff,
        importing_agent=agent,
        merge_strategy=strategy,
        confidence_decay=confidence_decay,
    )
    
    _save_context_registry(registry, args)
    
    print(f"Imported handoff from '{handoff.from_agent}' as '{agent}'")
    print(f"  Imported: {result.imported_count}")
    print(f"  Skipped:  {result.skipped_count}")
    
    if handoff.notes:
        print()
        print(f"Notes from {handoff.from_agent}:")
        print(f"  {handoff.notes}")
    
    if handoff.recommendations:
        print()
        print("Recommendations:")
        for rec in handoff.recommendations:
            print(f"  - {rec}")
    
    if handoff.warnings:
        print()
        print("\033[33mWarnings:\033[0m")
        for warn in handoff.warnings:
            print(f"  - {warn}")
    
    if result.conflicts:
        print()
        print("\033[33mPotential conflicts:\033[0m")
        for c in result.conflicts:
            print(f"  - {c['resource']}: {c['warning']}")
    
    if getattr(args, 'verbose', False) and result.messages:
        print()
        print("Details:")
        for msg in result.messages:
            print(f"  {msg}")
    
    return 0


def cmd_force_unlock(args):
    """Force-release a stuck state lock."""
    sm = _create_state_manager(args)

    if not sm.backend.supports_locking:
        print("Backend does not support locking.")
        return 1

    lock_id = args.lock_id
    sm.backend.force_unlock(lock_id)
    print(f"Lock forcefully released: {lock_id}")
    print("WARNING: Releasing a lock held by another process may cause state corruption.")
    return 0


def cmd_migrate_state(args):
    """Migrate state from local to remote backend (or vice versa)."""
    # Load from local
    local_path = Path(args.state) if args.state else Path.cwd() / DEFAULT_STATE_FILE
    if not local_path.exists():
        print(f"Error: local state file not found: {local_path}")
        return 1

    local_sm = StateManager(path=local_path)
    local_sm.load()

    # Load spec to get backend config
    spec_path = getattr(args, "spec", None)
    try:
        _, backend_config = load_spec_with_backend(spec_path)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1

    if not backend_config:
        print("Error: no backend configured in spec. Add a backend: section first.")
        return 1

    backend = create_backend(backend_config)
    remote_sm = StateManager(backend=backend)

    try:
        with remote_sm:
            remote_sm.state = local_sm.state
            remote_sm.save()
            print(f"State migrated to {backend.backend_type} backend.")
            print(f"  Resources: {len(local_sm.state.list())}")
            print(f"  Serial: {local_sm.state.serial}")
            print()
            print("You can now remove the local state file if desired:")
            print(f"  del {local_path}" if sys.platform == "win32" else f"  rm {local_path}")
            return 0
    except StateLockError as e:
        print(f"Error: {e}")
        return 1


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
    init_parser.add_argument("--migrate-state", action="store_true",
                            help="Migrate local state to remote backend configured in spec")
    init_parser.add_argument("--spec", default=None, help="Path to spec file")
    init_parser.add_argument("--state", default=None, help="Path to state file")

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
    ci_parser.add_argument("--parallelism", type=int, default=0,
                          help="Number of parallel workers (0=auto, 1=sequential)")

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

    # state pull
    state_pull = state_subparsers.add_parser("pull", help="Download remote state to local file")
    state_pull.add_argument("--spec", default=None, help="Path to spec file")
    state_pull.add_argument("--state", default=None, help="Path to state file")
    state_pull.add_argument("-o", "--output", default=None,
                            help="Output file (default: terra4mice.state.json)")

    # state push
    state_push = state_subparsers.add_parser("push", help="Upload local state to remote backend")
    state_push.add_argument("--spec", default=None, help="Path to spec file")
    state_push.add_argument("--state", default=None, help="Path to state file")
    state_push.add_argument("-i", "--input", default=None,
                            help="Input file (default: terra4mice.state.json)")

    # mark
    mark_parser = subparsers.add_parser("mark", help="Mark resource status")
    mark_parser.add_argument("address", help="Resource address (type.name)")
    mark_parser.add_argument("--status", "-s", choices=["implemented", "partial", "broken"],
                            default="implemented", help="Status to set")
    mark_parser.add_argument("--files", "-f", default="", help="Files that implement (comma-separated)")
    mark_parser.add_argument("--tests", "-t", default="", help="Tests that cover (comma-separated)")
    mark_parser.add_argument("--reason", "-r", default="", help="Reason (for partial/broken)")
    mark_parser.add_argument("--lock", "-l", action="store_true",
                            help="Lock resource to prevent refresh from overwriting")
    mark_parser.add_argument("--state", default=None, help="Path to state file")
    mark_parser.add_argument("--agent", "-a", default=None,
                            help="Agent ID for context tracking (auto-detected if not set)")
    mark_parser.add_argument("--contexts", default=None, help="Path to contexts file")

    # lock
    lock_parser = subparsers.add_parser("lock", help="Lock resource (prevent refresh overwrite)")
    lock_parser.add_argument("address", help="Resource address (type.name)")
    lock_parser.add_argument("--state", default=None, help="Path to state file")

    # unlock
    unlock_parser = subparsers.add_parser("unlock", help="Unlock resource (allow refresh)")
    unlock_parser.add_argument("address", help="Resource address (type.name)")
    unlock_parser.add_argument("--state", default=None, help="Path to state file")

    # apply
    apply_parser = subparsers.add_parser("apply", help="Interactive apply loop")
    apply_parser.add_argument("--spec", default=None, help="Path to spec file")
    apply_parser.add_argument("--state", default=None, help="Path to state file")
    apply_parser.add_argument("--enhanced", action="store_true",
                              help="Use enhanced apply mode (DAG ordering, context-aware)")
    apply_parser.add_argument("--mode", default=None,
                              choices=["interactive", "auto", "hybrid", "market"],
                              help="Apply mode (default: interactive)")
    apply_parser.add_argument("--agent", default=None,
                              help="Agent ID for context tracking")
    apply_parser.add_argument("--parallel", type=int, default=1,
                              help="Max parallel implementations")
    apply_parser.add_argument("--timeout", type=int, default=0,
                              help="Timeout in minutes (0=no timeout)")
    apply_parser.add_argument("--require-tests", action="store_true",
                              help="Require tests before marking implemented")
    apply_parser.add_argument("--auto-commit", action="store_true",
                              help="Git commit after each resource")
    apply_parser.add_argument("--dry-run", action="store_true",
                              help="Show plan without executing")
    apply_parser.add_argument("--resource", default=None,
                              help="Apply only a specific resource address")
    apply_parser.add_argument("--contexts", default=None,
                              help="Path to contexts file")
    apply_parser.add_argument("--market-url", default=None,
                              help="Execution Market API URL (default: https://api.execution.market)")
    apply_parser.add_argument("--market-api-key", default=None,
                              help="Execution Market API key (or EXECUTION_MARKET_API_KEY env)")
    apply_parser.add_argument("--verify-level", default="basic",
                              choices=["basic", "git_diff", "full"],
                              help="Verification level for implementations")
    apply_parser.add_argument("--max-workers", type=int, default=1,
                              help="Max parallel workers for execution")
    apply_parser.add_argument("--project-root", default=None,
                              help="Project root directory")
    apply_parser.add_argument("--bounty", type=float, default=None,
                              help="Default bounty for market tasks (USD)")

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
    refresh_parser.add_argument("--parallelism", type=int, default=0,
                               help="Number of parallel workers (0=auto, 1=sequential)")

    # contexts
    contexts_parser = subparsers.add_parser("contexts", help="Multi-agent context tracking")
    contexts_subparsers = contexts_parser.add_subparsers(dest="contexts_command")

    # contexts list
    contexts_list = contexts_subparsers.add_parser("list", help="List all agents and their contexts")
    contexts_list.add_argument("--contexts", default=None, help="Path to contexts file")
    contexts_list.add_argument("--verbose", "-v", action="store_true", help="Show details")

    # contexts show
    contexts_show = contexts_subparsers.add_parser("show", help="Show detailed view of agent context")
    contexts_show.add_argument("agent", help="Agent ID to show")
    contexts_show.add_argument("--contexts", default=None, help="Path to contexts file")

    # contexts sync
    contexts_sync = contexts_subparsers.add_parser("sync", help="Sync contexts between agents")
    contexts_sync.add_argument("--from", dest="from_agent", required=True,
                              help="Source agent ID")
    contexts_sync.add_argument("--to", dest="to_agent", required=True,
                              help="Target agent ID")
    contexts_sync.add_argument("--resources", default=None,
                              help="Specific resources to sync (comma-separated)")
    contexts_sync.add_argument("--decay", type=float, default=0.1,
                              help="Confidence decay on sync (default: 0.1)")
    contexts_sync.add_argument("--contexts", default=None, help="Path to contexts file")
    contexts_sync.add_argument("--spec", default=None, help="Path to spec file")
    contexts_sync.add_argument("--state", default=None, help="Path to state file")
    contexts_sync.add_argument("--verbose", "-v", action="store_true", help="Show details")

    # contexts export
    contexts_export = contexts_subparsers.add_parser("export", help="Export agent context to file")
    contexts_export.add_argument("--agent", "-a", required=True, help="Agent ID to export")
    contexts_export.add_argument("-o", "--output", required=True, help="Output file path")
    contexts_export.add_argument("--project", default=None, help="Project name")
    contexts_export.add_argument("--notes", default=None, help="Handoff notes")
    contexts_export.add_argument("--recommend", default=None,
                                help="Recommendations (comma-separated)")
    contexts_export.add_argument("--to", default=None, help="Target agent (optional)")
    contexts_export.add_argument("--include-state", action="store_true",
                                help="Include state snapshot in export")
    contexts_export.add_argument("--contexts", default=None, help="Path to contexts file")
    contexts_export.add_argument("--spec", default=None, help="Path to spec file")
    contexts_export.add_argument("--state", default=None, help="Path to state file")

    # contexts import
    contexts_import = contexts_subparsers.add_parser("import", help="Import context from file")
    contexts_import.add_argument("-i", "--input", required=True, help="Input file path")
    contexts_import.add_argument("--agent", "-a", default=None,
                                help="Importing agent ID (auto-detected if not set)")
    contexts_import.add_argument("--strategy", choices=["merge", "replace", "skip"],
                                default="merge", help="Merge strategy (default: merge)")
    contexts_import.add_argument("--decay", type=float, default=0.1,
                                help="Confidence decay on import (default: 0.1)")
    contexts_import.add_argument("--contexts", default=None, help="Path to contexts file")
    contexts_import.add_argument("--verbose", "-v", action="store_true", help="Show details")

    # diff
    diff_parser = subparsers.add_parser("diff", help="Show changes between two state files")
    diff_parser.add_argument("--old", required=True,
                            help="Path to old state file (e.g., state.json.bak)")
    diff_parser.add_argument("--new", default=None,
                            help="Path to new state file (defaults to current)")
    diff_parser.add_argument("--state", default=None, help="Path to state file")

    # force-unlock
    force_unlock_parser = subparsers.add_parser(
        "force-unlock", help="Force-release a stuck state lock"
    )
    force_unlock_parser.add_argument("lock_id", help="Lock ID to force-release")
    force_unlock_parser.add_argument("--spec", default=None, help="Path to spec file")
    force_unlock_parser.add_argument("--state", default=None, help="Path to state file")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "init":
        if getattr(args, "migrate_state", False):
            return cmd_migrate_state(args)
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
        elif args.state_command == "pull":
            return cmd_state_pull(args)
        elif args.state_command == "push":
            return cmd_state_push(args)
        else:
            state_parser.print_help()
            return 0
    elif args.command == "mark":
        return cmd_mark(args)
    elif args.command == "lock":
        return cmd_lock(args)
    elif args.command == "unlock":
        return cmd_unlock(args)
    elif args.command == "apply":
        return cmd_apply(args)
    elif args.command == "refresh":
        return cmd_refresh(args)
    elif args.command == "contexts":
        if args.contexts_command == "list":
            return cmd_contexts_list(args)
        elif args.contexts_command == "show":
            return cmd_contexts_show(args)
        elif args.contexts_command == "sync":
            return cmd_contexts_sync(args)
        elif args.contexts_command == "export":
            return cmd_contexts_export(args)
        elif args.contexts_command == "import":
            return cmd_contexts_import(args)
        else:
            print("Usage: terra4mice contexts <command>")
            print()
            print("Commands:")
            print("  list    List all agents and their contexts")
            print("  show    Show detailed view of agent context")
            print("  sync    Sync contexts between agents")
            print("  export  Export agent context to file")
            print("  import  Import context from file")
            return 0
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "force-unlock":
        return cmd_force_unlock(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
