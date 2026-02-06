# terra4mice

> State-Driven Development Framework
>
> "Software isn't done when it works. It's done when state converges with spec."

terra4mice applies Terraform's mental model to software development. While Terraform manages infrastructure, terra4mice manages **living development**.

## The Problem

In livecoding, this happens:

1. You implement A
2. B breaks A
3. You workaround with C
4. D becomes a TODO
5. Someone says "it works"
6. Weeks later: D never existed

**The system doesn't know**:
- Which parts of the spec are complete
- Which parts are mocked
- Which parts only exist in your head

## The Solution

```
SPEC (desired state)  ->  What SHOULD exist (declarative YAML)
STATE (current state) ->  What DOES exist (inferred/marked)
PLAN (diff)           ->  spec - state = work to do
APPLY (execution)     ->  Cycles until convergence
```

## Quick Start

```bash
# Install
pip install terra4mice

# With deep AST analysis (optional, Python >=3.10)
pip install terra4mice[ast]

# Initialize in your project
cd my-project
terra4mice init

# See what's missing
terra4mice plan

# Auto-detect codebase state
terra4mice refresh

# List resources in state
terra4mice state list

# Mark something as implemented
terra4mice mark feature.auth_login --files src/auth.py

# CI report (JSON)
terra4mice ci --format json
```

## Commands

### `terra4mice init`

Creates spec and state files:

```bash
terra4mice init
# Created: terra4mice.spec.yaml
# Created: terra4mice.state.json
```

### `terra4mice plan`

Shows what's needed to converge:

```
$ terra4mice plan

terra4mice will perform the following actions:

  + feature.auth_login
      # Resource declared in spec but not in state
  + feature.auth_refresh
      # Resource declared in spec but not in state
  ~ feature.auth_logout
      # Resource is partially implemented

Plan: 2 to create, 1 to update.
```

With `--verbose`, plan shows function-level symbol tracking:

```
$ terra4mice plan --verbose

  ~ module.inference
      # Resource is partially implemented
      Symbols: 10/12 found
        - format_report (missing)
        - validate_config (missing)
```

### `terra4mice refresh`

Auto-detects codebase state using multiple strategies:

```
$ terra4mice refresh

Scanning /my-project for resources...

Inference Report
============================================================
IMPLEMENTED (5 resources)
  module.models
    Confidence: [##########] 100%
    Files: src/models.py
    Evidence: Explicit files found, AST analysis: 100% match
    Symbols: 12/12 (100%)

PARTIAL (1 resources)
  feature.auth
    Confidence: [######----] 60%
    Symbols: 5/8 (62%)
    Missing: validate_token, refresh_session, logout_handler

MISSING (2 resources)
  feature.payments
  feature.notifications

Summary
  Convergence: 68.8%
```

Inference strategies (in priority order):
1. **tree-sitter AST** (with `[ast]`) - verifies functions, classes, exports against spec attributes
2. **stdlib ast** - basic Python analysis
3. **Regex** - Solidity, TypeScript/JavaScript patterns
4. **Heuristic** - config/docs file size

### `terra4mice state list`

Lists all resources in state:

```
$ terra4mice state list

feature.auth_login
feature.auth_refresh
module.payment_processor
```

### `terra4mice state show <address>`

Shows resource details including symbol-level tracking:

```
$ terra4mice state show module.inference

# module.inference
type     = "module"
name     = "inference"
status   = "implemented"
files    = ["src/terra4mice/inference.py"]
symbols  = 12 (10 implemented, 2 missing)
  InferenceEngine                     class      lines 94-686 (src/terra4mice/inference.py)
  InferenceEngine.infer_all           method     lines 154-178 (src/terra4mice/inference.py)
  InferenceEngine.infer_resource      method     lines 180-245 (src/terra4mice/inference.py)
  format_inference_report             function   lines 719-787 (src/terra4mice/inference.py)
  validate_config                     function   [MISSING]
```

### `terra4mice mark <address>`

Marks a resource with a status:

```bash
# Mark as implemented
terra4mice mark feature.auth_login --files src/auth.py

# Mark as partial
terra4mice mark feature.auth_refresh --status partial --reason "Missing token rotation"

# Mark as broken
terra4mice mark feature.auth_logout --status broken --reason "Tests failing"
```

### `terra4mice apply`

Interactive apply loop:

```
$ terra4mice apply

============================================================
Next: + feature.auth_login
      Resource declared in spec but not in state

Attributes: {'endpoints': ['POST /auth/login']}

Action: [i]mplement, [p]artial, [s]kip, [q]uit? i
Files that implement this: src/auth.py, src/routes/login.py
Marked as implemented: feature.auth_login
```

