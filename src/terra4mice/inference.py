"""
State Inference - Automatically detect what resources exist in codebase.

Inference strategies:
1. File-based: If spec says "files: [src/auth.py]" and file exists → implemented
2. Pattern-based: Match resource names to file/function patterns
3. Test-based: If tests exist and pass → implemented
4. AST-based: Deep analysis via tree-sitter (with stdlib ast/regex fallback)

The goal: Run `terra4mice refresh` and automatically update state
based on what actually exists in the codebase.
"""

import os
import re
import ast
import glob
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from .models import Spec, State, Resource, ResourceStatus

try:
    from . import analyzers as ts_analyzers
except ImportError:
    ts_analyzers = None


@dataclass
class InferenceResult:
    """Result of inferring a single resource's status."""
    address: str
    status: ResourceStatus
    confidence: float  # 0.0 to 1.0
    evidence: List[str] = field(default_factory=list)
    files_found: List[str] = field(default_factory=list)
    tests_found: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class InferenceConfig:
    """Configuration for inference engine."""
    # Root directory to scan
    root_dir: Path = field(default_factory=Path.cwd)

    # Directories to scan for source files
    source_dirs: List[str] = field(default_factory=lambda: ["src", "lib", "app", "."])

    # Directories to scan for tests
    test_dirs: List[str] = field(default_factory=lambda: ["tests", "test", "spec", "__tests__"])

    # File patterns for different resource types
    file_patterns: Dict[str, List[str]] = field(default_factory=lambda: {
        "feature": ["**/*{name}*.py", "**/*{name}*.ts", "**/*{name}*.js", "**/*{name}*.sol"],
        "endpoint": ["**/routes/*{name}*.py", "**/api/*{name}*.py", "**/handlers/*{name}*.py"],
        "module": ["**/{name}.py", "**/{name}/*.py", "**/{name}/index.py"],
        "contract": ["**/*{name}*.sol", "**/src/*{name}*.sol"],
        "interface": ["**/interfaces/*{name}*.sol", "**/I{name}*.sol", "**/*{name}*.sol"],
        "mock": ["**/mocks/*{name}*.sol", "**/Mock*{name}*.sol"],
        "deploy": ["**/script/*{name}*.sol", "**/deploy/*{name}*.sol"],
        "test": ["**/test/*{name}*.sol", "**/test/*{name}*.t.sol"],
        "datasource": ["**/sources/*{name}*.py", "**/integrations/*{name}*.py", "**/connectors/*{name}*.py"],
        "api": ["**/api/*{name}*.py", "**/routes/*{name}*.py", "**/endpoints/*{name}*.py"],
        "query": ["**/queries/*{name}*.py", "**/graphql/*{name}*.py"],
        "infra": ["**/infra/*{name}*.py", "**/db/*{name}*.py", "**/cache/*{name}*.py"],
    })

    # Test file patterns
    test_patterns: List[str] = field(default_factory=lambda: [
        "test_{name}*.py",
        "*{name}*_test.py",
        "test_{name}*.ts",
        "{name}.test.ts",
        "{name}.spec.ts",
        "{name}*.t.sol",
        "*{name}*.t.sol",
    ])

    # Directories to exclude from scanning (dependencies, build artifacts)
    exclude_dirs: List[str] = field(default_factory=lambda: [
        "lib", "node_modules", "vendor", ".git", "__pycache__",
        "out", "cache", "artifacts", "build", "dist", ".forge-cache",
    ])

    # Minimum confidence to mark as implemented
    min_confidence_implemented: float = 0.7
    min_confidence_partial: float = 0.3


