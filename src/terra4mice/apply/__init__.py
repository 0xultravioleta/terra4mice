"""
terra4mice Apply Runner — Multi-modal execution engine.

Phase 5: Transforms `terra4mice apply` from a basic interactive prompt
into a DAG-ordered, context-aware execution engine.
"""

from .runner import ApplyRunner, ApplyConfig, ApplyResult
from .modes import InteractiveMode, AutoMode, HybridMode, MarketMode
from .verify import verify_implementation, VerificationResult

__all__ = [
    "ApplyRunner",
    "ApplyConfig",
    "ApplyResult",
    "InteractiveMode",
    "AutoMode",
    "HybridMode",
    "MarketMode",
    "verify_implementation",
    "VerificationResult",
]
