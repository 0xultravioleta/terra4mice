# terra4mice

> State-Driven Development Framework
>
> "Software isn't done when it works. It's done when state converges with spec."

Like Git tracks file changes, terra4mice tracks feature completeness. While Git shows `git diff` for code, terra4mice shows `terra4mice plan` for implementation gaps.

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

## When NOT to Use terra4mice

❌ Projects <10 resources (GitHub Issues suffice)
❌ Greenfield R&D with changing requirements (overhead not justified)
❌ Teams without spec-first culture (terra4mice forces it)
❌ Pure code quality needs (use SonarQube/linters instead)

## When terra4mice Shines

✅ Multi-AI development workflows (Claude Code + Copilot + Cursor)
✅ Livecoding/streaming projects (transparent progress tracking)
✅ Spec drift as chronic problem (incomplete implementations)
✅ Dependency tracking across features

## Quick Start

```bash
# Install (tree-sitter AST analysis included by default)
pip install terra4mice

# With remote state backend (S3 + DynamoDB locking)
pip install terra4mice[remote]

# All extras
pip install terra4mice[all]

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

Context-aware apply engine with DAG ordering and multiple execution modes:

```bash
# Interactive mode (default) — manual implementation with guidance
terra4mice apply

# Auto mode — AI agent implements resources automatically
terra4mice apply --mode auto --agent claude-code

# Hybrid mode — AI implements, human reviews each change
terra4mice apply --mode hybrid --agent claude-code

# Market mode — post tasks to Execution Market for bounty-based implementation
terra4mice apply --mode market --bounty 50 --market-api-key $KEY

# Parallel execution (any mode) — respects dependency DAG
terra4mice apply --mode auto --max-workers 4

# Dry run — show plan without executing
terra4mice apply --dry-run

# Apply a single resource
terra4mice apply --resource feature.auth_login

# With verification level
terra4mice apply --mode auto --verify-level full
```

Interactive mode example:

```
$ terra4mice apply

════════════════════════════════════════════════════════════
 Action 1/3: + create feature.auth_login
════════════════════════════════════════════════════════════

 Resource declared in spec but not in state

 Dependencies:
   (none)

 Attributes:
   - endpoints: ['POST /auth/login']

──────────────────────────────────────────────────────────
 [i]mplement  [p]artial  [s]kip  [a]i-assist  [m]arket  [q]uit
→ i
Files that implement this (comma-separated): src/auth.py
✓ Marked as implemented: feature.auth_login
```

#### Apply Modes

| Mode | Description |
|------|-------------|
| **interactive** | Manual implementation with dependency status, context, and suggested files |
| **auto** | AI agent implements resources — supports Claude Code, Codex, or custom agents |
| **hybrid** | AI generates implementation, human reviews and accepts/rejects/edits |
| **market** | Posts tasks to [Execution Market](https://execution.market) for bounty-based implementation |

#### Agent Chaining & Fallbacks

Use comma-separated agent names for automatic fallback:

```bash
# Try Claude Code first, fall back to Codex if it fails
terra4mice apply --mode auto --agent claude-code,codex
```

#### Parallel Execution Engine

The parallel executor respects the dependency DAG — independent resources run concurrently while dependent resources wait:

```bash
# 4 workers process independent resources in parallel
terra4mice apply --mode auto --max-workers 4
```

#### Verification Levels

| Level | Checks |
|-------|--------|
| `basic` | Files exist and are non-empty |
| `git_diff` | Basic + git diff shows changes to expected files |
| `full` | git_diff + tree-sitter AST verification against spec attributes |

### `terra4mice state pull / push`

Sync state between local and remote backends:

```bash
# Download remote state to a local file
terra4mice state pull -o local_backup.json

# Upload local state to the remote backend
terra4mice state push -i local_backup.json
```

### `terra4mice force-unlock <lock-id>`

Force-release a stuck state lock (when a process crashes mid-operation):

```bash
terra4mice force-unlock a1b2c3d4-5678-9abc-def0-123456789abc
# Lock forcefully released: a1b2c3d4-...
# WARNING: Releasing a lock held by another process may cause state corruption.
```

### `terra4mice init --migrate-state`

Migrate local state to a remote backend configured in the spec:

```bash
# 1. Add backend: section to terra4mice.spec.yaml
# 2. Run migration
terra4mice init --migrate-state
# State migrated to s3 backend.
#   Resources: 12
#   Serial: 45
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

## Remote State Backend

Store state in S3 with optional DynamoDB locking for team collaboration. Add a `backend:` section to your spec:

```yaml
# terra4mice.spec.yaml
version: "1"

backend:
  type: s3
  config:
    bucket: my-terra4mice-state
    key: projects/myapp/terra4mice.state.json
    region: us-east-1
    lock_table: terra4mice-locks    # DynamoDB table (optional)
    profile: my-aws-profile         # AWS profile (optional)
    encrypt: true                   # S3 SSE (optional)

resources:
  # ... your spec unchanged ...
```

Without `backend:` or with `type: local`, behavior is unchanged (local file).

### DynamoDB Lock Table Setup

