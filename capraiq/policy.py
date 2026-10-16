"""Authority ladder policy engine. v1 is READ-ONLY: no external write is ever allowed."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Callable, Optional


class Level(IntEnum):
    """Ladder, least to most restricted."""
    OBSERVE = 0
    DRAFT = 1
    BOUNDED = 2
    APPROVAL_REQUIRED = 3
    PROHIBITED = 4

    @property
    def label(self) -> str:
        return self.name.lower().replace("_", "-")


class Role(str, Enum):
    DATA_STEWARD = "data_steward"
    PROFIT_ANALYST = "profit_analyst"
    ADS_AUDITOR = "ads_auditor"
    LIFECYCLE_AUDITOR = "lifecycle_auditor"
    SKEPTIC = "skeptic"
    CHIEF_OF_STAFF = "chief_of_staff"


class Action(str, Enum):
    # reads
    READ_METRICS = "read_metrics"
    READ_TENANT_DATA = "read_tenant_data"
    # internal writes (local ledger / files) - not external systems
    DRAFT_RECOMMENDATION = "draft_recommendation"
    WRITE_DECISION_LOG = "write_decision_log"
    WRITE_BRIEF_FILE = "write_brief_file"
    # external writes (store / ad / email platforms)
    PAUSE_CAMPAIGN = "pause_campaign"
    CHANGE_CAMPAIGN_BUDGET = "change_campaign_budget"
    CHANGE_PRICE = "change_price"
    EDIT_EMAIL_FLOW = "edit_email_flow"
    SEND_CUSTOMER_EMAIL = "send_customer_email"
    UPDATE_COST_RECORD = "update_cost_record"
    # never
    CREATE_REFUND = "create_refund"
    SPEND_MONEY = "spend_money"
    DELETE_DATA = "delete_data"
    CROSS_TENANT_READ = "cross_tenant_read"


@dataclass(frozen=True)
class ActionSpec:
    base_level: Level          # level once the platform is write-enabled (future versions)
    external_write: bool
    internal_roles: Optional[frozenset] = None  # who may perform an internal write


ACTIONS: dict[Action, ActionSpec] = {
    Action.READ_METRICS: ActionSpec(Level.OBSERVE, False),
    Action.READ_TENANT_DATA: ActionSpec(Level.OBSERVE, False),
    Action.DRAFT_RECOMMENDATION: ActionSpec(Level.DRAFT, False),
    Action.WRITE_DECISION_LOG: ActionSpec(Level.DRAFT, False, frozenset({Role.CHIEF_OF_STAFF})),
    Action.WRITE_BRIEF_FILE: ActionSpec(Level.DRAFT, False, frozenset({Role.CHIEF_OF_STAFF})),
    Action.PAUSE_CAMPAIGN: ActionSpec(Level.BOUNDED, True),
    Action.CHANGE_CAMPAIGN_BUDGET: ActionSpec(Level.APPROVAL_REQUIRED, True),
    Action.CHANGE_PRICE: ActionSpec(Level.APPROVAL_REQUIRED, True),
    Action.EDIT_EMAIL_FLOW: ActionSpec(Level.APPROVAL_REQUIRED, True),
    Action.SEND_CUSTOMER_EMAIL: ActionSpec(Level.APPROVAL_REQUIRED, True),
    Action.UPDATE_COST_RECORD: ActionSpec(Level.APPROVAL_REQUIRED, True),
    Action.CREATE_REFUND: ActionSpec(Level.PROHIBITED, True),
    Action.SPEND_MONEY: ActionSpec(Level.PROHIBITED, True),
    Action.DELETE_DATA: ActionSpec(Level.PROHIBITED, True),
    Action.CROSS_TENANT_READ: ActionSpec(Level.PROHIBITED, False),
}

# Highest level of autonomy per role (a role may act on its own only at or below this level).
ROLE_CLEARANCE: dict[Role, Level] = {
    Role.DATA_STEWARD: Level.DRAFT,
    Role.PROFIT_ANALYST: Level.DRAFT,
    Role.ADS_AUDITOR: Level.DRAFT,
    Role.LIFECYCLE_AUDITOR: Level.OBSERVE,
    Role.SKEPTIC: Level.DRAFT,
    Role.CHIEF_OF_STAFF: Level.DRAFT,
}

V1_READ_ONLY = True


class PolicyViolation(Exception):
    pass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    level: Level          # effective level in this mode
    reason: str


class PolicyEngine:
    def __init__(self, read_only: bool = V1_READ_ONLY):
        self.read_only = read_only
        self.audit: list[tuple[str, str, bool, str]] = []

    def level_for(self, action: Action) -> Level:
        """Effective level for display: in v1 any external write is at least approval-required."""
        spec = ACTIONS.get(action)
        if spec is None:
            return Level.PROHIBITED
        if spec.external_write and self.read_only and spec.base_level != Level.PROHIBITED:
            return max(spec.base_level, Level.APPROVAL_REQUIRED)
        return spec.base_level

    def evaluate(self, role, action) -> PolicyDecision:
        try:
            role = Role(role)
            action = Action(action)
        except ValueError:
            return self._log(role, action, PolicyDecision(False, Level.PROHIBITED, "unknown role/action: default deny"))
        spec = ACTIONS[action]
        level = self.level_for(action)
        if spec.base_level == Level.PROHIBITED:
            d = PolicyDecision(False, Level.PROHIBITED, "prohibited action")
        elif spec.external_write:
            why = "v1 is read-only: external writes are blocked" if self.read_only else "external write requires approval"
            d = PolicyDecision(False, level, why)
        elif spec.internal_roles is not None and role not in spec.internal_roles:
            d = PolicyDecision(False, level, f"role {role.value} may not perform {action.value}")
        elif level > ROLE_CLEARANCE[role]:
            d = PolicyDecision(False, level, f"role {role.value} lacks clearance for level {level.label}")
        else:
            d = PolicyDecision(True, level, "allowed (read / internal draft)")
        return self._log(role, action, d)

    def _log(self, role, action, d: PolicyDecision) -> PolicyDecision:
        self.audit.append((getattr(role, "value", str(role)), getattr(action, "value", str(action)), d.allowed, d.reason))
        return d

    def enforce(self, role, action) -> PolicyDecision:
        d = self.evaluate(role, action)
        if not d.allowed:
            raise PolicyViolation(f"{getattr(role, 'value', role)} -> {getattr(action, 'value', action)}: {d.reason}")
        return d

    def guarded(self, role, action, fn: Callable, *args, **kwargs):
        self.enforce(role, action)
        return fn(*args, **kwargs)

    def denied_count(self) -> int:
        return sum(1 for a in self.audit if not a[2])
