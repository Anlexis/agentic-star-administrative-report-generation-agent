# GOV-C2-007 — Unit Tests: ReportDraftingNode (inner domain node 4)
#
# Deterministic, rule-based section synthesis (DocGeneration pattern; the LLM
# seam is bypassed in CI). Reads section_plan + extracted_facts + case_record
# (all JSON strings) and writes section_drafts as a JSON string.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from src.nodes.report_drafting_node import ReportDraftingNode
from src.schemas.state import from_json, to_json


_SECTION_PLAN = to_json(
    [
        {
            "key": "background",
            "title": "Background",
            "required": True,
            "source_facts": ["background"],
            "has_content": True,
        },
        {
            "key": "findings",
            "title": "Findings",
            "required": True,
            "source_facts": ["findings", "metrics"],
            "has_content": True,
        },
        {
            "key": "recommendations",
            "title": "Recommendations",
            "required": True,
            "source_facts": ["recommendations_source"],
            "has_content": True,
        },
        {
            "key": "next_actions",
            "title": "Next Actions",
            "required": True,
            "source_facts": ["recommendations_source"],
            "has_content": True,
        },
    ]
)

_FACTS = to_json(
    {
        "background": ["Subject: Bridge inspection", "Routine annual inspection."],
        "findings": ["Two joints show corrosion."],
        "recommendations_source": ["Schedule repairs within 60 days."],
        "metrics": {"data_point_count": 2, "numeric_average": 60.0},
        "data_points": [],
    }
)

_CASE_RECORD = to_json({"report_type": "survey", "subject": "Bridge inspection FY2025"})


class TestReportDraftingContract:
    def test_returns_section_drafts_key_only(self):
        node = ReportDraftingNode()
        result = node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})
        assert set(result.keys()) == {"section_drafts"}

    def test_section_drafts_is_json_dict(self):
        node = ReportDraftingNode()
        drafts = from_json(
            node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        assert isinstance(drafts, dict)

    def test_one_draft_per_planned_section(self):
        node = ReportDraftingNode()
        drafts = from_json(
            node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        assert set(drafts.keys()) == {"background", "findings", "recommendations", "next_actions"}
        for key, body in drafts.items():
            assert isinstance(body, str)
            assert body.strip(), f"section {key} produced empty draft"


class TestReportDraftingContent:
    def test_background_mentions_subject(self):
        node = ReportDraftingNode()
        drafts = from_json(
            node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        assert "Bridge inspection FY2025" in drafts["background"]

    def test_findings_includes_fact_and_metrics(self):
        node = ReportDraftingNode()
        drafts = from_json(
            node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        assert "corrosion" in drafts["findings"]
        # Quantitative summary surfaces metric labels.
        assert "Numeric Average" in drafts["findings"] or "Data Point Count" in drafts["findings"]

    def test_recommendations_uses_source_material(self):
        node = ReportDraftingNode()
        drafts = from_json(
            node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        assert "Schedule repairs within 60 days" in drafts["recommendations"]


class TestReportDraftingEmptyFacts:
    def test_empty_findings_get_placeholder(self):
        node = ReportDraftingNode()
        empty_facts = to_json(
            {"background": [], "findings": [], "recommendations_source": [], "metrics": {}, "data_points": []}
        )
        drafts = from_json(
            node.execute({"section_plan": _SECTION_PLAN, "extracted_facts": empty_facts, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        # findings still drafted (required), with a neutral no-content line.
        assert "findings" in drafts
        assert "No specific findings" in drafts["findings"]

    def test_section_without_key_is_skipped(self):
        node = ReportDraftingNode()
        plan = to_json([{"title": "No key", "required": True, "source_facts": []}])
        drafts = from_json(
            node.execute({"section_plan": plan, "extracted_facts": _FACTS, "case_record": _CASE_RECORD})[
                "section_drafts"
            ]
        )
        assert drafts == {}
