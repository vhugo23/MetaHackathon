from datetime import UTC, datetime

import pytest

from meta_rne.domain.diagnosis import (
    BgpSessionObserved,
    Diagnosis,
    DiagnosisConfidence,
    DiagnosisType,
    EndpointBgpState,
    EndpointInterfaceState,
    EvidenceItem,
    EvidenceKind,
    EvidenceRole,
    InterfaceStateObserved,
    LinkRef,
    SymptomKey,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
LINK = LinkRef("leaf-1", "eth1", "spine-1", "eth1")
ITEM = EvidenceItem(
    EvidenceKind.INTERFACE_STATE,
    "leaf-1",
    "eth1",
    InterfaceStateObserved("eth1", EndpointInterfaceState.DOWN),
    EvidenceRole.SUPPORTS,
)


def _diagnosis(
    dtype: DiagnosisType,
    confidence: DiagnosisConfidence | None,
    evidence: tuple[EvidenceItem, ...] = (ITEM,),
    collected_at: datetime = NOW,
) -> Diagnosis:
    return Diagnosis(
        diagnosis_type=dtype,
        resource=LINK,
        confidence=confidence,
        evidence=evidence,
        symptom_keys=(),
        collection_id="run-1",
        collected_at=collected_at,
    )


def test_link_ref_resource_id_and_canonical_order() -> None:
    assert LINK.resource_id == "leaf-1:eth1--spine-1:eth1"
    with pytest.raises(ValueError, match="canonical"):
        LinkRef("spine-1", "eth1", "leaf-1", "eth1")


def test_diagnosis_enum_values_are_exact() -> None:
    assert [t.value for t in DiagnosisType] == [
        "PHYSICAL_LINK_FAILURE",
        "BGP_SESSION_FAILURE",
        "INSUFFICIENT_EVIDENCE",
    ]
    assert [c.value for c in DiagnosisConfidence] == ["HIGH", "MEDIUM", "LOW"]


def test_confidence_is_none_exactly_for_insufficient_evidence() -> None:
    _diagnosis(DiagnosisType.INSUFFICIENT_EVIDENCE, None)
    _diagnosis(DiagnosisType.PHYSICAL_LINK_FAILURE, DiagnosisConfidence.LOW)
    with pytest.raises(ValueError):
        _diagnosis(DiagnosisType.INSUFFICIENT_EVIDENCE, DiagnosisConfidence.LOW)
    with pytest.raises(ValueError):
        _diagnosis(DiagnosisType.BGP_SESSION_FAILURE, None)


def test_diagnosis_requires_evidence_and_utc() -> None:
    with pytest.raises(ValueError):
        _diagnosis(DiagnosisType.PHYSICAL_LINK_FAILURE, DiagnosisConfidence.LOW, evidence=())
    with pytest.raises(ValueError):
        _diagnosis(
            DiagnosisType.PHYSICAL_LINK_FAILURE,
            DiagnosisConfidence.LOW,
            collected_at=datetime(2026, 10, 7),
        )


def test_evidence_observed_type_must_match_kind() -> None:
    with pytest.raises(ValueError, match="InterfaceStateObserved"):
        EvidenceItem(
            EvidenceKind.INTERFACE_STATE,
            "leaf-1",
            "eth1",
            BgpSessionObserved("10.255.0.0", EndpointBgpState.DOWN, "Active"),
            EvidenceRole.SUPPORTS,
        )


def test_symptom_key_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        SymptomKey("", "bgp-neighbor:10.255.0.0")
