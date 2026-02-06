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

import yaml
from pathlib import Path
from typing import Union

from .models import Spec, Resource, ResourceStatus


DEFAULT_SPEC_FILE = "terra4mice.spec.yaml"


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
