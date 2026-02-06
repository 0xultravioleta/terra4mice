# Changelog

All notable changes to terra4mice will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-02-06

### Added
- Initial release of terra4mice
- Core CLI with `plan`, `apply`, `state list`, `state show`, `mark` commands
- Spec format in YAML with resource definitions
- State inference engine with multi-language support:
  - Python: AST analysis with tree-sitter-like scoring
  - Solidity: Contract, interface, mock, test, deploy detection
  - TypeScript/JavaScript: Module and function detection
  - Generic: File existence and size-based scoring
- CI/CD integration:
  - `terra4mice ci` command with JSON/Markdown/text output formats
  - GitHub Action composite workflow (`action.yml`)
  - Pre-commit hook (`.pre-commit-hooks.yaml`)
  - Shields.io badge generation
- Convergence calculation and visualization
- Exit codes for CI gates (0=converged, 1=error, 2=incomplete)
- `--fail-under N` flag for convergence thresholds
- Artifact generation for CI pipelines

### Dogfooded On
- `describe-net-contracts` (SealRegistry) - Foundry/Solidity project
- `terra4mice` itself - Meta-dogfooding with 14 resources

### Technical Notes
- Pure Python implementation
- Single dependency: PyYAML
- MIT License
- Tested on Python 3.9-3.12
