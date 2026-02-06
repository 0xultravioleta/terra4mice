"""
Payment Processor Module

Handles payment processing via x402 protocol.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PaymentStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Payment:
    id: str
    amount: float
    currency: str
    status: PaymentStatus


class PaymentProcessor:
    """Process payments using x402 protocol."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.payments = {}

    def create_payment(self, amount: float, currency: str = "USDC") -> Payment:
        """Create a new payment."""
        import uuid
        payment_id = str(uuid.uuid4())
        payment = Payment(
            id=payment_id,
            amount=amount,
            currency=currency,
            status=PaymentStatus.PENDING
        )
        self.payments[payment_id] = payment
        return payment

    def process_payment(self, payment_id: str) -> bool:
        """Process a pending payment."""
        payment = self.payments.get(payment_id)
        if not payment:
            return False

        # Simulate processing
        payment.status = PaymentStatus.COMPLETED
        return True

    def get_payment(self, payment_id: str) -> Optional[Payment]:
        """Get payment by ID."""
        return self.payments.get(payment_id)
