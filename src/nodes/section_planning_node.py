"""AgentCore Platform v1.0"""

# GOV-C2-007 — SectionPlanningNode
# Domain step 3: resolve the ordered list of report sections to render against
# the configured section template (background, findings, recommendations, next
# actions), and bind each section to the facts that feed it.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).
#
# Configuration reaches this node through seeded state (`report_config`), not
# through a per-call config parameter: a domain node's contract is
# execute(self, state) -> dict, so state seeding is the only route a declared
# value can travel from config/config.yaml into the workflow.

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Built-in section set, used whenever no template is configured for the report
# type. Each entry: key, title, required flag, and the fact buckets that feed it.
_DEFAULT_SECTION_TEMPLATE: List[Dict[str, Any]] = [
    {
        "key": "background",
        "title": "Background / 背景",
        "required": True,
        "source_facts": ["background"],
    },
    {
        "key": "findings",
        "title": "Findings / 調査結果",
        "required": True,
        "source_facts": ["findings", "metrics"],
    },
    {
        "key": "recommendations",
        "title": "Recommendations / 提言",
        "required": True,
        "source_facts": ["recommendations_source", "findings"],
    },
    {
        "key": "next_actions",
        "title": "Next Actions / 今後の対応",
        "required": True,
        "source_facts": ["recommendations_source"],
    },
]


class SectionPlanningNode(FunctionNode):
    """Resolve the report section plan against the configured section template.

    Input state keys:
        case_record:     report_type drives template selection
        extracted_facts: marks which sections actually have source material
        report_config:   live settings seeded from config/config.yaml

    Output state keys (partial dict):
        section_plan: ordered list of sections to render
    """

    # The manifest's declared caller level. The request boundary
    # (PreProcessNode) refuses anything below it before this node is
    # reachable, so this is defence in depth rather than the primary gate —
    # and declaring a HIGHER level here would refuse the declared caller and
    # make the public path unreachable.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        case_record: Dict[str, Any] = from_json(state.get("case_record"), {}) or {}
        extracted_facts: Dict[str, Any] = from_json(state.get("extracted_facts"), {}) or {}
        report_config: Dict[str, Any] = from_json(state.get("report_config"), {}) or {}

        report_type = str(case_record.get("report_type", "general"))
        templates = report_config.get("section_templates")
        template_map: Dict[str, Any] = templates if isinstance(templates, dict) else {}
        configured = template_map.get(report_type)
        base_template = configured if isinstance(configured, list) and configured else _DEFAULT_SECTION_TEMPLATE

        section_plan: List[Dict[str, Any]] = []
        for section in base_template:
            key = section.get("key")
            source_facts = section.get("source_facts", [])
            # A section "has content" when any of its fact buckets is non-empty.
            has_content = any(bool(extracted_facts.get(fact_key)) for fact_key in source_facts)
            section_plan.append(
                {
                    "key": key,
                    "title": section.get("title", str(key).replace("_", " ").title()),
                    "required": bool(section.get("required", True)),
                    "source_facts": source_facts,
                    "has_content": has_content,
                }
            )

        emit_trace_event(
            "section_plan_resolved",
            {
                "report_type": report_type,
                "section_keys": [s["key"] for s in section_plan],
                "template_source": "configured" if base_template is configured else "built_in",
            },
            state,
        )

        logger.info(
            "SectionPlanningNode: report_type=%s, %d sections planned",
            report_type,
            len(section_plan),
        )

        return {
            "section_plan": to_json(section_plan),
        }
