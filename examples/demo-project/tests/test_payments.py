"""Tests for payment processor."""

import pytest
from src.payments.processor import PaymentProcessor, PaymentStatus


@pytest.fixture
def processor():
    """Create payment processor."""
    return PaymentProcessor(api_key="test_key")


class TestPaymentProcessor:
    """Test cases for PaymentProcessor."""

    def test_create_payment(self, processor):
        """Test payment creation."""
        payment = processor.create_payment(100.0, "USDC")
        assert payment is not None
        assert payment.amount == 100.0
        assert payment.currency == "USDC"
        assert payment.status == PaymentStatus.PENDING

    def test_process_payment(self, processor):
        """Test payment processing."""
        payment = processor.create_payment(50.0)
        result = processor.process_payment(payment.id)
        assert result is True
        assert payment.status == PaymentStatus.COMPLETED

    def test_get_payment(self, processor):
        """Test getting payment by ID."""
        payment = processor.create_payment(25.0)
        retrieved = processor.get_payment(payment.id)
        assert retrieved == payment

    def test_process_nonexistent(self, processor):
        """Test processing non-existent payment."""
        result = processor.process_payment("fake_id")
        assert result is False
