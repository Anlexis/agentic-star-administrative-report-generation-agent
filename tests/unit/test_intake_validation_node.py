# GOV-C2-007 — Unit Tests: IntakeValidationNode (inner domain node 1)
#
# Parses the officer payload, validates schema, strips resident PII (APPI),
# and writes case_record + intake_notes as JSON strings (msgpack safety).
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import json


from src.nodes.intake_validation_node import IntakeValidationNode
from src.schemas.state import from_json


_VALID_PAYLOAD = json.dumps(
    {
        "report_type": "survey",
        "subject": "Road maintenance survey FY2025",
        "period": "2025-04 .. 2025-09",
        "department": "Public Works",
        "fields": {"background": "Annual inspection", "findings": "3 segments degraded"},
        "data_points": [{"segment": "A", "score": 72}, {"segment": "B", "score": 55}],
    },
    ensure_ascii=False,
)


class TestIntakeValidationContract:
    """execute() returns the case_record + intake_notes partial dict (JSON strings)."""

    def test_returns_expected_keys(self):
        node = IntakeValidationNode()
        result = node.execute({"validated_input": _VALID_PAYLOAD})

        assert set(result.keys()) == {"case_record", "intake_notes"}

    def test_case_record_is_json_string(self):
        node = IntakeValidationNode()
        result = node.execute({"validated_input": _VALID_PAYLOAD})

        # Msgpack safety: complex fields are stored as JSON STRINGS, not bare dicts.
        assert isinstance(result["case_record"], str)
        case_record = from_json(result["case_record"])
        assert isinstance(case_record, dict)

    def test_case_record_carries_core_fields(self):
        node = IntakeValidationNode()
        case_record = from_json(node.execute({"validated_input": _VALID_PAYLOAD})["case_record"])

        assert case_record["report_type"] == "survey"
        assert case_record["subject"] == "Road maintenance survey FY2025"
        assert case_record["period"] == "2025-04 .. 2025-09"
        assert case_record["department"] == "Public Works"
        assert isinstance(case_record["fields"], dict)
        assert isinstance(case_record["data_points"], list)
        assert len(case_record["data_points"]) == 2

    def test_intake_notes_is_json_list(self):
        node = IntakeValidationNode()
        notes = from_json(node.execute({"validated_input": _VALID_PAYLOAD})["intake_notes"])
        assert isinstance(notes, list)
        # Always at least the APPI redaction note.
        assert any("APPI" in str(n) for n in notes)


class TestIntakeValidationFreeText:
    """Non-JSON input is wrapped as a free-text report request (no hard fail)."""

    def test_free_text_is_wrapped(self):
        node = IntakeValidationNode()
        result = node.execute({"validated_input": "Please summarize the bridge inspection results."})

        case_record = from_json(result["case_record"])
        assert case_record["report_type"] == "general"
        assert "bridge inspection" in case_record["free_text"]

    def test_falls_back_to_user_input(self):
        """When validated_input is absent, the node reads user_input."""
        node = IntakeValidationNode()
        result = node.execute({"user_input": _VALID_PAYLOAD})
        case_record = from_json(result["case_record"])
        assert case_record["report_type"] == "survey"


class TestIntakeValidationMissingFields:
    """Missing recommended keys produce a note but not a crash."""

    def test_missing_recommended_keys_noted(self):
        node = IntakeValidationNode()
        # No report_type / subject — both are recommended.
        payload = json.dumps({"period": "2025"})
        notes = from_json(node.execute({"validated_input": payload})["intake_notes"])
        combined = " ".join(str(n) for n in notes)
        assert "Missing recommended fields" in combined
