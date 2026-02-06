"""
Core data models for terra4mice.

Inspired by Terraform's resource model:
- Resource: A unit of functionality (feature, endpoint, module)
- State: Current status of all resources
- Spec: Desired state of all resources
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime


@dataclass
class SymbolStatus:
    """Status of an individual symbol (function/class/method) within a resource."""

    name: str
    kind: str  # "function", "class", "method", "interface", "type", "enum"
    status: str = "implemented"  # "implemented" | "missing"
    line_start: int = 0
    line_end: int = 0
    parent: str = ""  # "ClassName" for methods
    file: str = ""

    @property
    def qualified_name(self) -> str:
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name


class ResourceStatus(Enum):
    """Status of a resource in the state."""
    MISSING = "missing"           # No existe código
    PARTIAL = "partial"           # Existe pero incompleto
    IMPLEMENTED = "implemented"   # Funciona y tiene tests
    BROKEN = "broken"             # Existía pero ahora falla
    DEPRECATED = "deprecated"     # Marcado para remover


@dataclass
class Resource:
    """
    A resource is a unit of functionality that can be tracked.

    Similar to Terraform resources, but for code instead of infrastructure.

    Examples:
    - resource "feature" "auth_login" { ... }
    - resource "endpoint" "api_users" { ... }
    - resource "module" "payment_processor" { ... }
    """
    type: str                     # feature, endpoint, module, test, etc.
    name: str                     # Unique name within type
    status: ResourceStatus = ResourceStatus.MISSING

    # Spec attributes (desired state)
    attributes: Dict[str, Any] = field(default_factory=dict)

    # Dependencies
    depends_on: List[str] = field(default_factory=list)  # ["feature.auth_login"]

    # Metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Evidence of implementation
    files: List[str] = field(default_factory=list)       # Files that implement this
    tests: List[str] = field(default_factory=list)       # Tests that cover this

    # Symbol-level tracking (functions, classes, methods)
    symbols: Dict[str, 'SymbolStatus'] = field(default_factory=dict)

    @property
    def address(self) -> str:
        """Terraform-style resource address: type.name"""
        return f"{self.type}.{self.name}"

    def __str__(self) -> str:
        return f"{self.address} ({self.status.value})"


@dataclass
class State:
    """
    The state file - tracks what resources exist.

    Equivalent to terraform.tfstate
    """
    version: str = "1"
    serial: int = 0
    resources: Dict[str, Resource] = field(default_factory=dict)

    # Metadata
    last_updated: Optional[datetime] = None

    def get(self, address: str) -> Optional[Resource]:
        """Get resource by address (type.name)"""
        return self.resources.get(address)

    def set(self, resource: Resource) -> None:
        """Add or update a resource in state"""
        resource.updated_at = datetime.now()
        if resource.address not in self.resources:
            resource.created_at = datetime.now()
        self.resources[resource.address] = resource
        self.serial += 1
        self.last_updated = datetime.now()

    def remove(self, address: str) -> Optional[Resource]:
        """Remove a resource from state"""
        if address in self.resources:
            resource = self.resources.pop(address)
            self.serial += 1
            self.last_updated = datetime.now()
            return resource
        return None

    def list(self, type_filter: Optional[str] = None) -> List[Resource]:
        """List all resources, optionally filtered by type"""
        resources = list(self.resources.values())
        if type_filter:
            resources = [r for r in resources if r.type == type_filter]
        return sorted(resources, key=lambda r: r.address)

    def list_by_status(self, status: ResourceStatus) -> List[Resource]:
        """List resources by status"""
        return [r for r in self.resources.values() if r.status == status]


@dataclass
class Spec:
    """
    The spec file - defines desired state.

    Equivalent to Terraform .tf files
    """
    version: str = "1"
    resources: Dict[str, Resource] = field(default_factory=dict)

    def get(self, address: str) -> Optional[Resource]:
        """Get resource by address"""
        return self.resources.get(address)

    def add(self, resource: Resource) -> None:
        """Add a resource to spec"""
        self.resources[resource.address] = resource

    def list(self, type_filter: Optional[str] = None) -> List[Resource]:
        """List all resources in spec"""
        resources = list(self.resources.values())
        if type_filter:
            resources = [r for r in resources if r.type == type_filter]
        return sorted(resources, key=lambda r: r.address)


@dataclass
class PlanAction:
    """A single action in a plan."""
    action: str  # create, update, delete, no-op
    resource: Resource
    reason: str = ""

    @property
    def symbol(self) -> str:
        """Terraform-style action symbol"""
        symbols = {
            "create": "+",
            "update": "~",
            "delete": "-",
            "no-op": " ",
        }
        return symbols.get(self.action, "?")

    def __str__(self) -> str:
        return f"  {self.symbol} {self.resource.address}"


@dataclass
class Plan:
    """
    A plan shows what needs to change.

    Equivalent to terraform plan output
    """
    actions: List[PlanAction] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(a.action != "no-op" for a in self.actions)

    @property
    def creates(self) -> List[PlanAction]:
        return [a for a in self.actions if a.action == "create"]

    @property
    def updates(self) -> List[PlanAction]:
        return [a for a in self.actions if a.action == "update"]

    @property
    def deletes(self) -> List[PlanAction]:
        return [a for a in self.actions if a.action == "delete"]

    def summary(self) -> str:
        """Human-readable summary"""
        if not self.has_changes:
            return "No changes. State matches spec."

        parts = []
        if self.creates:
            parts.append(f"{len(self.creates)} to create")
        if self.updates:
            parts.append(f"{len(self.updates)} to update")
        if self.deletes:
            parts.append(f"{len(self.deletes)} to delete")

        return f"Plan: {', '.join(parts)}."
