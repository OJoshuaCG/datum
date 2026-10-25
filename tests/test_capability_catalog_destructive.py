"""
Las dos capacidades de la aprobación destructiva: ``blueprints.approve_destructive`` y
``blueprints.apply_destructive``.

Separan al que REVISA el SQL del que lo EJECUTA sobre un entorno protegido. Fijan acá lo que el
catálogo no deja elegir (invariantes 5, 7c, 8 y 11) y lo que la política del cambio exige: solo
owner, step-up, fuera del techo de agente, sensibles (el conjunto pasa de 17 a 19) y fuera de
los scopes de integración.

Verificar con
``.venv/bin/python scripts/run_tests_direct.py tests.test_capability_catalog_destructive``.
"""

import pytest

import app.services.capability_catalog as cc
from app.core import authz
from app.services.capability_catalog import (
    AGENT_ALLOWED,
    CAPABILITIES,
    Capability,
    GatewayRole,
)
from app.services.integration_scope_catalog import INTEGRATION_ALLOWED

APPROVE = Capability.BLUEPRINTS_APPROVE_DESTRUCTIVE
APPLY = Capability.BLUEPRINTS_APPLY_DESTRUCTIVE
BOTH = (APPROVE, APPLY)

EXPECTED_SENSITIVE_COUNT = 19


def test_the_values_are_the_wire_names():
    assert APPROVE.value == "blueprints.approve_destructive"
    assert APPLY.value == "blueprints.apply_destructive"


@pytest.mark.parametrize("capability", BOTH)
def test_both_are_sensitive_step_up_mutating_and_outside_the_agent_ceiling(capability):
    spec = cc.spec(capability)
    assert spec.mutates
    assert spec.requires_step_up
    assert not spec.discloses
    assert not spec.agent_allowed
    assert capability not in AGENT_ALLOWED
    assert cc.is_grantable(capability)
    assert cc.is_sensitive(capability)
    assert cc.needs_second_approver(capability=capability)


def test_only_applying_is_destructive_because_approving_writes_gateway_metadata_only():
    assert not cc.spec(APPROVE).destructive
    assert cc.spec(APPLY).destructive


@pytest.mark.parametrize("capability", BOTH)
def test_both_are_owner_only_in_the_role_chain(capability):
    assert capability in cc.ROLE_CAPABILITIES[GatewayRole.OWNER]
    assert capability not in cc.ROLE_CAPABILITIES[GatewayRole.OPERATOR]
    assert capability not in cc.ROLE_CAPABILITIES[GatewayRole.VIEWER]
    assert capability in cc.OWNER_ONLY_CAPABILITIES


@pytest.mark.parametrize("capability", BOTH)
def test_neither_is_a_global_capability(capability):
    for global_capabilities in cc.GLOBAL_CAPABILITIES.values():
        assert capability not in global_capabilities
    assert cc.spec(capability).scope_axis == "environment"


@pytest.mark.parametrize("capability", BOTH)
def test_granting_either_loose_implies_only_the_blueprints_read(capability):
    assert cc.IMPLIED_READ[capability] == frozenset({Capability.BLUEPRINTS_READ})


def test_the_sensitive_set_grows_from_seventeen_to_nineteen_with_exactly_these_two():
    sensitive = {s.id.value for s in CAPABILITIES if cc.is_sensitive(s.id)}
    assert len(sensitive) == EXPECTED_SENSITIVE_COUNT
    assert len(cc._SENSITIVE_POLICY) == EXPECTED_SENSITIVE_COUNT
    assert {APPROVE.value, APPLY.value} <= sensitive
    assert sensitive == cc._SENSITIVE_POLICY


def test_the_catalog_matrix_published_to_the_frontend_lists_both_with_their_flags():
    rows = {row["id"]: row for row in cc.capability_matrix()}
    for capability in BOTH:
        row = rows[capability.value]
        assert row["sensitive"] is True
        assert row["requires_step_up"] is True
        assert row["agent_allowed"] is False
        assert row["grantable"] is True
        assert row["roles"] == ["owner"]
    assert rows[APPROVE.value]["destructive"] is False
    assert rows[APPLY.value]["destructive"] is True


def test_neither_is_reachable_from_an_integration_scope():
    """
    Una máquina nunca consume una aprobación ni la otorga: ningún scope de integración mapea a
    estas capacidades, así que el guard le sigue devolviendo ``environment.destructive_blocked``.
    """
    mapped_capabilities = set(INTEGRATION_ALLOWED.values())
    assert APPROVE not in mapped_capabilities
    assert APPLY not in mapped_capabilities


def test_the_authz_aliases_exist_and_declare_their_capability():
    assert authz.declared_capability(_dependency_of(authz.BlueprintsApproveDestructive)) == APPROVE.value
    assert authz.declared_capability(_dependency_of(authz.BlueprintsApplyDestructive)) == APPLY.value


def _dependency_of(annotated_alias):
    """La ``Depends(...)`` embebida en un ``Annotated[Actor, Depends(...)]``."""
    dependency_marker = annotated_alias.__metadata__[0]
    return dependency_marker.dependency


def test_import_invariants_reject_the_apply_capability_in_operator(monkeypatch):
    """El invariante 7c (destructiva => solo owner) cubre a la capacidad nueva, no solo a las viejas."""
    from types import MappingProxyType

    roles = dict(cc.ROLE_CAPABILITIES)
    roles[GatewayRole.OPERATOR] = roles[GatewayRole.OPERATOR] | {APPLY}
    monkeypatch.setattr(cc, "ROLE_CAPABILITIES", MappingProxyType(roles))
    with pytest.raises(AssertionError, match="destructive"):
        cc._assert_invariants()


def test_import_invariants_reject_a_sensitive_set_that_loses_one_of_them(monkeypatch):
    monkeypatch.setattr(cc, "_SENSITIVE_POLICY", cc._SENSITIVE_POLICY - {APPROVE.value})
    with pytest.raises(AssertionError, match="sensible"):
        cc._assert_invariants()
