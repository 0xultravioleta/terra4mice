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
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


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

    raise ValueError(f"Unknown backend type: {backend_type}")
