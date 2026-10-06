# GOV-C2-007 — Unit Tests: DataExtractionNode (inner domain node 2)
#
# Reads the sanitized case_record (JSON string), buckets fields into report
# sections, aggregates numeric data points into metrics, and writes
# extracted_facts as a JSON string.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import pytest

from src.nodes.data_extraction_node import DataExtractionNode
from src.schemas.state import from_json, to_json


def _case_record(**overrides):
    base = {
        "report_type": "survey",
        "subject": "Bridge inspection FY2025",
        "period": "2025-Q1",
        "department": "Public Works",
        "fields": {
            "background": "Routine annual inspection.",
            "findings": "Two joints show corrosion.",
            "recommendation": "Schedule repairs.",
            "misc_observation": "Lighting needs review.",
        },
        "data_points": [{"score": 80}, {"score": 60}, {"score": "40"}],
        "free_text": "",
    }
    base.update(overrides)
    return to_json(base)


class TestDataExtractionContract:
    def test_returns_extracted_facts_key_only(self):
        node = DataExtractionNode()
        result = node.execute({"case_record": _case_record()})
        assert set(result.keys()) == {"extracted_facts"}

    def test_extracted_facts_is_json_string(self):
        node = DataExtractionNode()
        result = node.execute({"case_record": _case_record()})
        assert isinstance(result["extracted_facts"], str)
        facts = from_json(result["extracted_facts"])
        assert isinstance(facts, dict)

    def test_section_buckets_present(self):
        node = DataExtractionNode()
        facts = from_json(node.execute({"case_record": _case_record()})["extracted_facts"])
        for key in ("background", "findings", "recommendations_source", "metrics", "data_points"):
            assert key in facts, f"extracted_facts missing bucket: {key}"
        assert isinstance(facts["background"], list)
        assert isinstance(facts["findings"], list)
        assert isinstance(facts["metrics"], dict)


class TestDataExtractionBucketing:
    def test_subject_and_period_seed_background(self):
        node = DataExtractionNode()
        facts = from_json(node.execute({"case_record": _case_record()})["extracted_facts"])
        background_text = " ".join(facts["background"])
        assert "Bridge inspection FY2025" in background_text
        assert "2025-Q1" in background_text

    def test_recommendation_field_routes_to_recommendations_source(self):
        node = DataExtractionNode()
        facts = from_json(node.execute({"case_record": _case_record()})["extracted_facts"])
        rec_text = " ".join(facts["recommendations_source"])
        assert "Schedule repairs" in rec_text

    def test_unclassified_field_routes_to_findings(self):
        node = DataExtractionNode()
        facts = from_json(node.execute({"case_record": _case_record()})["extracted_facts"])
        findings_text = " ".join(facts["findings"])
        # misc_observation has no hint match → lands in findings.
        assert "Lighting needs review" in findings_text


class TestDataExtractionMetrics:
    def test_numeric_data_points_aggregated(self):
        node = DataExtractionNode()
        facts = from_json(node.execute({"case_record": _case_record()})["extracted_facts"])
        metrics = facts["metrics"]
        assert metrics["data_point_count"] == 3
        # 80, 60, "40" → all coerce to numbers.
        assert metrics["numeric_count"] == 3
        assert metrics["numeric_total"] == pytest.approx(180.0)
        assert metrics["numeric_average"] == pytest.approx(60.0)
        assert metrics["numeric_min"] == pytest.approx(40.0)
        assert metrics["numeric_max"] == pytest.approx(80.0)

    def test_no_numeric_points_still_has_count(self):
        node = DataExtractionNode()
        cr = _case_record(data_points=[], fields={})
        facts = from_json(node.execute({"case_record": cr})["extracted_facts"])
        assert facts["metrics"]["data_point_count"] == 0
        assert "numeric_average" not in facts["metrics"]

    def test_missing_case_record_is_non_fatal(self):
        """from_json(None) → {} default; node still returns a well-formed dict."""
        node = DataExtractionNode()
        facts = from_json(node.execute({})["extracted_facts"])
        assert isinstance(facts, dict)
        assert facts["metrics"]["data_point_count"] == 0
