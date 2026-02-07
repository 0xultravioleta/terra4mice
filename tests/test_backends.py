"""
Tests for terra4mice backends (local + S3 with mocked boto3).

Covers:
- LockInfo creation and serialization
- StateLockError message formatting
- LocalBackend read/write/exists
- S3Backend read/write/exists/lock/unlock/force_unlock (mocked)
- S3Backend without lock_table (no-op locking)
- S3Backend missing boto3
- create_backend factory
- StateManager with backend (context manager, locking)
- CLI state pull/push integration
"""

import json
import tempfile
import warnings
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from terra4mice.backends import (
    LockInfo,
    StateLockError,
    StateBackend,
    LocalBackend,
    S3Backend,
    create_backend,
)
from terra4mice.state_manager import StateManager
from terra4mice.models import State, Resource, ResourceStatus


# ---------------------------------------------------------------------------
# TestLockInfo
# ---------------------------------------------------------------------------

class TestLockInfo:
    def test_auto_fills_defaults(self):
        info = LockInfo()
        assert info.lock_id  # UUID generated
        assert "@" in info.who  # user@host
        assert info.created  # ISO timestamp
        assert info.info == ""

    def test_explicit_values(self):
        info = LockInfo(lock_id="abc-123", who="alice@laptop", created="2026-01-01T00:00:00+00:00", info="refresh")
        assert info.lock_id == "abc-123"
        assert info.who == "alice@laptop"
        assert info.info == "refresh"

    def test_to_dict_from_dict_roundtrip(self):
        original = LockInfo(info="terra4mice refresh")
        data = original.to_dict()
        restored = LockInfo.from_dict(data)
        assert restored.lock_id == original.lock_id
        assert restored.who == original.who
        assert restored.created == original.created
        assert restored.info == original.info

    def test_to_dict_keys(self):
        info = LockInfo()
        d = info.to_dict()
        assert set(d.keys()) == {"lock_id", "who", "created", "info"}

    def test_from_dict_missing_keys(self):
        info = LockInfo.from_dict({})
        # Should not crash - __post_init__ auto-fills empty strings
        assert info.lock_id  # UUID auto-generated from empty string
        assert "@" in info.who  # user@host auto-generated


# ---------------------------------------------------------------------------
# TestStateLockError
# ---------------------------------------------------------------------------

class TestStateLockError:
    def test_message_includes_holder_info(self):
        lock = LockInfo(lock_id="lock-xyz", who="bob@server")
        err = StateLockError(lock)
        msg = str(err)
        assert "bob@server" in msg
        assert "lock-xyz" in msg
        assert "force-unlock" in msg

    def test_lock_info_attribute(self):
        lock = LockInfo(lock_id="abc")
        err = StateLockError(lock)
        assert err.lock_info.lock_id == "abc"


# ---------------------------------------------------------------------------
# TestLocalBackend
# ---------------------------------------------------------------------------

