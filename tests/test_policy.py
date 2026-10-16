import pytest

from capraiq.policy import ACTIONS, Action, Level, PolicyEngine, PolicyViolation, Role, ROLE_CLEARANCE

EXTERNAL = [a for a, s in ACTIONS.items() if s.external_write]


def test_every_action_and_role_mapped():
    assert set(ACTIONS) == set(Action) and set(ROLE_CLEARANCE) == set(Role)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", EXTERNAL)
def test_all_external_writes_blocked_for_every_role(role, action):
    p = PolicyEngine()
    d = p.evaluate(role, action)
    assert not d.allowed and d.level >= Level.APPROVAL_REQUIRED
    with pytest.raises(PolicyViolation):
        p.enforce(role, action)
    called = []
    with pytest.raises(PolicyViolation):
        p.guarded(role, action, lambda: called.append(1))
    assert called == []


def test_v1_clamps_bounded_to_approval_required():
    p = PolicyEngine()
    assert ACTIONS[Action.PAUSE_CAMPAIGN].base_level == Level.BOUNDED
    assert p.level_for(Action.PAUSE_CAMPAIGN) == Level.APPROVAL_REQUIRED


def test_prohibited_stay_prohibited():
    p = PolicyEngine()
    for a in (Action.SPEND_MONEY, Action.DELETE_DATA, Action.CREATE_REFUND, Action.CROSS_TENANT_READ):
        assert p.level_for(a) == Level.PROHIBITED
        for r in Role:
            assert not p.evaluate(r, a).allowed


def test_reads_allowed_for_all_roles():
    p = PolicyEngine()
    for r in Role:
        assert p.evaluate(r, Action.READ_METRICS).allowed


def test_internal_writes_restricted_to_chief():
    p = PolicyEngine()
    assert p.evaluate(Role.CHIEF_OF_STAFF, Action.WRITE_DECISION_LOG).allowed
    assert p.evaluate(Role.CHIEF_OF_STAFF, Action.WRITE_BRIEF_FILE).allowed
    for r in Role:
        if r != Role.CHIEF_OF_STAFF:
            assert not p.evaluate(r, Action.WRITE_DECISION_LOG).allowed


def test_lifecycle_auditor_cannot_draft():
    p = PolicyEngine()
    assert not p.evaluate(Role.LIFECYCLE_AUDITOR, Action.DRAFT_RECOMMENDATION).allowed
    assert p.evaluate(Role.SKEPTIC, Action.DRAFT_RECOMMENDATION).allowed


def test_unknown_role_or_action_default_deny():
    p = PolicyEngine()
    assert not p.evaluate("intern", Action.READ_METRICS).allowed
    assert not p.evaluate(Role.SKEPTIC, "launch_missiles").allowed
    assert p.evaluate(Role.SKEPTIC, "read_metrics").allowed   # strings accepted


def test_even_with_read_only_off_external_write_still_not_allowed_autonomously():
    p = PolicyEngine(read_only=False)
    assert not p.evaluate(Role.ADS_AUDITOR, Action.CHANGE_CAMPAIGN_BUDGET).allowed


def test_audit_log_records_denials():
    p = PolicyEngine()
    p.evaluate(Role.ADS_AUDITOR, Action.PAUSE_CAMPAIGN)
    assert p.denied_count() == 1 and p.audit[0][:3] == ("ads_auditor", "pause_campaign", False)


def test_pipeline_never_performed_external_write(synth):
    allowed_external = [a for a in synth["policy"].audit if a[2] and a[1] in {x.value for x in EXTERNAL}]
    assert allowed_external == []