```bash
aws dynamodb create-table \
  --table-name terra4mice-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

### How Locking Works

When a `backend` with `lock_table` is configured, mutating commands (`refresh`, `mark`, `lock`, `unlock`, `state rm`, `state push`) automatically acquire a DynamoDB lock before writing. If another process holds the lock, the command fails with a descriptive error showing who holds it and when it was acquired.

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
| 1 - MVP CLI | ✅ DONE | init, plan, refresh, state, mark, apply, ci, diff |
| 2 - tree-sitter AST | ✅ DONE | Multi-language deep analysis, spec attribute verification, symbol tracking |
| 3 - Multi-AI Contexts | ✅ DONE | Track which AI (Claude, Codex, Kimi) has context on what |
| 4 - CI/CD Integration | ✅ DONE | GitHub Action, PR comments, convergence badges |
| 4.5 - Remote State | ✅ DONE | S3 backend, DynamoDB locking, state pull/push, migrate-state |
| 5 - Apply Runner | ✅ DONE | DAG-ordered execution, Auto/Hybrid/Market modes, parallel engine, verification |
| 5.1 - Agent Dispatch | ✅ DONE | Claude Code/Codex backends, agent chaining, fallbacks |
| 5.2 - Parallel Engine | ✅ DONE | ThreadPoolExecutor with DAG-aware scheduling, failure cascading |
| 5.3 - Execution Market | ✅ DONE | Market mode, bounty tasks, dry-run support |
| 5.4 - E2E Tests & PyPI | ✅ DONE | Comprehensive e2e tests, `python -m terra4mice`, PyPI-ready packaging |
| 6 - Ecosystem Rollout | PLANNED | Deploy across Ultravioleta DAO projects |

## Multi-Agent Context Tracking

When multiple AIs work on the same project, each carries its own isolated context. The `contexts` command group provides a **context registry** to know which AI has context on what resources.

### `terra4mice contexts list`

Shows all agents and their resource contexts:

```
$ terra4mice contexts list

AGENT          RESOURCE              LAST SEEN    STATUS
claude-code    module.inference      2min ago     active
claude-code    module.analyzers      2min ago     active
codex          feature.auth_login    1hr ago      stale
kimi-2.5       feature.frontend      30min ago    active

Agents: 3 | Active contexts: 4 | Stale: 1
```

### `terra4mice contexts show <agent>`

Shows detailed context for a specific agent:

```
$ terra4mice contexts show claude-code

# claude-code
Last active: 2min ago
Status: active

Resources in context:
  module.inference      implemented    2min ago
  module.analyzers      implemented    2min ago
  feature.ci            partial        15min ago

Files touched:
  src/terra4mice/inference.py
  src/terra4mice/analyzers.py
```

### `terra4mice contexts sync`

Synchronize context between agents:

```bash
# Sync all context from one agent to another
terra4mice contexts sync --from=claude-code --to=codex

# Sync specific resources only
terra4mice contexts sync --from=claude-code --to=codex --resources=module.inference,module.analyzers

# Dry run to see what would sync
terra4mice contexts sync --from=claude-code --to=codex --dry-run
```

### `terra4mice contexts export / import`

Export and import agent contexts for backup or transfer:

```bash
# Export an agent's context to a file
terra4mice contexts export claude-code -o claude-context.json

# Import context from a file
terra4mice contexts import codex -i claude-context.json

# Export all agents
terra4mice contexts export --all -o all-contexts.json
```

### `terra4mice mark --agent`

Mark resources with agent attribution:

```bash
# Mark as implemented by a specific agent
terra4mice mark module.auth --status implemented --agent=codex --files src/auth.py

# Mark as partial with agent context
terra4mice mark feature.payments --status partial --agent=claude-code --reason "Missing refund logic"
```

This automatically updates the context registry so other agents know who worked on what.

## Multi-Agent Workflow Examples

### Example 1: Handoff Between Agents

When one agent completes work and another takes over:

```bash
# Claude finishes working on inference
terra4mice mark module.inference --status implemented --agent=claude-code --files src/inference.py

# Before Codex starts, sync the context
terra4mice contexts sync --from=claude-code --to=codex --resources=module.inference

# Codex can now see what Claude did
terra4mice contexts show codex
```

### Example 2: Parallel Development

Multiple agents working on different features:

```bash
# See who's working on what
terra4mice contexts list

# Each agent marks their own work
terra4mice mark feature.auth --agent=claude-code --status implemented
terra4mice mark feature.payments --agent=kimi-2.5 --status partial

# Check for conflicts (same resource, different agents)
terra4mice plan --check-conflicts
```

### Example 3: Context Recovery

When an agent loses context (new session):

```bash
# Export context before session ends
terra4mice contexts export claude-code -o session-backup.json

# In new session, restore context
terra4mice contexts import claude-code -i session-backup.json

# Or sync from another agent that has current context
terra4mice contexts sync --from=codex --to=claude-code
```

### Example 4: CI Integration with Multi-Agent

```yaml
# .github/workflows/terra4mice.yml
- name: Check convergence and contexts
  run: |
    terra4mice plan --detailed-exitcode
    terra4mice contexts list --format json > contexts.json
    # Fail if any contexts are stale > 24h
    terra4mice contexts list --stale-threshold 24h --fail-if-stale
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
