"""
Spec Parser - Load and validate spec.yaml files.

Spec format:
```yaml
version: "1"
resources:
  feature:
    auth_login:
      status: required
      depends_on: []
      attributes:
        endpoints: [POST /auth/login]
        tests: [unit, integration]
    auth_refresh:
      status: required
      depends_on: [feature.auth_login]
```
"""

import re

import yaml
from pathlib import Path
from typing import Union

from .models import Spec, Resource, ResourceStatus


DEFAULT_SPEC_FILE = "terra4mice.spec.yaml"


def load_spec_with_backend(path: Union[str, Path] = None):
    """
    Load spec and backend config from YAML.

    Returns:
        Tuple of (Spec, Optional[dict]) where the dict is the backend config
    """
    if path is None:
        path = Path.cwd() / DEFAULT_SPEC_FILE
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    backend_config = data.get("backend")
    spec = parse_spec(data)
    return spec, backend_config


def load_spec(path: Union[str, Path] = None) -> Spec:
    """
    Load spec from YAML file.

    Args:
        path: Path to spec file. If None, looks for terra4mice.spec.yaml

    Returns:
        Spec object with all declared resources
    """
    if path is None:
        path = Path.cwd() / DEFAULT_SPEC_FILE
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    return parse_spec(data)


def parse_spec(data: dict) -> Spec:
    """
    Parse spec data into Spec object.

    Args:
        data: Parsed YAML data

    Returns:
        Spec object
    """
    spec = Spec(version=data.get("version", "1"))

    resources_data = data.get("resources", {})

    # Iterate over resource types (feature, endpoint, module, etc.)
    for resource_type, resources in resources_data.items():
        if not isinstance(resources, dict):
            continue

        # Iterate over resources of this type
        for resource_name, resource_attrs in resources.items():
            if resource_attrs is None:
                resource_attrs = {}

            resource = Resource(
                type=resource_type,
                name=resource_name,
                status=ResourceStatus.MISSING,  # Spec declares desired, not current
                attributes=resource_attrs.get("attributes", {}),
                depends_on=resource_attrs.get("depends_on", []),
                files=resource_attrs.get("files", []),
                tests=resource_attrs.get("tests", []),
            )

            spec.add(resource)

    return spec


def validate_spec(spec: Spec) -> list:
    """
    Validate spec for common issues.

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    # Check for circular dependencies
    for resource in spec.list():
        visited = set()
        if _has_circular_dep(spec, resource.address, visited):
            errors.append(f"Circular dependency detected: {resource.address}")

    # Check for missing dependencies
    for resource in spec.list():
        for dep in resource.depends_on:
            if spec.get(dep) is None:
                errors.append(
                    f"Missing dependency: {resource.address} depends on {dep}"
                )

    return errors


def _has_circular_dep(spec: Spec, address: str, visited: set) -> bool:
    """Check for circular dependencies recursively."""
    if address in visited:
        return True

    visited.add(address)
    resource = spec.get(address)
    if resource is None:
        return False

    for dep in resource.depends_on:
        if _has_circular_dep(spec, dep, visited.copy()):
            return True

    return False


def create_example_spec(path: Union[str, Path] = None) -> Path:
    """
    Create an example spec file.

    Args:
        path: Where to create the file. Defaults to terra4mice.spec.yaml

    Returns:
        Path to created file
    """
    if path is None:
        path = Path.cwd() / DEFAULT_SPEC_FILE
    else:
        path = Path(path)

    example = """# terra4mice Spec File
# Define your desired state here

version: "1"

resources:
  # Features are high-level capabilities
  feature:
    auth_login:
      attributes:
        description: "User login with email/password"
        endpoints: [POST /auth/login]
        tests: [unit, integration]
      depends_on: []

    auth_refresh:
      attributes:
        description: "Refresh JWT tokens"
        endpoints: [POST /auth/refresh]
      depends_on:
        - feature.auth_login

    auth_logout:
      attributes:
        description: "User logout and token revocation"
        endpoints: [POST /auth/logout]
      depends_on:
        - feature.auth_login

  # Endpoints are API routes
  endpoint:
    api_users_list:
      attributes:
        method: GET
        path: /api/users
        auth: required
      depends_on:
        - feature.auth_login

  # Modules are internal components
  module:
    payment_processor:
      attributes:
        provider: x402
        tokens: [usdc, eurc]
      depends_on: []
