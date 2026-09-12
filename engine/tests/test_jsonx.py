"""Model output is untrusted input. These are the shapes we have actually been handed."""

from __future__ import annotations

from engine.jsonx import extract_json, extract_object


def test_plain_object():
    assert extract_json('{"converged": true}') == {"converged": True}


def test_fenced_json():
    text = 'Here is my verdict:\n```json\n{"converged": false}\n```\nHope that helps!'
    assert extract_json(text) == {"converged": False}


def test_unlabelled_fence():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_prose_wrapped_object():
    text = 'After reviewing, I conclude: {"converged": true, "note": "done"} — that is all.'
    assert extract_json(text) == {"converged": True, "note": "done"}


def test_trailing_comma_recovered():
    assert extract_json('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_braces_inside_strings_do_not_confuse_the_scanner():
    text = '{"signature": "def f(x: dict) -> {str: int}", "ok": true}'
    assert extract_json(text) == {"signature": "def f(x: dict) -> {str: int}", "ok": True}


def test_escaped_quote_inside_string():
    assert extract_json('{"note": "he said \\"no\\" firmly"}') == {
        "note": 'he said "no" firmly'
    }


def test_array_at_top_level():
    assert extract_json('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_nested_object_returned_whole():
    text = '{"plan": {"tasks": [{"task_id": "t1"}]}}'
    assert extract_json(text) == {"plan": {"tasks": [{"task_id": "t1"}]}}


def test_garbage_returns_none_and_does_not_raise():
    for bad in ["", "   ", "I cannot help with that.", "{{{{", "{'single': 'quotes'}"]:
        assert extract_json(bad) is None


def test_extract_object_rejects_non_objects():
    assert extract_object("[1, 2, 3]") is None
    assert extract_object("42") is None
    assert extract_object('{"a": 1}') == {"a": 1}
