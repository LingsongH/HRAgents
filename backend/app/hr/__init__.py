"""Enterprise HR domain layer.

The original project keeps its generic Harness/Memory/Loop runtime.  This package
adds HR-specific trusted-policy semantics without duplicating the control plane.
"""

from app.hr.policy_claims import PolicyClaimExtractor
from app.hr.trusted_rag import AccessContext, TrustedPolicyRAG
from app.hr.conflict_workflow import PolicyConflictWorkflow
from app.hr.bounded_react import BoundedReActRuntime

__all__ = [
    "AccessContext",
    "TrustedPolicyRAG",
    "PolicyClaimExtractor",
    "PolicyConflictWorkflow",
    "BoundedReActRuntime",
]
