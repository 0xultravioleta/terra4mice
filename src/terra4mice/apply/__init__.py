"""
terra4mice Apply Runner — Multi-modal execution engine.

Phase 5: Transforms ``terra4mice apply`` from a basic interactive prompt
into a DAG-ordered, context-aware execution engine with AI agent dispatch.

Phase 5.1: Auto/Hybrid/Market modes — AI agents implement resources
automatically, with optional human review and Execution Market integration.
"""

from .runner import ApplyRunner, ApplyConfig, ApplyResult, CyclicDependencyError
from .modes import InteractiveMode, AutoMode, HybridMode, MarketMode
from .verify import verify_implementation, VerificationResult, VerificationLevel
from .agents import (
    AgentBackend,
    AgentResult,
    PromptBuilder,
    SubprocessAgent,
    ClaudeCodeAgent,
    CodexAgent,
    CallableAgent,
    ChainedAgent,
    get_agent,
    register_agent,
    list_agents,
)

__all__ = [
    # Runner
    "ApplyRunner",
    "ApplyConfig",
    "ApplyResult",
    "CyclicDependencyError",
    # Modes
    "InteractiveMode",
    "AutoMode",
    "HybridMode",
    "MarketMode",
    # Agents
    "AgentBackend",
    "AgentResult",
    "PromptBuilder",
    "SubprocessAgent",
    "ClaudeCodeAgent",
    "CodexAgent",
    "CallableAgent",
    "ChainedAgent",
    "get_agent",
    "register_agent",
    "list_agents",
    # Verification
    "verify_implementation",
    "VerificationResult",
    "VerificationLevel",
]
