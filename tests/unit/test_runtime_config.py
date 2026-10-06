# GOV-C2-007 — Unit Tests: runtime configuration actually reaches the workflow
#
# A declared setting that no live reader consumes is the failure this file
# exists to catch. The static manifest (config/agent.yaml) carries identity and
# compile-time requirements only; the runtime parameters live in
# config/config.yaml, and a reader pointed at the wrong file — or at a per-call
# config parameter a domain node never receives — returns nothing and degrades
# silently to built-in defaults with a green suite.
#
# Deterministic — no model, no network. src.* imports only.


from src.graph.graph import ReportGenerationGraphNode, runtime_config
from src.graph.context_bridge import set_caller_contract
from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.schemas.state import from_json


class TestShippedConfigIsReadable:
    def test_config_yaml_is_loaded(self):
        config = runtime_config()
        assert isinstance(config, dict) and config, "config/config.yaml did not load"

    def test_the_declared_backbone_parameters_are_present(self):
        config = runtime_config()
        assert config["max_retry"] == 3
        assert config["timeout_s"] == 30

    def test_the_declared_report_block_is_present(self):
        report = runtime_config()["report"]
        assert "report_template_path" in report
        assert "section_templates" in report

    def test_no_declared_key_is_without_a_reader(self):
        """Every key shipped in config/config.yaml is consumed somewhere.

        max_retry and timeout_s are read by the framework from the graph
        constructor argument; the report block is read through the seeding path
        below. A key outside this set is a dead declaration."""
        assert set(runtime_config()) == {"max_retry", "timeout_s", "report"}


class TestSettingsReachTheInnerGraph:
    def test_parent_config_forwards_the_report_block(self):
        settings = ReportGenerationGraphNode()._parent_config()["configurable"]["report"]
        assert settings["report_template_path"] == runtime_config()["report"]["report_template_path"]

    def test_parent_config_is_never_empty(self):
        """An empty forward is the silent-default failure mode this guards."""
        settings = ReportGenerationGraphNode()._parent_config()["configurable"]["report"]
        assert settings["report_template_path"]

    def test_a_declared_section_template_arrives_in_inner_state(self, monkeypatch):
        """The end-to-end config path: config/config.yaml -> _parent_config()
        -> DomainWorkflowGraph(config=...) -> _extra_initial_state() -> the
        state field SectionPlanningNode reads."""
        monkeypatch.setattr(
            "src.graph.graph.runtime_config",
            lambda: {
                "max_retry": 3,
                "timeout_s": 30,
                "report": {
                    "report_template_path": "config/admin_report_template.md",
                    "section_templates": {
                        "incident": [
                            {
                                "key": "summary",
                                "title": "Incident Summary",
                                "required": True,
                                "source_facts": ["background"],
                            }
                        ]
                    },
                },
            },
        )
        set_caller_contract({})
        inner = ReportGenerationGraphNode().get_subgraph()
        seeded = from_json(inner._extra_initial_state()["report_config"], {})
        assert seeded["section_templates"]["incident"][0]["title"] == "Incident Summary"

    def test_a_malformed_section_template_is_dropped_rather_than_crashing_compile(self, monkeypatch):
        monkeypatch.setattr(
            "src.graph.graph.runtime_config",
            lambda: {"report": {"section_templates": {"incident": [{"key": "<script>"}]}}},
        )
        settings = ReportGenerationGraphNode()._parent_config()["configurable"]["report"]
        assert settings["section_templates"] == {}

    def test_a_template_path_that_walks_out_of_the_repo_is_dropped(self, monkeypatch):
        monkeypatch.setattr(
            "src.graph.graph.runtime_config",
            lambda: {"report": {"report_template_path": "../../etc/passwd"}},
        )
        settings = ReportGenerationGraphNode()._parent_config()["configurable"]["report"]
        assert settings["report_template_path"] == "config/admin_report_template.md"


class TestCallerContractCrossesTheGraphBoundary:
    def test_the_bridge_carries_the_contract_into_inner_state(self):
        """The framework passes only a string into a nested graph, so without the
        bridge the case data would never reach the report pipeline."""
        contract = {"report_type": "survey_summary", "subject": "Welfare survey", "fields": {}, "data_points": []}
        node = ReportGenerationGraphNode()
        from src.schemas.state import to_json as _to_json

        node.extract_input({"validated_input": "Welfare survey", "caller_contract": _to_json(contract)})
        inner = node.get_subgraph()
        seeded = from_json(inner._extra_initial_state()["caller_contract"], {})
        assert seeded["report_type"] == "survey_summary"

    def test_an_absent_contract_seeds_an_empty_mapping_rather_than_failing(self):
        set_caller_contract(None)
        seeded = from_json(DomainWorkflowGraph()._extra_initial_state()["caller_contract"], None)
        assert seeded == {}
