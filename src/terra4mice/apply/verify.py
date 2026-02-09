"""
Basic verification — check whether a resource's implementation exists.

Extensible for future tree-sitter integration.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Set

from ..models import Resource


class VerificationLevel(Enum):
    """Verification level for implementation checking."""
    BASIC = "basic"           # Check if files exist and are non-empty
    GIT_DIFF = "git_diff"     # Check git diff shows actual changes  
    FULL = "full"            # Future: tree-sitter AST verification


@dataclass
class VerificationResult:
    """Result of verifying a resource's implementation."""

    passed: bool = False
    score: float = 0.0               # 0.0–1.0, fraction of checks passing
    missing_attributes: list[str] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)
    level: VerificationLevel = VerificationLevel.BASIC
    git_diff_stats: Optional[str] = None      # Git diff --stat output
    git_changed_files: List[str] = field(default_factory=list)  # Files changed in git diff
    verification_details: List[str] = field(default_factory=list)  # Detailed verification info
    ast_score: Optional[float] = None         # AST verification score (0.0-1.0)
    ast_symbols_found: List[str] = field(default_factory=list)    # Symbols found in AST
    ast_symbols_expected: List[str] = field(default_factory=list) # Symbols expected from spec

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        level_info = f" level={self.level.value}"
        git_info = f", git_changed={len(self.git_changed_files)}" if self.git_changed_files else ""
        ast_info = f", ast_score={self.ast_score:.0%}" if self.ast_score is not None else ""
        return (
            f"[{status}] score={self.score:.0%}{level_info}, "
            f"files_checked={len(self.files_checked)}"
            f"{git_info}{ast_info}, "
            f"missing_attributes={len(self.missing_attributes)}"
        )


def verify_implementation(
    resource: Resource,
    project_root: str | Path,
    level: VerificationLevel = VerificationLevel.BASIC,
) -> VerificationResult:
    """
    Verify that a resource's implementation meets the specified verification level.

    Args:
        resource: The resource to verify.
        project_root: Root directory of the project.
        level: Verification level to use.

    Returns:
        VerificationResult with pass/fail and score.
    """
    root = Path(project_root)
    result = VerificationResult(level=level)

    # Gather files to check: resource.files + attributes.get("files", [])
    files: list[str] = list(resource.files)
    attr_files = resource.attributes.get("files", [])
    if isinstance(attr_files, list):
        files.extend(attr_files)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_files: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    if not unique_files:
        # No files declared — can't verify, treat as pass with 0 score
        result.passed = True
        result.score = 0.0
        result.missing_attributes.append("no files declared")
        result.verification_details.append("No files declared in resource spec")
        return result

    # BASIC verification: check files exist and are non-empty
    basic_score = _verify_basic_files(unique_files, root, result)
    
    if level == VerificationLevel.BASIC:
        result.score = basic_score
        result.passed = basic_score == 1.0 and len(unique_files) > 0
        return result
    
    # GIT_DIFF verification: check git shows actual changes
    if level == VerificationLevel.GIT_DIFF:
        git_score = _verify_git_diff(unique_files, root, result)
        # Combine basic and git scores (both must pass for full score)
        result.score = min(basic_score, git_score)
        result.passed = (basic_score == 1.0 and git_score == 1.0 and len(unique_files) > 0)
        return result
    
    # FULL verification: tree-sitter AST verification
    if level == VerificationLevel.FULL:
        git_score = _verify_git_diff(unique_files, root, result)
        ast_score = _verify_ast_spec(resource, unique_files, root, result)
        
        # Weighted combination: 30% basic, 30% git diff, 40% AST spec match
        result.score = (0.3 * basic_score + 0.3 * git_score + 0.4 * ast_score)
        result.passed = (basic_score == 1.0 and git_score == 1.0 and ast_score > 0.0 and len(unique_files) > 0)
        return result

    return result


def _verify_basic_files(
    files: List[str], 
    root: Path, 
    result: VerificationResult
) -> float:
    """Verify files exist and are non-empty. Returns score 0.0-1.0."""
    found = 0
    for filepath in files:
        result.files_checked.append(filepath)
        full_path = root / filepath
        if full_path.exists() and full_path.stat().st_size > 0:
            found += 1
            result.verification_details.append(f"✓ {filepath} exists and non-empty")
        else:
            result.missing_attributes.append(f"file missing or empty: {filepath}")
            result.verification_details.append(f"✗ {filepath} missing or empty")

    return found / len(files) if files else 0.0