class TestLocalBackend:
    def test_read_nonexistent(self, tmp_path):
        backend = LocalBackend(tmp_path / "nope.json")
        assert backend.read() is None

    def test_write_then_read(self, tmp_path):
        p = tmp_path / "state.json"
        backend = LocalBackend(p)
        data = b'{"version": "1", "resources": []}'
        backend.write(data)
        assert backend.read() == data

    def test_exists_false(self, tmp_path):
        backend = LocalBackend(tmp_path / "nope.json")
        assert backend.exists() is False

    def test_exists_true(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_bytes(b"{}")
        backend = LocalBackend(p)
        assert backend.exists() is True

    def test_backend_type(self, tmp_path):
        backend = LocalBackend(tmp_path / "state.json")
        assert backend.backend_type == "local"

    def test_supports_locking_false(self, tmp_path):
        backend = LocalBackend(tmp_path / "state.json")
        assert backend.supports_locking is False

    def test_lock_noop(self, tmp_path):
        backend = LocalBackend(tmp_path / "state.json")
        info = backend.lock("test")
        assert info.info == "test"

    def test_unlock_noop(self, tmp_path):
        backend = LocalBackend(tmp_path / "state.json")
        backend.unlock("any-id")  # Should not raise


# ---------------------------------------------------------------------------
# TestS3Backend (mocked boto3)
# ---------------------------------------------------------------------------

def _make_mock_boto3():
    """Create a mock boto3 module."""
    mock_boto3 = MagicMock()
    mock_session = MagicMock()
    mock_boto3.Session.return_value = mock_session

    mock_s3 = MagicMock()
    mock_dynamodb = MagicMock()
    mock_session.client.side_effect = lambda svc, **kw: mock_s3 if svc == "s3" else mock_dynamodb

    return mock_boto3, mock_s3, mock_dynamodb


class TestS3Backend:
    @patch.dict("sys.modules", {"boto3": _make_mock_boto3()[0]})
    def _make_backend(self, lock_table="terra4mice-locks", **kwargs):
        mock_boto3, mock_s3, mock_dynamodb = _make_mock_boto3()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            backend = S3Backend(
                bucket="test-bucket",
                key="state/terra4mice.state.json",
                region="us-east-1",
                lock_table=lock_table,
                **kwargs,
            )
        backend._mock_s3 = mock_s3
        backend._mock_dynamodb = mock_dynamodb
        return backend

    def test_backend_type(self):
        backend = self._make_backend()
        assert backend.backend_type == "s3"

    def test_supports_locking_with_table(self):
        backend = self._make_backend(lock_table="my-table")
        assert backend.supports_locking is True

    def test_supports_locking_without_table(self):
        backend = self._make_backend(lock_table=None)
        assert backend.supports_locking is False

    def test_read_returns_bytes(self):
        backend = self._make_backend()
        body_mock = MagicMock()
        body_mock.read.return_value = b'{"version": "1"}'
        backend._s3.get_object.return_value = {"Body": body_mock}

        result = backend.read()
        assert result == b'{"version": "1"}'
        backend._s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="state/terra4mice.state.json"
        )

    def test_read_returns_none_on_no_such_key(self):
        backend = self._make_backend()
        exc = type("NoSuchKey", (Exception,), {})
        backend._s3.exceptions.NoSuchKey = exc
        backend._s3.get_object.side_effect = exc("not found")

        result = backend.read()
        assert result is None

    def test_write(self):
        backend = self._make_backend()
        data = b'{"version": "1"}'
        backend.write(data)
        backend._s3.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="state/terra4mice.state.json",
            Body=data,
            ContentType="application/json",
        )

    def test_write_with_encryption(self):
        backend = self._make_backend(encrypt=True)
        backend.write(b"{}")
        call_kwargs = backend._s3.put_object.call_args[1]
        assert call_kwargs["ServerSideEncryption"] == "AES256"

    def test_exists_true(self):
        backend = self._make_backend()
        backend._s3.head_object.return_value = {}
        assert backend.exists() is True

    def test_exists_false(self):
        backend = self._make_backend()
        backend._s3.head_object.side_effect = Exception("404")
        assert backend.exists() is False

    def test_lock_success(self):
        backend = self._make_backend()
        info = backend.lock("refresh")
        assert info.info == "refresh"
        assert info.lock_id  # UUID
        backend._dynamodb.put_item.assert_called_once()

    def test_lock_conflict_raises(self):
        backend = self._make_backend()
        exc = type("ConditionalCheckFailedException", (Exception,), {})
        backend._dynamodb.exceptions.ConditionalCheckFailedException = exc
        backend._dynamodb.put_item.side_effect = exc("conflict")

        # Mock reading existing lock
        existing_lock = LockInfo(lock_id="existing-lock", who="alice@laptop")
        backend._dynamodb.get_item.return_value = {
            "Item": {
                "LockID": {"S": "test-bucket/state/terra4mice.state.json"},
                "Info": {"S": json.dumps(existing_lock.to_dict())},
            }
        }

        with pytest.raises(StateLockError) as exc_info:
            backend.lock("refresh")

        assert "alice@laptop" in str(exc_info.value)

    def test_unlock(self):
        backend = self._make_backend()
        backend.unlock("my-lock-id")
        backend._dynamodb.delete_item.assert_called_once()
        call_kwargs = backend._dynamodb.delete_item.call_args[1]
        assert "ConditionExpression" in call_kwargs

    def test_force_unlock(self):
        backend = self._make_backend()
        backend.force_unlock("my-lock-id")
        backend._dynamodb.delete_item.assert_called_once()
        call_kwargs = backend._dynamodb.delete_item.call_args[1]
        # force_unlock should NOT have a condition expression
        assert "ConditionExpression" not in call_kwargs


