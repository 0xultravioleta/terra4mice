"""
Basic verification — check whether a resource's implementation exists.

Extensible for future tree-sitter integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import Resource


@dataclass
class VerificationResult:
    """Result of verifying a resource's implementation."""

    passed: bool = False
    score: float = 0.0               # 0.0–1.0, fraction of checks passing
    missing_attributes: list[str] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] score={self.score:.0%}, "
            f"files_checked={len(self.files_checked)}, "
            f"missing_attributes={len(self.missing_attributes)}"
        )


def verify_implementation(
    resource: Resource,
    project_root: str | Path,
) -> VerificationResult:
    """
    Verify that a resource's files exist and are non-empty.

    Args:
        resource: The resource to verify.
        project_root: Root directory of the project.

    Returns:
        VerificationResult with pass/fail and score.
    """
    root = Path(project_root)
    result = VerificationResult()

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
        return result

    found = 0
    for filepath in unique_files:
        result.files_checked.append(filepath)
        full_path = root / filepath
        if full_path.exists() and full_path.stat().st_size > 0:
            found += 1
        else:
            result.missing_attributes.append(f"file missing or empty: {filepath}")

    result.score = found / len(unique_files) if unique_files else 0.0
    result.passed = found == len(unique_files) and len(unique_files) > 0

    return result
