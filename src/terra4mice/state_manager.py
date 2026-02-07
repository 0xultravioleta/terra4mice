"""
State Manager - Track what resources exist in your codebase.

The state file (terra4mice.state.json) tracks:
- Which resources have been created (implemented)
- Their current status
- Evidence (files, tests)
- Timestamps

Similar to terraform.tfstate
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Union, Optional, List

from .models import State, Resource, ResourceStatus, SymbolStatus
from .backends import StateBackend, LocalBackend, StateLockError


DEFAULT_STATE_FILE = "terra4mice.state.json"


class StateManager:
    """
    Manages the terra4mice state file.

    Usage:
        sm = StateManager()
        sm.load()
        sm.mark_created("feature.auth_login", files=["src/auth.py"])
        sm.save()

    With remote backend + locking:
        sm = StateManager(backend=s3_backend)
        with sm:  # auto-lock + load
            sm.mark_created("feature.auth_login", files=["src/auth.py"])
            sm.save()
        # auto-unlock on exit
    """

    def __init__(self, path: Union[str, Path] = None, backend: StateBackend = None):
        """
        Initialize state manager.

        Args:
            path: Path to state file. Defaults to terra4mice.state.json
            backend: StateBackend instance. If provided, path is ignored.
        """
        if backend is not None:
            self.backend = backend
        elif path is not None:
            self.backend = LocalBackend(Path(path))
        else:
            self.backend = LocalBackend(Path.cwd() / DEFAULT_STATE_FILE)

        # Keep self.path for backward compat (used by tests and other code)
        if isinstance(self.backend, LocalBackend):
            self.path = self.backend.path
        else:
            self.path = None

        self.state = State()
        self._lock_info = None

    def load(self) -> State:
        """
        Load state from backend.

        Returns:
            State object (empty if state doesn't exist)
        """
        raw = self.backend.read()
        if raw is None:
            self.state = State()
            return self.state

        data = json.loads(raw)
        self.state = self._parse_state(data)
        return self.state

    def save(self) -> None:
        """Save state to backend."""
        data = self._serialize_state(self.state)
        raw = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.backend.write(raw)

    def __enter__(self):
        """Context manager: acquire lock and load state."""
        if self.backend.supports_locking:
            self._lock_info = self.backend.lock()
        self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: release lock."""
        if self._lock_info:
            self.backend.unlock(self._lock_info.lock_id)
            self._lock_info = None
        return False

    def list(self, type_filter: Optional[str] = None) -> List[Resource]:
        """
        List all resources in state.

        Equivalent to: terraform state list

        Args:
            type_filter: Optional filter by resource type

        Returns:
            List of resources
        """
        return self.state.list(type_filter)

    def show(self, address: str) -> Optional[Resource]:
        """
        Show details of a specific resource.

        Equivalent to: terraform state show <address>

        Args:
            address: Resource address (type.name)

        Returns:
            Resource or None if not found
        """
        return self.state.get(address)

    def mark_created(
        self,
        address: str,
        files: List[str] = None,
        tests: List[str] = None,
        attributes: dict = None,
        lock: bool = False,
    ) -> Resource:
        """
        Mark a resource as created (implemented).

        This is called when you've implemented something and want to
        record it in the state.

        Args:
            address: Resource address (type.name)
            files: Files that implement this resource
            tests: Tests that cover this resource
            attributes: Additional attributes
            lock: If True, lock resource to prevent refresh overwrite

        Returns:
            The created/updated resource
        """
        resource_type, resource_name = address.split(".", 1)

        existing = self.state.get(address)
        if existing:
            resource = existing
            resource.status = ResourceStatus.IMPLEMENTED
        else:
            resource = Resource(
                type=resource_type,
                name=resource_name,
                status=ResourceStatus.IMPLEMENTED,
            )

        resource.source = "manual"
        if lock:
            resource.locked = True

        if files:
            resource.files = files
        if tests:
            resource.tests = tests
        if attributes:
            resource.attributes.update(attributes)

        self.state.set(resource)
        return resource

    def mark_partial(self, address: str, reason: str = "", lock: bool = False) -> Resource:
        """
        Mark a resource as partially implemented.

        Args:
            address: Resource address
            reason: Why it's partial
            lock: If True, lock resource to prevent refresh overwrite

        Returns:
            Updated resource
        """
        resource = self.state.get(address)
        if resource is None:
            resource_type, resource_name = address.split(".", 1)
            resource = Resource(type=resource_type, name=resource_name)

        resource.status = ResourceStatus.PARTIAL
        resource.source = "manual"
        if lock:
            resource.locked = True
        if reason:
            resource.attributes["partial_reason"] = reason

        self.state.set(resource)
        return resource

    def mark_broken(self, address: str, reason: str = "", lock: bool = False) -> Resource:
        """
        Mark a resource as broken.

        Args:
            address: Resource address
            reason: Why it's broken
            lock: If True, lock resource to prevent refresh overwrite

        Returns:
            Updated resource
        """
        resource = self.state.get(address)
        if resource is None:
            resource_type, resource_name = address.split(".", 1)
            resource = Resource(type=resource_type, name=resource_name)

        resource.status = ResourceStatus.BROKEN
        resource.source = "manual"
        if lock:
            resource.locked = True
        if reason:
            resource.attributes["broken_reason"] = reason

        self.state.set(resource)
        return resource

    def mark_locked(self, address: str, locked: bool = True) -> Optional[Resource]:
        """
        Lock or unlock a resource in state.

        Locked resources are not overwritten by refresh.

        Args:
            address: Resource address (type.name)
            locked: True to lock, False to unlock

        Returns:
            Updated resource or None if not found
        """
        resource = self.state.get(address)
        if resource is None:
            return None

        resource.locked = locked
        if locked:
            resource.source = "manual"
        self.state.set(resource)
        return resource

    def remove(self, address: str) -> Optional[Resource]:
        """
        Remove a resource from state.

        Equivalent to: terraform state rm <address>

        Args:
            address: Resource address

        Returns:
            Removed resource or None
        """
        return self.state.remove(address)

    def _parse_state(self, data: dict) -> State:
        """Parse JSON data into State object."""
        state = State(
            version=data.get("version", "1"),
            serial=data.get("serial", 0),
        )

        if "last_updated" in data and data["last_updated"]:
            state.last_updated = datetime.fromisoformat(data["last_updated"])

        for resource_data in data.get("resources", []):
            resource = Resource(
                type=resource_data["type"],
                name=resource_data["name"],
                status=ResourceStatus(resource_data.get("status", "missing")),
                locked=resource_data.get("locked", False),
                source=resource_data.get("source", "auto"),
                attributes=resource_data.get("attributes", {}),
                depends_on=resource_data.get("depends_on", []),
                files=resource_data.get("files", []),
                tests=resource_data.get("tests", []),
            )

            if resource_data.get("created_at"):
                resource.created_at = datetime.fromisoformat(resource_data["created_at"])
            if resource_data.get("updated_at"):
                resource.updated_at = datetime.fromisoformat(resource_data["updated_at"])

            # Parse symbols (backward-compatible: missing = empty dict)
            for qname, sym_data in resource_data.get("symbols", {}).items():
                resource.symbols[qname] = SymbolStatus(
                    name=sym_data["name"],
                    kind=sym_data["kind"],
                    status=sym_data.get("status", "implemented"),
                    line_start=sym_data.get("line_start", 0),
                    line_end=sym_data.get("line_end", 0),
                    parent=sym_data.get("parent", ""),
                    file=sym_data.get("file", ""),
                )

            state.set(resource)

        return state

    def _serialize_state(self, state: State) -> dict:
        """Serialize State to JSON-compatible dict."""
        resources = []
        for resource in state.list():
            entry = {
                "type": resource.type,
                "name": resource.name,
                "status": resource.status.value,
                "locked": resource.locked,
                "source": resource.source,
                "attributes": resource.attributes,
                "depends_on": resource.depends_on,
                "files": resource.files,
                "tests": resource.tests,
                "created_at": resource.created_at.isoformat() if resource.created_at else None,
                "updated_at": resource.updated_at.isoformat() if resource.updated_at else None,
            }
            if resource.symbols:
                entry["symbols"] = {
                    qname: {
                        "name": sym.name,
                        "kind": sym.kind,
                        "status": sym.status,
                        "line_start": sym.line_start,
                        "line_end": sym.line_end,
                        "parent": sym.parent,
                        "file": sym.file,
                    }
                    for qname, sym in resource.symbols.items()
                }
            resources.append(entry)

        return {
            "version": state.version,
            "serial": state.serial,
            "last_updated": state.last_updated.isoformat() if state.last_updated else None,
            "resources": resources,
        }