# ---------------------------------------------------------------------------
# TestS3BackendNoLockTable
# ---------------------------------------------------------------------------

class TestS3BackendNoLockTable:
    def _make_backend(self):
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_s3 = MagicMock()
        mock_session.client.return_value = mock_s3

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            backend = S3Backend(
                bucket="test-bucket",
                key="state.json",
                lock_table=None,
            )
        return backend

    def test_lock_is_noop_with_warning(self):
        backend = self._make_backend()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            info = backend.lock("test")
            assert len(w) == 1
            assert "lock_table" in str(w[0].message)
        assert info.info == "test"

    def test_unlock_is_noop(self):
        backend = self._make_backend()
        backend.unlock("any-id")  # No error

    def test_force_unlock_is_noop(self):
        backend = self._make_backend()
        backend.force_unlock("any-id")  # No error


# ---------------------------------------------------------------------------
# TestS3BackendMissingBoto3
# ---------------------------------------------------------------------------

class TestS3BackendMissingBoto3:
    def test_import_error(self):
        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(ImportError, match="boto3"):
                S3Backend(bucket="b", key="k")


# ---------------------------------------------------------------------------
# TestCreateBackend
# ---------------------------------------------------------------------------

class TestCreateBackend:
    def test_default_local(self):
        backend = create_backend()
        assert isinstance(backend, LocalBackend)

    def test_explicit_path(self, tmp_path):
        p = tmp_path / "custom.json"
        backend = create_backend(path=p)
        assert isinstance(backend, LocalBackend)
        assert backend.path == p

    def test_path_overrides_config(self, tmp_path):
        p = tmp_path / "custom.json"
        config = {"type": "s3", "config": {"bucket": "b", "key": "k"}}
        backend = create_backend(backend_config=config, path=p)
        assert isinstance(backend, LocalBackend)

    def test_config_local(self):
        config = {"type": "local", "config": {"path": "my/state.json"}}
        backend = create_backend(backend_config=config)
        assert isinstance(backend, LocalBackend)
        assert str(backend.path) == "my/state.json" or backend.path == Path("my/state.json")

    def test_config_local_default(self):
        config = {"type": "local"}
        backend = create_backend(backend_config=config)
        assert isinstance(backend, LocalBackend)

    def test_config_s3(self):
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        config = {
            "type": "s3",
            "config": {
                "bucket": "my-bucket",
                "key": "state.json",
                "region": "eu-west-1",
                "lock_table": "locks",
                "encrypt": True,
            },
        }
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            backend = create_backend(backend_config=config)
        assert isinstance(backend, S3Backend)
        assert backend.bucket == "my-bucket"
        assert backend.encrypt is True

    def test_config_s3_missing_bucket(self):
        config = {"type": "s3", "config": {"key": "state.json"}}
        with pytest.raises(ValueError, match="bucket"):
            create_backend(backend_config=config)

    def test_config_s3_missing_key(self):
        config = {"type": "s3", "config": {"bucket": "b"}}
        with pytest.raises(ValueError, match="key"):
            create_backend(backend_config=config)

    def test_unknown_type(self):
        config = {"type": "gcs", "config": {}}
        with pytest.raises(ValueError, match="Unknown backend type"):
            create_backend(backend_config=config)


# ---------------------------------------------------------------------------
# TestStateManagerWithBackend
# ---------------------------------------------------------------------------

