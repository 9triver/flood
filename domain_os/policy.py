"""Minimal policy providers for domain commands."""

from __future__ import annotations

from .models import Capability, CapabilityRisk, Intent, PolicyDecision, Resource


class RiskBasedPolicy:
    """Allow low-risk actions, gate controls, and deny critical actions by default."""

    def evaluate(
        self,
        intent: Intent,
        resource: Resource,
        capability: Capability,
    ) -> PolicyDecision:
        if capability.risk is CapabilityRisk.CRITICAL:
            return PolicyDecision(
                allowed=False,
                reason="critical capabilities require an explicit deployment policy",
            )
        if capability.risk is CapabilityRisk.CONTROLLED:
            return PolicyDecision(
                allowed=True,
                requires_approval=True,
                reason="controlled infrastructure action requires approval",
            )
        return PolicyDecision(allowed=True, reason="low-risk capability")
