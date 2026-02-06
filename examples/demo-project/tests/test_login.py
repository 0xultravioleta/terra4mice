"""Tests for login feature."""

import pytest
from src.auth.login import LoginService, User


@pytest.fixture
def user_store():
    """Create a test user store."""
    import hashlib
    return {
        "test@example.com": User(
            id="user_1",
            email="test@example.com",
            password_hash=hashlib.sha256("password123".encode()).hexdigest()
        )
    }


@pytest.fixture
def login_service(user_store):
    """Create login service with test store."""
    return LoginService(user_store)


class TestLoginService:
    """Test cases for LoginService."""

    def test_login_success(self, login_service):
        """Test successful login."""
        token = login_service.login("test@example.com", "password123")
        assert token is not None
        assert len(token) == 64

    def test_login_wrong_password(self, login_service):
        """Test login with wrong password."""
        token = login_service.login("test@example.com", "wrong")
        assert token is None

    def test_login_unknown_user(self, login_service):
        """Test login with unknown user."""
        token = login_service.login("unknown@example.com", "password123")
        assert token is None

    def test_validate_token(self, login_service):
        """Test token validation."""
        token = login_service.login("test@example.com", "password123")
        assert login_service.validate_token(token) is True

    def test_validate_invalid_token(self, login_service):
        """Test invalid token validation."""
        assert login_service.validate_token("short") is False