class TestStateManagerWithBackend:
    def test_load_save_via_local_backend(self, tmp_path):
        p = tmp_path / "state.json"
        backend = LocalBackend(p)
        sm = StateManager(backend=backend)

        # Save empty state
        sm.save()
        assert p.exists()

        # Load it back
        sm2 = StateManager(backend=LocalBackend(p))
        state = sm2.load()
        assert state.version == "1"
        assert len(state.list()) == 0

    def test_load_nonexistent(self, tmp_path):
        backend = LocalBackend(tmp_path / "nope.json")
        sm = StateManager(backend=backend)
        state = sm.load()
        assert len(state.list()) == 0

    def test_save_load_with_resources(self, tmp_path):
        p = tmp_path / "state.json"
        backend = LocalBackend(p)
        sm = StateManager(backend=backend)
        sm.load()
        sm.mark_created("feature.auth", files=["auth.py"])
        sm.save()

        sm2 = StateManager(backend=LocalBackend(p))
        sm2.load()
        r = sm2.show("feature.auth")
        assert r is not None
        assert r.status == ResourceStatus.IMPLEMENTED

    def test_context_manager_no_locking(self, tmp_path):
        p = tmp_path / "state.json"
        backend = LocalBackend(p)
        sm = StateManager(backend=backend)

        with sm:
            sm.mark_created("module.test")
            sm.save()

        # Verify saved
        sm2 = StateManager(backend=LocalBackend(p))
        sm2.load()
        assert sm2.show("module.test") is not None

    def test_context_manager_with_locking(self, tmp_path):
        """Test context manager acquires/releases lock on locking backend."""
        mock_backend = MagicMock(spec=StateBackend)
        mock_backend.supports_locking = True
        mock_backend.read.return_value = b'{"version": "1", "serial": 0, "resources": []}'
        lock_info = LockInfo(lock_id="test-lock")
        mock_backend.lock.return_value = lock_info

        sm = StateManager(backend=mock_backend)

        with sm:
            pass  # Just testing lock/unlock

        mock_backend.lock.assert_called_once()
        mock_backend.unlock.assert_called_once_with("test-lock")

    def test_context_manager_unlock_on_exception(self, tmp_path):
        """Lock is released even if body raises."""
        mock_backend = MagicMock(spec=StateBackend)
        mock_backend.supports_locking = True
        mock_backend.read.return_value = b'{"version": "1", "serial": 0, "resources": []}'
        lock_info = LockInfo(lock_id="test-lock")
        mock_backend.lock.return_value = lock_info

        sm = StateManager(backend=mock_backend)

        with pytest.raises(RuntimeError):
            with sm:
                raise RuntimeError("boom")

        mock_backend.unlock.assert_called_once_with("test-lock")

    def test_backward_compat_path_only(self, tmp_path):
        """Existing StateManager(path=...) still works."""
        p = tmp_path / "state.json"
        sm = StateManager(path=p)
        assert isinstance(sm.backend, LocalBackend)
        assert sm.path == p

    def test_backward_compat_no_args(self):
        """StateManager() defaults to local backend in cwd."""
        sm = StateManager()
        assert isinstance(sm.backend, LocalBackend)


# ---------------------------------------------------------------------------
# TestSpecWithBackend
# ---------------------------------------------------------------------------

class TestSpecWithBackend:
    def test_load_spec_with_backend_config(self, tmp_path):
        from terra4mice.spec_parser import load_spec_with_backend

        spec_file = tmp_path / "terra4mice.spec.yaml"
        spec_file.write_text(
            """
version: "1"

backend:
  type: s3
  config:
    bucket: my-bucket
    key: state.json

resources:
  module:
    auth:
      attributes:
        description: "Auth module"
""",
            encoding="utf-8",
        )

        spec, backend_config = load_spec_with_backend(spec_file)
        assert backend_config is not None
        assert backend_config["type"] == "s3"
        assert backend_config["config"]["bucket"] == "my-bucket"
        assert len(spec.list()) == 1

    def test_load_spec_without_backend(self, tmp_path):
        from terra4mice.spec_parser import load_spec_with_backend

        spec_file = tmp_path / "terra4mice.spec.yaml"
        spec_file.write_text(
            """
version: "1"
resources:
  module:
    auth:
      attributes:
        description: "Auth module"
""",
            encoding="utf-8",
        )

        spec, backend_config = load_spec_with_backend(spec_file)
        assert backend_config is None
        assert len(spec.list()) == 1

    def test_load_spec_with_backend_not_found(self, tmp_path):
        from terra4mice.spec_parser import load_spec_with_backend

        with pytest.raises(FileNotFoundError):
            load_spec_with_backend(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# TestCLIStatePullPush
# ---------------------------------------------------------------------------

class TestCLIStatePullPush:
    def test_state_pull_local(self, tmp_path):
        """Test pulling state to a local file."""
        import sys

        # Create a state file
        state_path = tmp_path / "terra4mice.state.json"
        sm = StateManager(path=state_path)
        sm.mark_created("feature.auth", files=["auth.py"])
        sm.save()

        output_path = tmp_path / "pulled.json"

        from terra4mice.cli import main
        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "state", "pull",
                "--state", str(state_path),
                "-o", str(output_path),
            ]
            result = main()
        finally:
            sys.argv = old_argv

        assert result == 0
        assert output_path.exists()
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["version"] == "1"
        assert len(data["resources"]) == 1

    def test_state_push_local(self, tmp_path):
        """Test pushing state from a local file."""
        import sys

        # Create source state file
        source_path = tmp_path / "source.json"
        sm = StateManager(path=source_path)
        sm.mark_created("module.parser", files=["parser.py"])
        sm.save()

        # Target state file
        target_path = tmp_path / "terra4mice.state.json"

        from terra4mice.cli import main
        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "state", "push",
                "--state", str(target_path),
                "-i", str(source_path),
            ]
            result = main()
        finally:
            sys.argv = old_argv

        assert result == 0
        assert target_path.exists()

    def test_state_push_missing_file(self, tmp_path):
        """Push with nonexistent input file should fail."""
        import sys

        from terra4mice.cli import main
        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "state", "push",
                "--state", str(tmp_path / "state.json"),
                "-i", str(tmp_path / "nope.json"),
            ]
            result = main()
        finally:
            sys.argv = old_argv

        assert result == 1


