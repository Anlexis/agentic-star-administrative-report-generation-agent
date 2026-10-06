# GOV-C2-007 — Unit Tests: ReportRenderingNode (inner domain node 5, terminal)
#
# Assembles the drafted sections into the final ministry-style Markdown report.
# This is the ONLY domain node that sets `status` (AgentStatus.SUCCESS). It
# falls back to a built-in template when Jinja2 / the template file is absent
# (the CI case).
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from framework.schemas.agent_status import AgentStatus

from src.nodes.report_rendering_node import ReportRenderingNode
from src.schemas.state import to_json


_CASE_RECORD = to_json(
    {
        "report_type": "survey",
        "subject": "Bridge inspection FY2025",
        "period": "2025-Q1",
        "department": "Public Works",
    }
)

_SECTION_PLAN = to_json(
    [
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
            "source_facts": ["findings"],
            "has_content": True,
        },
    ]
)

_SECTION_DRAFTS = to_json(
    {
        "background": "This report concerns Bridge inspection FY2025.",
        "findings": "Two joints show corrosion.",
    }
)


class TestReportRenderingContract:
    def test_returns_report_and_status(self):
        node = ReportRenderingNode()
        result = node.execute(
            {"section_plan": _SECTION_PLAN, "section_drafts": _SECTION_DRAFTS, "case_record": _CASE_RECORD}
        )
        assert set(result.keys()) == {"administrative_report", "status"}

    def test_status_is_success(self):
        node = ReportRenderingNode()
        result = node.execute(
            {"section_plan": _SECTION_PLAN, "section_drafts": _SECTION_DRAFTS, "case_record": _CASE_RECORD}
        )
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_report_is_non_empty_string(self):
        node = ReportRenderingNode()
        result = node.execute(
            {"section_plan": _SECTION_PLAN, "section_drafts": _SECTION_DRAFTS, "case_record": _CASE_RECORD}
        )
        assert isinstance(result["administrative_report"], str)
        assert result["administrative_report"].strip()


class TestReportRenderingContent:
    def test_header_metadata_rendered(self):
        node = ReportRenderingNode()
        report = node.execute(
            {"section_plan": _SECTION_PLAN, "section_drafts": _SECTION_DRAFTS, "case_record": _CASE_RECORD}
        )["administrative_report"]
        assert "Bridge inspection FY2025" in report
        assert "survey" in report
        assert "2025-Q1" in report
        assert "Public Works" in report

    def test_section_titles_and_bodies_rendered(self):
        node = ReportRenderingNode()
        report = node.execute(
            {"section_plan": _SECTION_PLAN, "section_drafts": _SECTION_DRAFTS, "case_record": _CASE_RECORD}
        )["administrative_report"]
        assert "Background" in report
        assert "Findings" in report
        assert "Two joints show corrosion." in report

    def test_appi_footer_present(self):
        node = ReportRenderingNode()
        report = node.execute(
            {"section_plan": _SECTION_PLAN, "section_drafts": _SECTION_DRAFTS, "case_record": _CASE_RECORD}
        )["administrative_report"]
        assert "APPI" in report


class TestReportRenderingEdgeCases:
    def test_required_section_without_draft_gets_placeholder(self):
        """A required section missing a draft body still renders (placeholder)."""
        node = ReportRenderingNode()
        plan = to_json(
            [{"key": "background", "title": "Background", "required": True, "source_facts": [], "has_content": False}]
        )
        report = node.execute({"section_plan": plan, "section_drafts": to_json({}), "case_record": _CASE_RECORD})[
            "administrative_report"
        ]
        assert "No content available" in report

    def test_empty_plan_still_renders_header(self):
        node = ReportRenderingNode()
        result = node.execute({"section_plan": to_json([]), "section_drafts": to_json({}), "case_record": _CASE_RECORD})
        assert result["status"] == AgentStatus.SUCCESS.value
        # Title still present even with no sections.
        assert "Administrative Report" in result["administrative_report"]