"""

    with open(path, 'w', encoding='utf-8') as f:
        f.write(example)

    return path


def load_spec_from_obsidian(vault_path, subfolder="terra4mice"):
    """
    Load spec from an Obsidian vault.

    Reads Markdown notes with terra4mice frontmatter and constructs
    a Spec from them. Enables using Obsidian as the spec source.

    Args:
        vault_path: Path to Obsidian vault root
        subfolder: Subfolder within vault for terra4mice notes

    Returns:
        Spec object with resources from vault notes
    """
    base_path = Path(vault_path) / subfolder
    if not base_path.exists():
        raise FileNotFoundError(f"Obsidian vault subfolder not found: {base_path}")

    spec = Spec()

    # First pass: collect all resources to build known_addresses
    notes = []
    for md_path in sorted(base_path.rglob("*.md")):
        rel = md_path.relative_to(base_path)
        if any(part.startswith("_") for part in rel.parts):
            continue

        fm = _parse_obsidian_frontmatter(md_path)
        if fm is None:
            continue

        # Must have terra4mice_spec: true or terra4mice: true
        if not (fm.get("terra4mice_spec", False) or fm.get("terra4mice", False)):
            continue

        notes.append((md_path, rel, fm))

    # Build addresses for wikilink resolution
    known_addresses = set()
    for md_path, rel, fm in notes:
        rtype = fm.get("type")
        if not rtype and len(rel.parts) >= 2:
            rtype = rel.parts[0]
        if not rtype:
            rtype = "feature"

        rname = fm.get("name", md_path.stem)
        known_addresses.add(f"{rtype}.{rname}")

    # Second pass: build resources with dependencies
    for md_path, rel, fm in notes:
        rtype = fm.get("type")
        if not rtype and len(rel.parts) >= 2:
            rtype = rel.parts[0]
        if not rtype:
            rtype = "feature"

        rname = fm.get("name", md_path.stem)

        # Dependencies from frontmatter
        depends_on = list(fm.get("depends_on", []))

        # Dependencies from wikilinks in body
        body = _read_obsidian_body(md_path)
        wikilink_deps = _extract_wikilink_dependencies(body, known_addresses)
        for dep in wikilink_deps:
            if dep not in depends_on:
                depends_on.append(dep)

        resource = Resource(
            type=rtype,
            name=rname,
            status=ResourceStatus.MISSING,  # Spec = desired state
            attributes=fm.get("attributes", {}),
            depends_on=depends_on,
            files=fm.get("files", []),
            tests=fm.get("tests", []),
        )

        spec.add(resource)

    return spec


def _parse_obsidian_frontmatter(path):
    """Parse YAML frontmatter from a Markdown file."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None

    end = text.find("---", 3)
    if end == -1:
        return None

    yaml_str = text[3:end].strip()
    if not yaml_str:
        return {}

    try:
        return yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return None


def _read_obsidian_body(path):
    """Read everything below the frontmatter closing ---."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    if not text.startswith("---"):
        return text

    end = text.find("---", 3)
    if end == -1:
        return ""

    body = text[end + 3:]
    return body.lstrip("\n")


def _extract_wikilink_dependencies(body, known_addresses):
    """
    Extract dependencies from wikilinks in note body.

    Wikilinks like [[feature/auth]] are converted to feature.auth
    and included only if they match a known resource address.
    """
    if not body:
        return []

    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    matches = re.findall(pattern, body)

    deps = []
    seen = set()
    for match in matches:
        # Convert path-style to address-style: feature/auth -> feature.auth
        address = match.replace("/", ".")
        if address in known_addresses and address not in seen:
            deps.append(address)
            seen.add(address)

    return deps
