"""
Phase 10.5 — ResolutionResult

Structured container returned by EntityResolver.
Every pipeline consumes this object; none implement their own matching logic.
"""

from enum import Enum
from typing import Any, Optional


class ResolutionStatus(Enum):
    SUCCESS          = "SUCCESS"
    CLARIFICATION    = "CLARIFICATION"
    ERROR            = "ERROR"
    NOT_FOUND        = "NOT_FOUND"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"


class ResolutionResult:
    """
    Standardised container returned by the Entity Resolver.

    Attributes
    ----------
    status          — Resolution outcome (SUCCESS, NOT_FOUND, …)
    value           — The resolved entity name (when status == SUCCESS)
    entity_type     — Kind of entity: "database", "table", "column", "view", …
    confidence      — Float 0.0–1.0 reflecting match quality
    match_type      — How the match was achieved: EXACT | ALIAS | FUZZY | NONE
    alternatives    — Ordered list of alternative candidates for clarification
    message         — Human-readable explanation (used in clarification prompts)
    metadata        — Arbitrary extra data (e.g. {"similarity_score": 0.91})
    """

    def __init__(
        self,
        status: ResolutionStatus,
        value: Any = None,
        entity_type: str = "unknown",
        confidence: float = 0.0,
        match_type: str = "NONE",
        alternatives: Optional[list] = None,
        message: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.status       = status
        self.value        = value
        self.entity_type  = entity_type
        self.confidence   = confidence
        self.match_type   = match_type
        self.alternatives = alternatives or []
        self.message      = message
        self.metadata     = metadata or {}

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    @property
    def is_success(self) -> bool:
        return self.status == ResolutionStatus.SUCCESS

    @property
    def is_clarification(self) -> bool:
        return self.status in (
            ResolutionStatus.CLARIFICATION,
            ResolutionStatus.MULTIPLE_MATCHES,
        )

    @property
    def is_not_found(self) -> bool:
        return self.status == ResolutionStatus.NOT_FOUND

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ResolutionResult status={self.status.value} "
            f"match_type={self.match_type} "
            f"value={self.value!r} "
            f"confidence={self.confidence:.2f}>"
        )
