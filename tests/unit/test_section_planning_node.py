# GOV-C2-007 — Unit Tests: SectionPlanningNode (inner domain node 3)
#
# Resolves the ordered section plan against the ministry/agency template and
# marks which sections actually have source material, writing section_plan as
# a JSON string.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.


from src.nodes.section_planning_node import SectionPlanningNode
from src.schemas.state import from_json, to_json


_CASE_RECORD = to_json({"report_type": "general", "subject": "Subj", "fields": {}, "data_points": []})

_FACTS_WITH_CONTENT = to_json(
    {
        "background": ["Subject: Subj"],
        "findings": ["finding 1"],
        "recommendations_source": ["rec 1"],
        "metrics": {"data_point_count": 1},
        "data_points": [],
    }
)

_FACTS_EMPTY = to_json(
    {
        "background": [],
        "findings": [],
        "recommendations_source": [],
        "metrics": {},
        "data_points": [],
    }
)


class TestSectionPlanningContract:
    def test_returns_section_plan_key_only(self):
        node = SectionPlanningNode()
        result = node.execute({"case_record": _CASE_RECORD, "extracted_facts": _FACTS_WITH_CONTENT})
        assert set(result.keys()) == {"section_plan"}

    def test_section_plan_is_json_list_of_dicts(self):
        node = SectionPlanningNode()
        plan = from_json(
            node.execute({"case_record": _CASE_RECORD, "extracted_facts": _FACTS_WITH_CONTENT})["section_plan"]
        )
        assert isinstance(plan, list)
        assert len(plan) >= 1
        for section in plan:
            assert isinstance(section, dict)
            for field in ("key", "title", "required", "source_facts", "has_content"):
                assert field in section, f"section entry missing field: {field}"


class TestSectionPlanningDefaultTemplate:
    def test_default_sections_present(self):
        node = SectionPlanningNode()
        plan = from_json(
            node.execute({"case_record": _CASE_RECORD, "extracted_facts": _FACTS_WITH_CONTENT})["section_plan"]
        )
        keys = [s["key"] for s in plan]
        # The built-in ministry template covers these four sections.
        for expected in ("background", "findings", "recommendations", "next_actions"):
            assert expected in keys, f"default template missing section: {expected}"

    def test_has_content_true_when_facts_present(self):
        node = SectionPlanningNode()
        plan = from_json(
            node.execute({"case_record": _CASE_RECORD, "extracted_facts": _FACTS_WITH_CONTENT})["section_plan"]
        )
        by_key = {s["key"]: s for s in plan}
        # findings section is fed by findings + metrics — both non-empty here.
        assert by_key["findings"]["has_content"] is True

    def test_has_content_false_when_facts_empty(self):
        node = SectionPlanningNode()
        plan = from_json(node.execute({"case_record": _CASE_RECORD, "extracted_facts": _FACTS_EMPTY})["section_plan"])
        by_key = {s["key"]: s for s in plan}
        assert by_key["findings"]["has_content"] is False


class TestSectionPlanningConfigOverride:
    """A declared section template reaches this node through seeded state.

    A domain node's contract is execute(self, state) -> dict — there is no
    per-call config argument — so the settings arrive as the `report_config`
    field the inner graph seeds. These tests drive that field directly; the
    end-to-end proof that a value declared in config/config.yaml actually
    arrives here lives in tests/unit/test_runtime_config.py.
    """

    def test_per_report_type_template_override(self):
        node = SectionPlanningNode()
        cr = to_json({"report_type": "incident", "subject": "S", "fields": {}, "data_points": []})
        report_config = to_json(
            {
                "report_template_path": "config/admin_report_template.md",
                "section_templates": {
                    "incident": [
                        {"key": "summary", "title": "Summary", "required": True, "source_facts": ["background"]},
                    ]
                },
            }
        )
        plan = from_json(
            node.execute(
                {
                    "case_record": cr,
                    "extracted_facts": _FACTS_WITH_CONTENT,
                    "report_config": report_config,
                }
            )["section_plan"]
        )
        keys = [s["key"] for s in plan]
        assert keys == ["summary"], f"override template not applied, got {keys}"

    def test_template_for_another_report_type_is_not_applied(self):
        """The override is keyed by report_type — a mismatch keeps the built-in set."""
        node = SectionPlanningNode()
        cr = to_json({"report_type": "general", "subject": "S", "fields": {}, "data_points": []})
        report_config = to_json(
            {
                "section_templates": {
                    "incident": [
                        {"key": "summary", "title": "Summary", "required": True, "source_facts": ["background"]},
                    ]
                }
            }
        )
        plan = from_json(
            node.execute(
                {
                    "case_record": cr,
                    "extracted_facts": _FACTS_WITH_CONTENT,
                    "report_config": report_config,
                }
            )["section_plan"]
        )
        assert [s["key"] for s in plan] == ["background", "findings", "recommendations", "next_actions"]