def _verify_ast_spec(
    resource: Resource,
    files: List[str],
    root: Path,
    result: VerificationResult
) -> float:
    """Verify AST spec matches using tree-sitter analysis. Returns score 0.0-1.0."""
    try:
        from ..analyzers import analyze_file, score_against_spec, is_available
    except ImportError:
        result.verification_details.append("✗ analyzers module not available")
        result.ast_score = 0.0
        return 0.0
    
    if not is_available():
        result.verification_details.append("✗ tree-sitter not available, falling back gracefully")
        result.ast_score = 0.0
        return 0.0
    
    total_score = 0.0
    analyzed_files = 0
    all_symbols_found = set()
    
    for filepath in files:
        full_path = root / filepath
        if not full_path.exists():
            continue
            
        try:
            with open(full_path, 'rb') as f:
                source = f.read()
            
            analysis_result = analyze_file(str(filepath), source)
            if analysis_result is None:
                result.verification_details.append(f"✗ {filepath}: unsupported file type for AST analysis")
                continue
                
            # Score this file's analysis against the resource attributes
            file_score = score_against_spec(analysis_result, resource.attributes)
            total_score += file_score
            analyzed_files += 1
            
            # Collect all symbols found
            all_symbols_found.update(analysis_result.all_names)
            
            if file_score > 0:
                result.verification_details.append(f"✓ {filepath}: AST spec match {file_score:.0%}")
            else:
                result.verification_details.append(f"✗ {filepath}: AST spec match {file_score:.0%}")
                
        except Exception as e:
            result.verification_details.append(f"✗ {filepath}: AST analysis error: {e}")
            continue
    
    if analyzed_files == 0:
        result.verification_details.append("✗ No files could be analyzed with AST")
        result.ast_score = 0.0
        return 0.0
    
    # Calculate average score across all analyzed files
    avg_score = total_score / analyzed_files
    result.ast_score = avg_score
    result.ast_symbols_found = list(all_symbols_found)
    
    # Build list of expected symbols from resource attributes
    expected_symbols = []
    for attr_name in ["functions", "classes", "entities", "exports"]:
        attr_value = resource.attributes.get(attr_name)
        if isinstance(attr_value, list):
            expected_symbols.extend(attr_value)
        elif isinstance(attr_value, str):
            expected_symbols.append(attr_value)
    
    result.ast_symbols_expected = expected_symbols
    
    if avg_score > 0.5:
        result.verification_details.append(f"✓ AST verification passed with {avg_score:.0%} match")
    else:
        result.verification_details.append(f"✗ AST verification failed with {avg_score:.0%} match")
    
    return avg_score


def _verify_git_diff(
    expected_files: List[str], 
    root: Path, 
    result: VerificationResult
) -> float:
    """Verify git diff shows actual changes to expected files. Returns score 0.0-1.0."""
    try:
        # Run git diff --stat to see what files changed
        proc = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if proc.returncode != 0:
            result.verification_details.append(f"Git diff failed: {proc.stderr}")
            return 0.0
        
        result.git_diff_stats = proc.stdout
        
        if not proc.stdout.strip():
            result.verification_details.append("✗ Git diff shows no changes")
            return 0.0
        
        # Run git diff --name-only to get list of changed files
        proc_files = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if proc_files.returncode != 0:
            result.verification_details.append(f"Git diff --name-only failed: {proc_files.stderr}")
            return 0.0
        
        changed_files = [f.strip() for f in proc_files.stdout.split('\n') if f.strip()]
        result.git_changed_files = changed_files
        
        if not changed_files:
            result.verification_details.append("✗ Git diff shows no changed files")
            return 0.0
        
        # Check if expected files are in the changed files
        expected_set = set(expected_files)
        changed_set = set(changed_files)
        
        # Files that were expected and actually changed
        correctly_changed = expected_set.intersection(changed_set)
        
        if correctly_changed:
            result.verification_details.append(
                f"✓ Git diff shows changes to expected files: {', '.join(correctly_changed)}"
            )
            # Score based on how many expected files were changed
            score = len(correctly_changed) / len(expected_set)
        else:
            result.verification_details.append(
                f"✗ Git diff shows changes to {', '.join(changed_files)} but expected {', '.join(expected_files)}"
            )
            score = 0.0
        
        # Additional check: git diff should not be empty (agent actually changed something)
        if proc.stdout.strip():
            result.verification_details.append(f"✓ Git diff is non-empty ({len(proc.stdout.split())} words)")
        else:
            result.verification_details.append("✗ Git diff is empty")
            score = 0.0
        
        return score
        
    except subprocess.TimeoutExpired:
        result.verification_details.append("Git diff timed out")
        return 0.0
    except FileNotFoundError:
        result.verification_details.append("Git not found in PATH")
        return 0.0
    except Exception as e:
        result.verification_details.append(f"Git diff error: {e}")
        return 0.0
