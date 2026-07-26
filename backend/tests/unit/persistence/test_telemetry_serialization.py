"""Telemetry nested-value serialization (Gate E2A).

interface_states_to_json/_from_json and bgp_sessions_to_json/_from_json —
standalone sibling function pairs mirroring required_acl_rules_to_json's
convention for a standalone tuple of flat dataclasses: a bare JSON array,
`.value` enum serialization, tuple order preserved, duplicates preserved,
an unexpected extra key accepted and ignored (a successful case, not a
malformed one), and SerializationError for every genuinely malformed input.
"""

import pytest

from meta_rne.domain.telemetry import BgpSession, BgpState, InterfaceState, LinkState
from meta_rne.persistence.serialization import (
    SerializationError,
    bgp_sessions_from_json,
    bgp_sessions_to_json,
    interface_states_from_json,
    interface_states_to_json,
)

# --- InterfaceState: successful cases ----------------------------------------


def test_interface_states_to_json__empty_tuple__returns_empty_list() -> None:
    assert interface_states_to_json(()) == []


def test_interface_states__empty_tuple__round_trips() -> None:
    assert interface_states_from_json(interface_states_to_json(())) == ()


def test_interface_states__one_entry__round_trips() -> None:
    states = (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),)

    assert interface_states_from_json(interface_states_to_json(states)) == states


def test_interface_states__multiple_entries__preserve_order() -> None:
    states = (
        InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),
        InterfaceState(name="GigabitEthernet0/2", oper_state=LinkState.DOWN),
        InterfaceState(name="Ethernet1", oper_state=LinkState.UP),
    )

    assert interface_states_from_json(interface_states_to_json(states)) == states


def test_interface_states__duplicate_names__survive_unchanged() -> None:
    states = (
        InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),
        InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.DOWN),
    )

    assert interface_states_from_json(interface_states_to_json(states)) == states


def test_interface_states_to_json__exact_keys() -> None:
    states = (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),)

    result = interface_states_to_json(states)

    assert set(result[0].keys()) == {"name", "oper_state"}


def test_interface_states_to_json__oper_state_serialized_through_value() -> None:
    states = (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.DOWN),)

    result = interface_states_to_json(states)

    assert result[0]["oper_state"] == "down"


def test_interface_states_from_json__extra_key_ignored__returns_expected_object() -> None:
    data = [{"name": "GigabitEthernet0/1", "oper_state": "up", "extra": "ignored"}]

    result = interface_states_from_json(data)

    assert result == (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),)


# --- BgpSession: successful cases --------------------------------------------


def test_bgp_sessions_to_json__empty_tuple__returns_empty_list() -> None:
    assert bgp_sessions_to_json(()) == []


def test_bgp_sessions__empty_tuple__round_trips() -> None:
    assert bgp_sessions_from_json(bgp_sessions_to_json(())) == ()


def test_bgp_sessions__one_entry__round_trips() -> None:
    sessions = (BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),)

    assert bgp_sessions_from_json(bgp_sessions_to_json(sessions)) == sessions


def test_bgp_sessions__multiple_entries__preserve_order() -> None:
    sessions = (
        BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),
        BgpSession(neighbor_ip="10.0.0.2", state=BgpState.IDLE),
        BgpSession(neighbor_ip="10.0.0.3", state=BgpState.CONNECT),
    )

    assert bgp_sessions_from_json(bgp_sessions_to_json(sessions)) == sessions


def test_bgp_sessions__duplicate_neighbor_ips__survive_unchanged() -> None:
    sessions = (
        BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),
        BgpSession(neighbor_ip="10.0.0.1", state=BgpState.IDLE),
    )

    assert bgp_sessions_from_json(bgp_sessions_to_json(sessions)) == sessions


def test_bgp_sessions_to_json__exact_keys() -> None:
    sessions = (BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),)

    result = bgp_sessions_to_json(sessions)

    assert set(result[0].keys()) == {"neighbor_ip", "state"}


def test_bgp_sessions_to_json__state_serialized_through_value_preserving_casing() -> None:
    sessions = (
        BgpSession(neighbor_ip="10.0.0.1", state=BgpState.OPEN_SENT),
        BgpSession(neighbor_ip="10.0.0.2", state=BgpState.OPEN_CONFIRM),
    )

    result = bgp_sessions_to_json(sessions)

    assert result[0]["state"] == "OpenSent"
    assert result[1]["state"] == "OpenConfirm"


def test_bgp_sessions_from_json__extra_key_ignored__returns_expected_object() -> None:
    data = [{"neighbor_ip": "10.0.0.1", "state": "Established", "extra": "ignored"}]

    result = bgp_sessions_from_json(data)

    assert result == (BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),)


# --- Malformed cases: InterfaceState ------------------------------------------


def test_interface_states_from_json__non_list_top_level__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        interface_states_from_json({"not": "a list"})


def test_interface_states_from_json__non_dict_element__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        interface_states_from_json(["not-a-dict"])


def test_interface_states_from_json__missing_name__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        interface_states_from_json([{"oper_state": "up"}])


def test_interface_states_from_json__missing_oper_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        interface_states_from_json([{"name": "GigabitEthernet0/1"}])


def test_interface_states_from_json__invalid_oper_state_value__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        interface_states_from_json([{"name": "GigabitEthernet0/1", "oper_state": "sideways"}])


# --- Malformed cases: BgpSession -----------------------------------------------


def test_bgp_sessions_from_json__non_list_top_level__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_sessions_from_json({"not": "a list"})


def test_bgp_sessions_from_json__non_dict_element__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_sessions_from_json(["not-a-dict"])


def test_bgp_sessions_from_json__missing_neighbor_ip__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_sessions_from_json([{"state": "Established"}])


def test_bgp_sessions_from_json__missing_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_sessions_from_json([{"neighbor_ip": "10.0.0.1"}])


def test_bgp_sessions_from_json__invalid_state_value__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_sessions_from_json([{"neighbor_ip": "10.0.0.1", "state": "NotARealState"}])
