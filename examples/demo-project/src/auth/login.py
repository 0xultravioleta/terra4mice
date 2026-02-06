"""
Authentication - Login feature

This module implements user login functionality.
"""

from dataclasses import dataclass
from typing import Optional
import hashlib


@dataclass
class User:
    id: str
    email: str
    password_hash: str


class LoginService:
    """Handle user login operations."""

    def __init__(self, user_store: dict):
        self.user_store = user_store

    def login(self, email: str, password: str) -> Optional[str]:
        """
        Authenticate user and return session token.

        Args:
            email: User email
            password: User password

        Returns:
            Session token if successful, None otherwise
        """
        user = self.user_store.get(email)
        if not user:
            return None

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        if user.password_hash != password_hash:
            return None

        # Generate session token
        token = hashlib.sha256(f"{user.id}:{email}".encode()).hexdigest()
        return token

    def validate_token(self, token: str) -> bool:
        """Validate a session token."""
        # Simplified validation
        return len(token) == 64


def create_login_handler(user_store: dict):
    """Factory function to create login handler."""
    return LoginService(user_store)