class InferenceEngine:
    """
    Engine for inferring resource status from codebase.

    Usage:
        engine = InferenceEngine(config)
        results = engine.infer_all(spec)
        engine.apply_to_state(results, state)
    """

    def __init__(self, config: InferenceConfig = None):
        self.config = config or InferenceConfig()

    def infer_all(self, spec: Spec) -> List[InferenceResult]:
        """
        Infer status for all resources in spec.

        Args:
            spec: The spec defining desired resources

        Returns:
            List of inference results
        """
        results = []

        for resource in spec.list():
            result = self.infer_resource(resource)
            results.append(result)

        return results

    def infer_resource(self, resource: Resource) -> InferenceResult:
        """
        Infer status for a single resource.

        Combines multiple inference strategies:
        1. Explicit files check (if spec defines files)
        2. Pattern matching
        3. Test detection
        4. AST analysis (if file found)
        """
        result = InferenceResult(
            address=resource.address,
            status=ResourceStatus.MISSING,
            confidence=0.0,
        )

        # Strategy 1: Check explicit files from spec
        if resource.files:
            found, missing = self._check_explicit_files(resource.files)
            if found:
                result.files_found.extend(found)
                result.evidence.append(f"Explicit files found: {found}")
                # All files found = high base confidence; partial = proportional
                ratio = len(found) / len(resource.files)
                if ratio >= 1.0:
                    result.confidence += 0.6  # All declared files exist
                else:
                    result.confidence += 0.4 * ratio

        # Strategy 2: Pattern matching (deduplicate against explicit files)
        pattern_files = self._find_by_pattern(resource)
        new_pattern_files = [f for f in pattern_files if f not in result.files_found]
        if pattern_files:
            result.files_found.extend(new_pattern_files)
            result.evidence.append(f"Pattern match files: {pattern_files}")
            result.confidence += 0.3

        # Strategy 3: Test detection
        test_files = self._find_tests(resource)
        new_test_files = [f for f in test_files if f not in result.tests_found]
        if test_files:
            result.tests_found.extend(new_test_files)
            result.evidence.append(f"Tests found: {test_files}")
            result.confidence += 0.2

        # Strategy 4: AST analysis (if we found files)
        if result.files_found:
            ast_confidence = self._analyze_ast(resource, result.files_found)
            if ast_confidence > 0:
                result.evidence.append(f"AST analysis: {ast_confidence:.0%} match")
                result.confidence += ast_confidence * 0.3

        # Determine final status based on confidence
        result.confidence = min(result.confidence, 1.0)

        if result.confidence >= self.config.min_confidence_implemented:
            result.status = ResourceStatus.IMPLEMENTED
            result.reason = "High confidence: files, patterns, and/or tests found"
        elif result.confidence >= self.config.min_confidence_partial:
            result.status = ResourceStatus.PARTIAL
            result.reason = "Medium confidence: some evidence found"
        else:
            result.status = ResourceStatus.MISSING
            result.reason = "Low confidence: insufficient evidence"

        return result

    def _check_explicit_files(self, files: List[str]) -> Tuple[List[str], List[str]]:
        """Check if explicitly declared files exist."""
        found = []
        missing = []

        for file_path in files:
            full_path = self.config.root_dir / file_path
            if full_path.exists():
                found.append(file_path)
            else:
                missing.append(file_path)

        return found, missing

    def _is_excluded(self, path: Path) -> bool:
        """Check if a path is inside an excluded directory."""
        exclude_dirs = getattr(self.config, 'exclude_dirs', [
            "lib", "node_modules", "vendor", ".git", "__pycache__",
            "out", "cache", "artifacts", "build", "dist", ".forge-cache",
        ])
        parts = path.parts
        for excluded in exclude_dirs:
            if excluded in parts:
                return True
        return False

    def _find_by_pattern(self, resource: Resource) -> List[str]:
        """Find files matching resource patterns."""
        found = []

        # Get patterns for this resource type
        patterns = list(self.config.file_patterns.get(resource.type, []))

        # Also try generic patterns
        generic_patterns = [
            f"**/*{resource.name}*.py",
            f"**/*{resource.name}*.ts",
            f"**/*{resource.name}*.tsx",
            f"**/*{resource.name}*.js",
            f"**/*{resource.name}*.jsx",
            f"**/*{resource.name}*.sol",
            f"**/{resource.name}/**/*.py",
            f"**/{resource.name}/**/*.ts",
            f"**/{resource.name}/**/*.tsx",
        ]
        patterns.extend(generic_patterns)

        for pattern_template in patterns:
            # Replace {name} with actual resource name
            pattern = pattern_template.format(name=resource.name)

            # Search in source directories
            for source_dir in self.config.source_dirs:
                search_path = self.config.root_dir / source_dir
                if not search_path.exists():
                    continue

                # Use glob to find matches
                matches = list(search_path.glob(pattern))
                for match in matches:
                    rel_path = str(match.relative_to(self.config.root_dir))
                    # Skip files in excluded directories
                    if self._is_excluded(match.relative_to(self.config.root_dir)):
                        continue
                    if rel_path not in found:
                        found.append(rel_path)

        return found[:10]  # Limit to avoid noise

    def _find_tests(self, resource: Resource) -> List[str]:
        """Find test files for a resource."""
        found = []

        for test_dir in self.config.test_dirs:
            test_path = self.config.root_dir / test_dir
            if not test_path.exists():
                continue

            for pattern_template in self.config.test_patterns:
                pattern = pattern_template.format(name=resource.name)
                matches = list(test_path.glob(f"**/{pattern}"))
                for match in matches:
                    rel_path = str(match.relative_to(self.config.root_dir))
                    if self._is_excluded(match.relative_to(self.config.root_dir)):
                        continue
                    if rel_path not in found:
                        found.append(rel_path)

        return found

    def _analyze_ast(self, resource: Resource, files: List[str]) -> float:
        """
        Analyze files to determine implementation completeness.

        Uses tree-sitter when available for deep analysis, with fallbacks:
        1. tree-sitter (Python, TS, JS, Solidity) -> score_against_spec
        2. stdlib ast (Python only)
        3. regex (Solidity)
        4. regex (TypeScript/JavaScript)
        5. size heuristic (config/docs)

        Returns confidence score 0.0 to 1.0
        """
        use_tree_sitter = (
            ts_analyzers is not None and ts_analyzers.is_available()
        )
        total_score = 0.0
        analyzed = 0

        for file_path in files:
            full_path = self.config.root_dir / file_path
            if not full_path.exists():
                continue

            # Try tree-sitter first for supported extensions
            if use_tree_sitter and file_path.endswith(
                ('.py', '.ts', '.tsx', '.js', '.jsx', '.sol')
            ):
                try:
                    with open(full_path, 'rb') as f:
                        source_bytes = f.read()
                    ts_result = ts_analyzers.analyze_file(file_path, source_bytes)
                    if ts_result is not None:
                        spec_score = ts_analyzers.score_against_spec(
                            ts_result, resource.attributes
                        )
                        # Base score from presence of any names
                        base_score = min(0.3, len(ts_result.all_names) * 0.05)
                        score = max(spec_score, base_score)
                        total_score += score
                        analyzed += 1
                        continue
                except (IOError, UnicodeDecodeError):
                    pass
                # Fall through to legacy analysis if tree-sitter failed

            if file_path.endswith('.py'):
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        source = f.read()
                    tree = ast.parse(source)
                    score = self._score_ast(tree, resource)
                    total_score += score
                    analyzed += 1
                except (SyntaxError, UnicodeDecodeError):
                    continue
            elif file_path.endswith('.sol'):
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        source = f.read()
                    score = self._score_solidity(source, resource)
                    total_score += score
                    analyzed += 1
                except (UnicodeDecodeError, IOError):
                    continue
            elif file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        source = f.read()
                    score = self._score_typescript_fallback(source, resource)
                    total_score += score
                    analyzed += 1
                except (UnicodeDecodeError, IOError):
                    continue
            elif file_path.endswith(('.md', '.yaml', '.yml', '.toml', '.json')):
                # For config/doc files, just check they have content
                try:
                    size = full_path.stat().st_size
                    if size > 50:
                        total_score += 0.5
                        analyzed += 1
                except IOError:
                    continue

        if analyzed == 0:
            return 0.0

        return total_score / analyzed

    def _score_ast(self, tree: ast.AST, resource: Resource) -> float:
        """
        Score an AST based on how well it implements the resource.

        Looks for:
        - Functions/classes with resource name
        - Docstrings mentioning resource
        - Expected patterns for resource type
        """
        score = 0.0

        # Collect all function and class names
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                names.append(node.name.lower())
            elif isinstance(node, ast.ClassDef):
                names.append(node.name.lower())
            elif isinstance(node, ast.AsyncFunctionDef):
                names.append(node.name.lower())

        # Check if resource name appears in function/class names
        resource_name_lower = resource.name.lower().replace('_', '')
        for name in names:
            name_normalized = name.replace('_', '')
            if resource_name_lower in name_normalized:
                score += 0.3
                break

        # Check for expected patterns based on resource type
        if resource.type == "endpoint":
            # Look for route decorators or handler patterns
            has_route = any(
                'route' in n or 'endpoint' in n or 'handler' in n or 'api' in n
                for n in names
            )
            if has_route:
                score += 0.3

        elif resource.type == "feature":
            # Features usually have multiple functions
            if len(names) >= 2:
                score += 0.2

        elif resource.type == "module":
            # Modules usually have __init__ or main class
            has_init = any('__init__' in n or 'main' in n for n in names)
            if has_init:
                score += 0.2

        # Check for test-like functions (suggests implementation exists)
        has_assertions = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                has_assertions = True
                break
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in ['assertEqual', 'assertTrue', 'assertFalse', 'assert_called']:
                        has_assertions = True
                        break

        if has_assertions:
            score += 0.2

        return min(score, 1.0)

    def _score_solidity(self, source: str, resource: Resource) -> float:
        """
        Score a Solidity file based on content analysis.

        Looks for:
        - Contract/interface/library declarations
        - Function definitions
        - Events, modifiers
        - Test patterns (forge-std)
        - Resource name in declarations
        """
        score = 0.0
        source_lower = source.lower()
        resource_name_lower = resource.name.lower().replace('_', '')

        # Count lines of code (non-empty, non-comment)
        lines = [l.strip() for l in source.split('\n') if l.strip() and not l.strip().startswith('//')]
        loc = len(lines)

        # Check for contract/interface/library declarations
        declarations = re.findall(
            r'\b(contract|interface|library|abstract\s+contract)\s+(\w+)',
            source
        )
        if declarations:
            score += 0.2
            # Check if resource name appears in a declaration
            for _, name in declarations:
                name_lower = name.lower().replace('_', '')
                if resource_name_lower in name_lower or name_lower in resource_name_lower:
                    score += 0.2
                    break

        # Check for function definitions
        functions = re.findall(r'\bfunction\s+(\w+)', source)
        if functions:
            score += 0.1
            # More functions = more substantial
            if len(functions) >= 5:
                score += 0.1

        # Resource-type-specific scoring
        if resource.type == "contract":
            # Contracts should have state variables, events, modifiers
            has_events = bool(re.search(r'\bevent\s+\w+', source))
            has_mappings = bool(re.search(r'\bmapping\s*\(', source))
            has_modifiers = bool(re.search(r'\bmodifier\s+\w+', source))
            if has_events:
                score += 0.1
            if has_mappings:
                score += 0.1
            if has_modifiers:
                score += 0.05
            # Substantial contract
            if loc > 100:
                score += 0.1

        elif resource.type == "interface":
            # Interfaces have function signatures without bodies
            if bool(re.search(r'\binterface\s+\w+', source)):
                score += 0.2

        elif resource.type == "mock":
            # Mocks typically have "Mock" in the name
            if 'mock' in source_lower:
                score += 0.2

        elif resource.type == "test":
            # Test files use forge-std
            if 'forge-std' in source or 'Test' in source:
                score += 0.1
            # Count test functions
            test_funcs = re.findall(r'\bfunction\s+(test\w+)', source)
            if test_funcs:
                score += 0.2
                if len(test_funcs) >= 10:
                    score += 0.1

        elif resource.type == "deploy":
            # Deploy scripts use forge Script
            if 'Script' in source or 'run()' in source:
                score += 0.2

        elif resource.type == "docs":
            # For non-sol docs, just being non-empty is good
            if loc > 20:
                score += 0.3

        return min(score, 1.0)

    def _score_typescript_fallback(self, source: str, resource: Resource) -> float:
        """
        Regex-based analysis for TypeScript/JavaScript when tree-sitter is not available.

        Detects function, class, interface, type, enum declarations and exports.
        """
        score = 0.0
        source_lower = source.lower()
        resource_name_lower = resource.name.lower().replace('_', '')

        # Find declarations
        declarations = re.findall(
            r'\b(?:function|class|interface|type|enum)\s+(\w+)', source
        )
        if declarations:
            score += 0.2
            for decl in declarations:
                decl_lower = decl.lower().replace('_', '')
                if resource_name_lower in decl_lower or decl_lower in resource_name_lower:
                    score += 0.2
                    break

        # Check exports
        exports = re.findall(r'\bexport\s+(?:default\s+)?(?:function|class|const|let|var|interface|type|enum)\s+(\w+)', source)
        if exports:
            score += 0.1

        # Check attributes.functions
        spec_functions = resource.attributes.get("functions")
        if spec_functions and isinstance(spec_functions, list):
            found = 0
            for fn in spec_functions:
                if fn.lower() in source_lower:
                    found += 1
            if spec_functions:
                score += 0.3 * (found / len(spec_functions))

        # Check attributes.class
        spec_class = resource.attributes.get("class")
        if spec_class and isinstance(spec_class, str):
            if spec_class.lower() in source_lower:
                score += 0.2

        return min(score, 1.0)

    def apply_to_state(
        self,
        results: List[InferenceResult],
        state: State,
        only_missing: bool = True
    ) -> List[str]:
        """
        Apply inference results to state.

        Args:
            results: Inference results to apply
            state: State to update
            only_missing: Only update resources that are missing in state

        Returns:
            List of addresses that were updated
        """
        updated = []

        for result in results:
            # Skip if only updating missing and resource already exists
            if only_missing:
                existing = state.get(result.address)
                if existing and existing.status != ResourceStatus.MISSING:
                    continue

            # Skip if no evidence found
            if result.status == ResourceStatus.MISSING:
                continue

            # Create or update resource in state
            resource_type, resource_name = result.address.split(".", 1)
            resource = Resource(
                type=resource_type,
                name=resource_name,
                status=result.status,
                files=result.files_found,
                tests=result.tests_found,
                attributes={
                    "inference_confidence": result.confidence,
                    "inference_evidence": result.evidence,
                }
            )

            state.set(resource)
            updated.append(result.address)

        return updated


