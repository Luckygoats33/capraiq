from __future__ import annotations

from ..policy import Action, Role
from .base import Agent, AgentContext, AgentOutput, Finding


class LifecycleAuditor(Agent):
    """PHASE 2 STUB. Will audit Klaviyo flows/campaigns once a read-only connector exists.
    v1: no connector, no network, no proposals."""
    role = Role.LIFECYCLE_AUDITOR

    def run(self, ctx: AgentContext) -> AgentOutput:
        ctx.policy.enforce(self.role, Action.READ_METRICS)
        return AgentOutput(self.role, findings=[Finding(
            self.role, "Lifecycle audit not enabled in v1 (Klaviyo flows are phase 2); no lifecycle claims are made.")])
