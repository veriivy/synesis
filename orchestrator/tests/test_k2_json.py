"""K2 JSON is untrusted and often truncated at the token cap."""

from orchestrator.k2 import extract_object


def test_extract_object_repairs_truncated_payload():
    raw = (
        '{ "round": 3, "similarities": [ { "topic": "auth", '
        '"detail": "Both plans implement session cookies; failures retain t'
    )
    data = extract_object(raw)
    assert data["round"] == 3
    assert isinstance(data["similarities"], list)
    assert data["similarities"][0]["topic"] == "auth"
