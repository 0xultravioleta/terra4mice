"""
State Backends - Pluggable storage for terra4mice state.

Supports local filesystem (default) and S3 with optional DynamoDB locking.
Similar to Terraform's backend system.

Usage:
    # Local (default)
    backend = LocalBackend(Path("terra4mice.state.json"))

    # S3 with locking
    backend = S3Backend(
        bucket="my-terra4mice-state",
        key="projects/myapp/terra4mice.state.json",
        region="us-east-1",
        lock_table="terra4mice-locks",
    )

    # Factory from spec config
    backend = create_backend(backend_config)
"""

import abc
import getpass
import json
import platform
import re
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class LockInfo:
    """Information about a state lock."""

    lock_id: str = ""
    who: str = ""
    created: str = ""
    info: str = ""

    def __post_init__(self):
        if not self.lock_id:
            self.lock_id = str(uuid.uuid4())
        if not self.who:
            try:
                user = getpass.getuser()
            except Exception:
                user = "unknown"
            self.who = f"{user}@{platform.node()}"
        if not self.created:
            self.created = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "lock_id": self.lock_id,
            "who": self.who,
            "created": self.created,
            "info": self.info,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LockInfo":
        return cls(
            lock_id=data.get("lock_id", ""),
            who=data.get("who", ""),
            created=data.get("created", ""),
            info=data.get("info", ""),
        )


class StateLockError(Exception):
    """Raised when state is locked by another process."""

    def __init__(self, lock_info: LockInfo):
        self.lock_info = lock_info
        super().__init__(
            f"Error acquiring state lock: state is locked by {lock_info.who} "
            f"(lock ID: {lock_info.lock_id}, since: {lock_info.created})\n"
            f"Use 'terra4mice force-unlock {lock_info.lock_id}' to force release."
        )


class StateBackend(abc.ABC):
    """Abstract base class for state storage backends."""

    @abc.abstractmethod
    def read(self) -> Optional[bytes]:
        """Read state data. Returns None if state doesn't exist."""

    @abc.abstractmethod
    def write(self, data: bytes) -> None:
        """Write state data."""

    @abc.abstractmethod
    def exists(self) -> bool:
        """Check if state exists."""

    def lock(self, info: str = "") -> LockInfo:
        """Acquire a lock. Default: no-op (returns dummy LockInfo)."""
        return LockInfo(info=info)

    def unlock(self, lock_id: str) -> None:
        """Release a lock. Default: no-op."""

    def force_unlock(self, lock_id: str) -> None:
        """Force-release a lock. Default: no-op."""

    @property
    def supports_locking(self) -> bool:
        return False

    @property
    def backend_type(self) -> str:
        return "abstract"