### `terra4mice diff`

Compare two state snapshots to see what changed:

```
$ terra4mice diff --old state.json.bak

terra4mice diff
==================================================
  Old: state.json.bak (serial 5)
  New: terra4mice.state.json (serial 8)

Upgraded (3):
  module.inference: partial -> implemented
  module.analyzers: missing -> implemented
  feature.ci: partial -> implemented

Convergence: 45.0% -> 78.3% (+33.3%)
```

### `terra4mice ci`

Output for CI/CD pipelines:

```bash
# JSON (machine-readable)
terra4mice ci --format json

# Markdown (PR comments)
terra4mice ci --format markdown --comment pr-comment.md

# Fail if convergence < threshold
terra4mice ci --fail-under 80
```

## Spec File Format

```yaml
# terra4mice.spec.yaml
version: "1"

resources:
  feature:
    auth_login:
      attributes:
        description: "User login"
        endpoints: [POST /auth/login]
      depends_on: []

    auth_refresh:
      attributes:
        description: "Token refresh"
      depends_on:
        - feature.auth_login

  module:
    state_manager:
      attributes:
        class: StateManager
        functions: [load, save, list, mark_created]
      files:
        - src/state_manager.py

  endpoint:
    api_users:
      attributes:
        method: GET
        path: /api/users
      depends_on:
        - feature.auth_login
```

### Spec Attributes for AST Verification

With `terra4mice[ast]` installed, these attributes are verified against actual code:

```yaml
attributes:
  class: StateManager              # verified in classes
  functions: [load, save, list]    # verified in defined functions
  entities: [Resource, State]      # verified in classes/interfaces/types/enums
  exports: [WorkerRatingModal]     # verified in exports (TS/JS)
  imports: [useState, useEffect]   # verified in imports
  commands: [init, plan, refresh]  # substring match in functions
  strategies: [explicit_files]     # substring match in functions+classes
```

Supported languages: Python, TypeScript/TSX, JavaScript, Solidity.

## State File Format

```json
{
  "version": "1",
  "serial": 3,
  "last_updated": "2026-01-27T15:30:00",
  "resources": [
    {
      "type": "module",
      "name": "inference",
      "status": "implemented",
      "files": ["src/terra4mice/inference.py"],
      "symbols": {
        "InferenceEngine": {
          "name": "InferenceEngine",
          "kind": "class",
          "status": "implemented",
          "line_start": 94,
          "line_end": 686,
          "file": "src/terra4mice/inference.py"
        },
        "format_report": {
          "name": "format_report",
          "kind": "function",
          "status": "missing"
        }
      }
    }
  ]
}
```

## CI/CD Integration

```yaml
# .github/workflows/terra4mice.yml
name: Check Convergence

on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install terra4mice[ast]
      - run: terra4mice plan --detailed-exitcode
        # Returns 2 if there are pending changes
```

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1 - MVP CLI | DONE | init, plan, refresh, state, mark, apply, ci, diff |
| 2 - tree-sitter AST | DONE | Multi-language deep analysis, spec attribute verification, symbol tracking |
| 3 - Multi-AI Contexts | PLANNED | Track which AI (Claude, Codex, Kimi) has context on what |
| 4 - CI/CD Integration | DONE | GitHub Action, PR comments, convergence badges |
| 5 - Apply Runner | PLANNED | Interactive apply loop, agent integration |
| 6 - Ecosystem Rollout | PLANNED | Deploy across Ultravioleta DAO projects |

### Phase 3: Multi-AI Context Tracking (Next)

When multiple AIs work on the same project, each carries its own isolated context. Phase 3 adds a **context registry** to know which AI has context on what:

```bash
terra4mice contexts list
# AGENT          RESOURCE              LAST SEEN    STATUS
# claude-code    module.inference      2min ago     active
# codex          feature.auth_login    1hr ago      stale
# kimi-2.5       feature.frontend      30min ago    active

terra4mice mark module.auth implemented --agent=codex
terra4mice contexts sync --from=claude-code --to=codex
```

## Philosophy

1. **State before intention** - What exists, not what we want
2. **Evidence before perception** - Tests, not "I think it works"
3. **Convergence before speed** - Better slow and correct
4. **Clarity before heroism** - Visible plan, not magic

## Definition of Done

A project is complete when:

```
$ terra4mice plan

No changes. State matches spec.
```

Nothing else.

## License

MIT - Public good for the developer community.

## Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
