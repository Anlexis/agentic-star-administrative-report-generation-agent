# GOV-C2-007 — Unit Tests: State round-trip (msgpack safety)
#
# The 5 complex inner-layer fields (case_record, intake_notes, extracted_facts,
# section_plan, section_drafts) are stored in State as JSON STRINGS — never bare
# dict/list — and (de)serialized via to_json() / from_json() in
# src.schemas.state. These tests prove that round-trip is lossless and that the
# helpers fail safe on None / malformed input.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import pytest

from src.schemas.state import State, from_json, to_json


# Representative shapes for the 5 complex fields per the state.py docstring.
_CASE_RECORD = {
    "report_type": "survey",
    "subject": "Bridge inspection FY2025",
    "period": "2025-Q1",
    "department": "Public Works",
    "fields": {"background": "Annual inspection"},
    "data_points": [{"segment": "A", "score": 72}],
    "free_text": "",
}
_INTAKE_NOTES = ["Resident PII redacted per APPI (個人情報保護法).", "Missing recommended fields ['subject']."]
_EXTRACTED_FACTS = {
    "background": ["Subject: Bridge inspection FY2025"],
    "findings": ["Two joints show corrosion."],
    "recommendations_source": ["Schedule repairs."],
    "metrics": {"data_point_count": 1, "numeric_average": 72.0},
    "data_points": [{"segment": "A", "score": 72}],
}
_SECTION_PLAN = [
    {
        "key": "background",
        "title": "Background / 背景",
        "required": True,
        "source_facts": ["background"],
        "has_content": True,
    },
    {
        "key": "findings",
        "title": "Findings / 調査結果",
        "required": True,
        "source_facts": ["findings", "metrics"],
        "has_content": True,
    },
]
_SECTION_DRAFTS = {
    "background": "This report concerns Bridge inspection FY2025.",
    "findings": "Two joints show corrosion.",
}

_ALL_COMPLEX_FIELDS = {
    "case_record": _CASE_RECORD,
    "intake_notes": _INTAKE_NOTES,
    "extracted_facts": _EXTRACTED_FACTS,
    "section_plan": _SECTION_PLAN,
    "section_drafts": _SECTION_DRAFTS,
}


class TestRoundTrip:
    @pytest.mark.parametrize("field_name,value", list(_ALL_COMPLEX_FIELDS.items()))
    def test_to_json_then_from_json_is_lossless(self, field_name, value):
        serialized = to_json(value)
        assert isinstance(serialized, str), f"{field_name}: to_json must yield a str"
        restored = from_json(serialized)
        assert restored == value, f"{field_name}: round-trip changed the value"

    def test_japanese_preserved_unescaped(self):
        """ensure_ascii=False keeps Japanese readable in the serialized string."""
        serialized = to_json(_SECTION_PLAN)
        assert "背景" in serialized
        assert from_json(serialized) == _SECTION_PLAN


class TestHelperFailSafe:
    def test_to_json_none_passthrough(self):
        """None must stay None (an 'unset' field stays distinguishable from {})."""
        assert to_json(None) is None

    def test_from_json_none_returns_default(self):
        assert from_json(None) is None
        assert from_json(None, {}) == {}
        assert from_json(None, []) == []

    def test_from_json_empty_string_returns_default(self):
        assert from_json("", {}) == {}

    def test_from_json_malformed_returns_default(self):
        # Malformed JSON must not raise — it falls back to the default.
        assert from_json("{not valid json", {"fallback": True}) == {"fallback": True}
        assert from_json("[1, 2,", []) == []


class TestStateSchema:
    def test_state_is_typeddict_subclass_of_agent_state(self):
        # State extends AgentState; TypedDicts expose __annotations__.
        assert issubclass(State, dict)
        ann = State.__annotations__
        for field in (
            "validated_input",
            "administrative_report",
            "case_record",
            "intake_notes",
            "extracted_facts",
            "section_plan",
            "section_drafts",
        ):
            assert field in ann, f"State missing declared field: {field}"