class LocalBackend(StateBackend):
    """Local filesystem backend. Default behavior, no locking."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> Optional[bytes]:
        if not self.path.exists():
            return None
        return self.path.read_bytes()

    def write(self, data: bytes) -> None:
        self.path.write_bytes(data)

    def exists(self) -> bool:
        return self.path.exists()

    @property
    def backend_type(self) -> str:
        return "local"


class S3Backend(StateBackend):
    """
    AWS S3 backend with optional DynamoDB locking.

    Requires boto3 (install with: pip install terra4mice[remote])
    """

    def __init__(
        self,
        bucket: str,
        key: str,
        region: str = "us-east-1",
        lock_table: Optional[str] = None,
        profile: Optional[str] = None,
        encrypt: bool = False,
    ):
        self.bucket = bucket
        self.key = key
        self.region = region
        self.lock_table = lock_table
        self.profile = profile
        self.encrypt = encrypt

        # Lazy import boto3
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 backend. "
                "Install with: pip install terra4mice[remote]"
            )

        session_kwargs = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        session = boto3.Session(**session_kwargs)
        self._s3 = session.client("s3")

        if lock_table:
            self._dynamodb = session.client("dynamodb")
        else:
            self._dynamodb = None

    def read(self) -> Optional[bytes]:
        try:
            response = self._s3.get_object(Bucket=self.bucket, Key=self.key)
            return response["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return None

    def write(self, data: bytes) -> None:
        put_kwargs = {
            "Bucket": self.bucket,
            "Key": self.key,
            "Body": data,
            "ContentType": "application/json",
        }
        if self.encrypt:
            put_kwargs["ServerSideEncryption"] = "AES256"
        self._s3.put_object(**put_kwargs)

    def exists(self) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=self.key)
            return True
        except Exception:
            return False

    def lock(self, info: str = "") -> LockInfo:
        if not self._dynamodb:
            warnings.warn(
                "No lock_table configured for S3 backend. "
                "State locking is disabled. Configure lock_table in backend config.",
                stacklevel=2,
            )
            return LockInfo(info=info)

        lock_info = LockInfo(info=info)
        lock_key = f"{self.bucket}/{self.key}"

        try:
            self._dynamodb.put_item(
                TableName=self.lock_table,
                Item={
                    "LockID": {"S": lock_key},
                    "Info": {"S": json.dumps(lock_info.to_dict())},
                },
                ConditionExpression="attribute_not_exists(LockID)",
            )
        except self._dynamodb.exceptions.ConditionalCheckFailedException:
            # Lock already exists - read who has it
            existing = self._read_lock()
            if existing:
                raise StateLockError(existing)
            raise StateLockError(LockInfo(who="unknown"))

        return lock_info

    def unlock(self, lock_id: str) -> None:
        if not self._dynamodb:
            return

        lock_key = f"{self.bucket}/{self.key}"

        try:
            # Only delete if the lock_id matches (prevent unlocking someone else's lock)
            self._dynamodb.delete_item(
                TableName=self.lock_table,
                Key={"LockID": {"S": lock_key}},
                ConditionExpression="contains(Info, :lid)",
                ExpressionAttributeValues={":lid": {"S": lock_id}},
            )
        except self._dynamodb.exceptions.ConditionalCheckFailedException:
            warnings.warn(
                f"Lock ID {lock_id} does not match current lock. Lock may have been released.",
                stacklevel=2,
            )

    def force_unlock(self, lock_id: str) -> None:
        if not self._dynamodb:
            return

        lock_key = f"{self.bucket}/{self.key}"
        self._dynamodb.delete_item(
            TableName=self.lock_table,
            Key={"LockID": {"S": lock_key}},
        )

    def _read_lock(self) -> Optional[LockInfo]:
        """Read existing lock info from DynamoDB."""
        if not self._dynamodb:
            return None

        lock_key = f"{self.bucket}/{self.key}"
        try:
            response = self._dynamodb.get_item(
                TableName=self.lock_table,
                Key={"LockID": {"S": lock_key}},
            )
            item = response.get("Item")
            if not item:
                return None
            info_str = item.get("Info", {}).get("S", "{}")
            return LockInfo.from_dict(json.loads(info_str))
        except Exception:
            return None

    @property
    def supports_locking(self) -> bool:
        return self._dynamodb is not None

    @property
    def backend_type(self) -> str:
        return "s3"


class ObsidianBackend(StateBackend):
    """
    Obsidian vault backend - stores state as interconnected Markdown notes.

    Each resource becomes a note with YAML frontmatter (machine-readable)
    and Markdown body (human notes, wikilinks). Enables Obsidian Graph View
    for dependency visualization and Dataview queries.

    Usage:
        backend = ObsidianBackend(
            vault_path="/path/to/vault",
            subfolder="terra4mice",
        )
    """

    def __init__(self, vault_path, subfolder="terra4mice"):
        self.vault_path = Path(vault_path)
        self.base_path = self.vault_path / subfolder

    def read(self):
        """Read state from vault notes, reassembling into JSON blob."""
        index_path = self.base_path / "_index.md"
        if not index_path.exists():
            return None

        # Read index for version/serial
        index_fm = self._read_frontmatter(index_path)
        if index_fm is None:
            return None

        state_dict = {
            "version": index_fm.get("version", "1"),
            "serial": index_fm.get("serial", 0),
            "resources": [],
        }

        # Walk subdirectories for resource notes
        for md_path in sorted(self.base_path.rglob("*.md")):
            # Skip _-prefixed files and dirs
            rel = md_path.relative_to(self.base_path)
            if any(part.startswith("_") for part in rel.parts):
                continue

            fm = self._read_frontmatter(md_path)
            if fm is None or not fm.get("terra4mice", False):
                continue

            resource_data = {
                "type": fm.get("type", ""),
                "name": fm.get("name", ""),
                "status": fm.get("status", "missing"),
                "locked": fm.get("locked", False),
                "source": fm.get("source", "auto"),
                "attributes": fm.get("attributes", {}),
                "depends_on": fm.get("depends_on", []),
                "files": fm.get("files", []),
                "tests": fm.get("tests", []),
            }
            state_dict["resources"].append(resource_data)

        return json.dumps(state_dict).encode("utf-8")

    def write(self, data):
        """Write state to vault as per-resource Markdown notes."""
        state_dict = json.loads(data.decode("utf-8"))

        # Ensure base directory exists
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Write _index.md
        index_fm = {
            "terra4mice_index": True,
            "version": state_dict.get("version", "1"),
            "serial": state_dict.get("serial", 0),
        }
        index_path = self.base_path / "_index.md"
        self._write_note(index_path, index_fm, self._index_body(state_dict))

        # Track which resource files we write (for cleanup)
        written_paths = set()

        # Write per-resource notes
        for res in state_dict.get("resources", []):
            rtype = res.get("type", "unknown")
            rname = res.get("name", "unnamed")

            type_dir = self.base_path / rtype
            type_dir.mkdir(parents=True, exist_ok=True)

            note_path = type_dir / f"{rname}.md"
            written_paths.add(note_path)

            frontmatter = {
                "terra4mice": True,
                "type": rtype,
                "name": rname,
                "status": res.get("status", "missing"),
            }
            # Only add optional fields if present
            if res.get("locked"):
                frontmatter["locked"] = True
            if res.get("source", "auto") != "auto":
                frontmatter["source"] = res["source"]
            if res.get("attributes"):
                frontmatter["attributes"] = res["attributes"]
            if res.get("depends_on"):
                frontmatter["depends_on"] = res["depends_on"]
            if res.get("files"):
                frontmatter["files"] = res["files"]
            if res.get("tests"):
                frontmatter["tests"] = res["tests"]

            default_body = self._resource_default_body(res)
            self._write_note(note_path, frontmatter, default_body)

        # Clean up removed resources (only managed notes)
        for md_path in list(self.base_path.rglob("*.md")):
            rel = md_path.relative_to(self.base_path)
            if any(part.startswith("_") for part in rel.parts):
                continue
            if md_path in written_paths:
                continue

            fm = self._read_frontmatter(md_path)
            if fm and fm.get("terra4mice", False):
                md_path.unlink()

                # Clean empty parent dirs (but not base_path)
                parent = md_path.parent
                if parent != self.base_path:
                    try:
                        parent.rmdir()  # Only removes if empty
                    except OSError:
                        pass

    def exists(self):
        """Check if state exists in vault."""
        return (self.base_path / "_index.md").exists()

    @property
    def backend_type(self):
        return "obsidian"

    @property
    def supports_locking(self):
        return False

    # --- Internal helpers ---

    def _read_frontmatter(self, path):
        """Parse YAML frontmatter from a Markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        if not text.startswith("---"):
            return None

        # Find closing ---
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

    def _read_body(self, path):
        """Read everything below the frontmatter closing ---."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

        if not text.startswith("---"):
            return text

        end = text.find("---", 3)
        if end == -1:
            return ""

        # Skip the closing --- and any leading newlines
        body = text[end + 3:]
        return body.lstrip("\n")

    def _write_note(self, path, frontmatter, default_body=""):
        """Write a note preserving existing body content."""
        # If file exists, preserve the body
        if path.exists():
            existing_body = self._read_body(path)
            body = existing_body if existing_body else default_body
        else:
            body = default_body

        yaml_str = yaml.dump(frontmatter, sort_keys=False, default_flow_style=False).strip()
        content = f"---\n{yaml_str}\n---\n\n{body}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _resource_default_body(self, resource_data):
        """Generate default body for a new resource note."""
        rtype = resource_data.get("type", "unknown")
        rname = resource_data.get("name", "unnamed")
        lines = [f"# {rtype}.{rname}"]

        desc = resource_data.get("attributes", {}).get("description", "")
        if desc:
            lines.append(f"\n{desc}")

        deps = resource_data.get("depends_on", [])
        if deps:
            lines.append("\n## Dependencies")
            for dep in deps:
                # Convert type.name to type/name for wikilink
                wikilink = dep.replace(".", "/")
                lines.append(f"- [[{wikilink}]]")

        files = resource_data.get("files", [])
        if files:
            lines.append("\n## Files")
            for f in files:
                lines.append(f"- `{f}`")

        lines.append("\n## Notes\n")

        return "\n".join(lines)

    def _index_body(self, state_dict):
        """Generate body for the _index.md file."""
        resources = state_dict.get("resources", [])
        total = len(resources)

        # Status breakdown
        status_counts = {}
        for r in resources:
            s = r.get("status", "missing")
            status_counts[s] = status_counts.get(s, 0) + 1

        lines = ["# terra4mice State Index"]
        lines.append(f"\nTotal resources: {total}")

        if status_counts:
            lines.append("\n## Status Breakdown")
            for status, count in sorted(status_counts.items()):
                lines.append(f"- {status}: {count}")

        if resources:
            lines.append("\n## Resources")
            for r in resources:
                rtype = r.get("type", "unknown")
                rname = r.get("name", "unnamed")
                status = r.get("status", "missing")
                lines.append(f"- [[{rtype}/{rname}]] ({status})")

        return "\n".join(lines)


def create_backend(
    backend_config: Optional[dict] = None,
    path: Optional[Path] = None,
) -> StateBackend:
    """
    Create the appropriate backend.

    Priority: explicit path > backend config > default local.

    Args:
        backend_config: Backend configuration from spec (backend: section)
        path: Explicit path override (from --state flag)

    Returns:
        StateBackend instance
    """
    if path is not None:
        return LocalBackend(Path(path))

    if backend_config is None:
        return LocalBackend(Path.cwd() / "terra4mice.state.json")

    backend_type = backend_config.get("type", "local")

    if backend_type == "local":
        config = backend_config.get("config", {})
        local_path = config.get("path", "terra4mice.state.json")
        return LocalBackend(Path(local_path))

    if backend_type == "s3":
        config = backend_config.get("config", {})
        required = ["bucket", "key"]
        missing = [k for k in required if k not in config]
        if missing:
            raise ValueError(
                f"S3 backend config missing required fields: {', '.join(missing)}"
            )
        return S3Backend(
            bucket=config["bucket"],
            key=config["key"],
            region=config.get("region", "us-east-1"),
            lock_table=config.get("lock_table"),
            profile=config.get("profile"),
            encrypt=config.get("encrypt", False),
        )

    if backend_type == "obsidian":
        config = backend_config.get("config", {})
        required = ["vault_path"]
        missing = [k for k in required if k not in config]
        if missing:
            raise ValueError(
                f"Obsidian backend config missing required fields: {', '.join(missing)}"
            )
        return ObsidianBackend(
            vault_path=config["vault_path"],
            subfolder=config.get("subfolder", "terra4mice"),
        )

    raise ValueError(f"Unknown backend type: {backend_type}")
