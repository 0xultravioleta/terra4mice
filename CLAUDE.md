# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is terra4mice

terra4mice is a **State-Driven Development Framework** that applies Terraform's mental model to software development. It tracks what's actually implemented in a codebase vs what's declared in a spec, exposing the real gap between intention and reality.

Core loop: `SPEC (desired) → STATE (current) → PLAN (diff) → APPLY (execute)`

## Build & Development Commands

```bash
# Install in development mode
pip install -e ".[dev]"

# Install with tree-sitter AST analysis (optional, Python >=3.10)
pip install -e ".[ast]"

# Install everything
pip install -e ".[all]"

# Run all tests
pytest -p no:pytest_ethereum

# Run a single test file
pytest tests/test_ci.py -p no:pytest_ethereum
pytest tests/test_analyzers.py -p no:pytest_ethereum

# Run a specific test class or method
pytest tests/test_ci.py::TestConvergence -p no:pytest_ethereum
pytest tests/test_analyzers.py::TestScoreAgainstSpec -p no:pytest_ethereum

# Run with coverage
pytest tests/ -p no:pytest_ethereum --cov=terra4mice --cov-report=term-missing

# Format code
black src/ tests/

# Type checking
mypy src/terra4mice/

# Run terra4mice on itself (dogfooding)
terra4mice plan
terra4mice plan --verbose
terra4mice refresh --dry-run
terra4mice refresh --force   # overwrite existing state entries
terra4mice ci --format json  # machine-readable convergence report
```

## Architecture

The codebase follows Terraform's architecture pattern with a clear pipeline:

```
Spec Parser → State Manager → Planner → CLI/CI Output
                  ↑
            Inference Engine
                  ↑
            AST Analyzers (tree-sitter, optional)
```

### Core modules (`src/terra4mice/`)

- **`models.py`** - Data models: `Resource`, `State`, `Spec`, `Plan`, `PlanAction`, `ResourceStatus` enum. Resources are addressed as `type.name` (e.g., `feature.auth_login`). ResourceStatus values: `missing`, `partial`, `implemented`, `broken`, `deprecated`.

- **`spec_parser.py`** - Loads `terra4mice.spec.yaml` (YAML) into `Spec` objects. Validates circular and missing dependencies. Resources are grouped by type (`feature`, `module`, `endpoint`, etc.) in the YAML hierarchy.

- **`state_manager.py`** - Manages `terra4mice.state.json` persistence. `StateManager` class handles CRUD: `mark_created()`, `mark_partial()`, `mark_broken()`, `remove()`. State serial increments on each mutation.

- **`planner.py`** - Diffs spec vs state to produce `Plan` with actions (`create`, `update`, `delete`, `no-op`). `check_dependencies()` verifies dependency graph satisfaction.

- **`inference.py`** - `InferenceEngine` auto-detects resource status from codebase using 4 strategies: explicit file checks, pattern matching, test detection, and AST analysis. When tree-sitter is available, AST analysis verifies spec attributes (functions, classes, exports, imports) against actual code. Falls back to stdlib `ast` (Python), regex (Solidity, TypeScript/JavaScript), or size heuristics. Confidence scores (0.0-1.0) determine final status: >=0.7 → implemented, >=0.3 → partial, <0.3 → missing.

- **`analyzers.py`** - Tree-sitter based multi-language AST analysis (Phase 2). Supports Python, TypeScript/TSX, JavaScript, and Solidity. Uses `Query` + `QueryCursor` API (tree-sitter v0.25+). Key exports: `analyze_file()` for language dispatch, `score_against_spec()` for verifying spec attributes against code, `AnalysisResult` dataclass with functions/classes/exports/imports/entities/decorators. Optional dependency: `pip install terra4mice[ast]`.

- **`ci.py`** - CI/CD output formatters: JSON (machine-readable), Markdown (PR comments with status table), and Shields.io badge JSON. Contains `_compute_convergence()` which scores: implemented=100%, partial=50%, missing/broken=0%.

- **`cli.py`** - argparse-based CLI entry point. Subcommands: `init`, `plan`, `refresh`, `state list/show/rm`, `mark`, `apply`, `ci`. Entry point defined in `pyproject.toml` as `terra4mice.cli:main`.

### Key design decisions

- **No CLI framework dependency** - Uses stdlib `argparse` instead of Click/Typer
- **JSON for state, YAML for spec** - State is machine-managed (JSON), spec is human-written (YAML)
- **Exit code 2** - Signals "plan has changes" (used for CI gating with `--detailed-exitcode`)
- **Inference is non-destructive by default** - `only_missing=True` means existing state entries aren't overwritten unless `--force`
- **tree-sitter is optional** - Degrades gracefully: tree-sitter → stdlib ast → regex → size heuristic
- **tree-sitter API** - Uses `Query(language, pattern)` + `QueryCursor(query)` + `cursor.captures(node)` which returns `dict[str, list[Node]]`. Do NOT use deprecated `language.query()`.

### GitHub Action

`action.yml` defines a composite action that installs terra4mice, runs `terra4mice ci`, posts plan as PR comment, and uploads plan artifacts. Inputs include `fail_on_incomplete`, `fail_under` (convergence threshold), and `post_comment`.

## Testing

Tests live in `tests/`:

- **`test_ci.py`** - CI output formats, convergence calculation, badge generation, ANSI stripping, and CLI integration via `sys.argv` manipulation + stdout capture. (49 tests)
- **`test_analyzers.py`** - Tree-sitter analysis per language, `score_against_spec`, file dispatch, fallback behavior, TypeScript regex fallback. Tests use `@pytest.mark.skipif(not HAS_TREE_SITTER)` so they pass with or without tree-sitter installed. (48 tests)

Test fixtures use helper functions `_make_resource()`, `_make_spec()`, `_make_state()` to build scenarios. CLI integration tests write temp spec/state files and invoke `main()` directly.

**Known issue**: `test_ci_comment_file` fails on Windows due to cp1252 encoding (pre-existing, not a regression).

## File Conventions

- Spec file: `terra4mice.spec.yaml` (version "1", resources grouped by type)
- State file: `terra4mice.state.json` (version "1", resources as flat array with serial counter)
- Resource addressing: `type.name` format (e.g., `module.spec_parser`, `feature.auth_login`)

## Spec Attributes for AST Verification

When tree-sitter is available, these spec attributes are verified against actual code:

```yaml
module:
  state_manager:
    attributes:
      class: StateManager              # verified in classes
      functions: [load, save, list]    # verified in functions
      entities: [Resource, State]      # verified in classes/interfaces/types
      exports: [WorkerRatingModal]     # verified in exports (TS/JS)
      imports: [useState, useEffect]   # verified in imports
      commands: [init, plan, refresh]  # substring match in functions
      strategies: [explicit_files]     # substring match in functions+classes
```

## Phases & Roadmap

- **Phase 1** (MVP CLI) - DONE: init, plan, refresh, state, mark, apply, ci
- **Phase 2** (tree-sitter AST) - DONE: analyzers.py, multi-language, spec attribute scoring
- **Phase 3** (Multi-AI Context Tracking) - SPEC DECLARED: context_registry, context_cli, context_export
- **Phase 4** (CI/CD Integration) - DONE: GitHub Action, PR comments, badges
- **Phase 5** (Apply Runner) - PLANNED
- **Phase 6** (Ecosystem Rollout) - PLANNED

## Self-Dogfooding

This project tracks itself with terra4mice. The root `terra4mice.spec.yaml` and `terra4mice.state.json` define and track the project's own modules, examples, and docs. Current convergence: 86.1% (Phase 3 features account for the gap).