# ---------------------------------------------------------------------------
# TestCLIForceUnlock
# ---------------------------------------------------------------------------

class TestCLIForceUnlock:
    def test_force_unlock_no_locking_backend(self, tmp_path):
        """Force-unlock on local backend (no locking) should fail gracefully."""
        import sys

        state_path = tmp_path / "terra4mice.state.json"
        state_path.write_text('{"version": "1", "serial": 0, "resources": []}', encoding="utf-8")

        from terra4mice.cli import main
        old_argv = sys.argv
        try:
            sys.argv = [
                "terra4mice", "force-unlock", "some-lock-id",
                "--state", str(state_path),
            ]
            result = main()
        finally:
            sys.argv = old_argv

        assert result == 1


# ---------------------------------------------------------------------------
# TestCLIMigrateState
# ---------------------------------------------------------------------------

class TestCLIMigrateState:
    def test_migrate_no_backend_config(self, tmp_path):
        """Migrate should fail if spec has no backend section."""
        import sys

        # Create spec without backend
        spec_path = tmp_path / "terra4mice.spec.yaml"
        spec_path.write_text(
            'version: "1"\nresources:\n  module:\n    auth:\n',
            encoding="utf-8",
        )

        # Create state
        state_path = tmp_path / "terra4mice.state.json"
        state_path.write_text('{"version": "1", "serial": 0, "resources": []}', encoding="utf-8")

        from terra4mice.cli import main
        old_argv = sys.argv
        old_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)
            sys.argv = ["terra4mice", "init", "--migrate-state"]
            result = main()
        finally:
            sys.argv = old_argv
            os.chdir(old_cwd)

        assert result == 1


# ---------------------------------------------------------------------------
# TestCreateBackendHelper
# ---------------------------------------------------------------------------

class TestCreateStateManagerHelper:
    def test_default_local(self):
        """_create_state_manager with no args returns local backend."""
        from terra4mice.cli import _create_state_manager

        args = type("Args", (), {"state": None, "spec": None})()
        sm = _create_state_manager(args)
        assert isinstance(sm.backend, LocalBackend)

    def test_explicit_state_path(self, tmp_path):
        """_create_state_manager with --state uses local path."""
        from terra4mice.cli import _create_state_manager

        p = tmp_path / "custom.json"
        args = type("Args", (), {"state": str(p), "spec": None})()
        sm = _create_state_manager(args)
        assert isinstance(sm.backend, LocalBackend)
        assert sm.path == p

    def test_from_spec_backend_config(self, tmp_path):
        """_create_state_manager reads backend from spec."""
        from terra4mice.cli import _create_state_manager

        spec_path = tmp_path / "terra4mice.spec.yaml"
        spec_path.write_text(
            """
version: "1"
backend:
  type: local
  config:
    path: custom_state.json
resources:
  module:
    test:
""",
            encoding="utf-8",
        )

        args = type("Args", (), {"state": None, "spec": str(spec_path)})()
        sm = _create_state_manager(args)
        assert isinstance(sm.backend, LocalBackend)