def infer_state(
    spec: Spec,
    root_dir: Path = None,
    config: InferenceConfig = None
) -> Tuple[State, List[InferenceResult]]:
    """
    Convenience function to infer state from spec.

    Args:
        spec: Spec defining desired resources
        root_dir: Root directory to scan (defaults to cwd)
        config: Inference configuration

    Returns:
        Tuple of (new State, list of InferenceResults)
    """
    if config is None:
        config = InferenceConfig()

    if root_dir:
        config.root_dir = Path(root_dir)

    engine = InferenceEngine(config)
    results = engine.infer_all(spec)

    state = State()
    engine.apply_to_state(results, state, only_missing=False)

    return state, results


def format_inference_report(results: List[InferenceResult]) -> str:
    """Format inference results as human-readable report."""
    lines = []
    lines.append("")
    lines.append("Inference Report")
    lines.append("=" * 60)
    lines.append("")

    # Group by status
    by_status = {}
    for r in results:
        status = r.status.value
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(r)

    # Colors
    colors = {
        "implemented": "\033[32m",  # Green
        "partial": "\033[33m",      # Yellow
        "missing": "\033[31m",      # Red
        "broken": "\033[31m",       # Red
    }
    reset = "\033[0m"

    for status in ["implemented", "partial", "missing"]:
        if status not in by_status:
            continue

        color = colors.get(status, "")
        lines.append(f"{color}{status.upper()}{reset} ({len(by_status[status])} resources)")
        lines.append("-" * 40)

        for r in by_status[status]:
            confidence_bar = "#" * int(r.confidence * 10) + "-" * (10 - int(r.confidence * 10))
            lines.append(f"  {r.address}")
            lines.append(f"    Confidence: [{confidence_bar}] {r.confidence:.0%}")

            if r.files_found:
                lines.append(f"    Files: {', '.join(r.files_found[:3])}")
            if r.tests_found:
                lines.append(f"    Tests: {', '.join(r.tests_found[:3])}")
            if r.evidence and status != "missing":
                lines.append(f"    Evidence: {r.evidence[0]}")

            lines.append("")

        lines.append("")

    # Summary
    total = len(results)
    implemented = len(by_status.get("implemented", []))
    partial = len(by_status.get("partial", []))
    missing = len(by_status.get("missing", []))

    lines.append("Summary")
    lines.append("-" * 40)
    lines.append(f"  Total resources: {total}")
    lines.append(f"  {colors['implemented']}Implemented: {implemented}{reset}")
    lines.append(f"  {colors['partial']}Partial: {partial}{reset}")
    lines.append(f"  {colors['missing']}Missing: {missing}{reset}")

    if total > 0:
        convergence = (implemented + partial * 0.5) / total * 100
        lines.append(f"  Convergence: {convergence:.1f}%")

    lines.append("")

    return "\n".join(lines)
