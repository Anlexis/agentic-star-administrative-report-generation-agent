"""AgentCore Platform v1.0"""

# GOV-C2-007 — ReportDraftingNode
# Domain step 4: draft the narrative prose for each planned section from the
# extracted facts.
#
# The synthesis is deterministic and rule-based: it composes prose from the
# structured facts upstream produced, so the same case data always yields the
# same report and the whole pipeline is testable without a model. Where a
# deployment wants model-authored prose, `_draft_section` is the single function
# to replace — every other step keeps its contract.
#
# No resident personal data is reintroduced here: only the already-stripped
# extracted facts feed the draft.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)


def _format_number(value: Any) -> str:
    """Render a figure with thousands separators.

    Separators are not only presentation. An unbroken run of ten or more digits
    has the shape of a resident identification number, which the output gate
    refuses to release — so an ungrouped total would make a legitimate report
    fail its own boundary check. Grouping keeps every rendered figure below the
    four-consecutive-digit threshold that shape needs.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _format_fact_lines(items: List[Any]) -> str:
    """Render a list of fact strings as Markdown bullet lines."""
    lines = []
    for item in items:
        text = item if isinstance(item, str) else str(item)
        text = text.strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _format_metrics(metrics: Dict[str, Any]) -> str:
    """Render the quantitative summary as a compact Markdown list."""
    if not metrics:
        return ""
    lines = []
    for key, value in metrics.items():
        label = str(key).replace("_", " ").title()
        lines.append(f"- {label}: {_format_number(value)}")
    return "\n".join(lines)


def _draft_background(facts: Dict[str, Any], subject: str) -> str:
    body = _format_fact_lines(facts.get("background", []))
    header = (
        f"This report concerns {subject}. "
        if subject
        else "This report summarizes the administrative matter under review. "
    )
    header += (
        "The background and scope are recorded below based on the case data " "submitted by the responsible office."
    )
    return header + ("\n\n" + body if body else "")


def _draft_findings(facts: Dict[str, Any]) -> str:
    body = _format_fact_lines(facts.get("findings", []))
    metrics_block = _format_metrics(facts.get("metrics", {}))
    parts = ["The following findings were identified from the submitted data:"]
    if body:
        parts.append(body)
    else:
        parts.append("- No specific findings were recorded in the input.")
    if metrics_block:
        parts.append("\nQuantitative summary:")
        parts.append(metrics_block)
    return "\n\n".join(parts)


def _draft_recommendations(facts: Dict[str, Any]) -> str:
    body = _format_fact_lines(facts.get("recommendations_source", []))
    if body:
        return f"Based on the findings above, the following measures are recommended:\n\n{body}"
    return (
        "Based on the findings above, the responsible office should review the "
        "identified matters and determine appropriate corrective measures."
    )


def _draft_next_actions(facts: Dict[str, Any]) -> str:
    if facts.get("recommendations_source"):
        return (
            "The responsible office will operationalize the recommended measures, "
            "assign owners and deadlines, and report progress at the next review "
            "cycle."
        )
    return (
        "The responsible office will confirm the findings, define the next steps "
        "with assigned owners and target dates, and schedule a follow-up review."
    )


# Section key -> deterministic drafter dispatch table.
_SECTION_DRAFTERS = {
    "findings": _draft_findings,
    "recommendations": _draft_recommendations,
    "next_actions": _draft_next_actions,
}


def _draft_section(key: str, facts: Dict[str, Any], subject: str) -> str:
    """Produce the narrative draft for one section."""
    if key == "background":
        return _draft_background(facts, subject)
    drafter = _SECTION_DRAFTERS.get(key)
    if drafter is not None:
        return drafter(facts)
    # A section the built-in drafters do not know — configured section templates
    # may name one — falls back to a neutral dump of its own fact bucket.
    generic = _format_fact_lines(facts.get(key, []))
    return generic or "(No content available for this section.)"


class ReportDraftingNode(FunctionNode):
    """Draft narrative prose for each planned section (deterministic synthesis).

    Input state keys:
        section_plan:    ordered sections to draft (from SectionPlanningNode)
        extracted_facts: structured facts per section (from DataExtractionNode)
        case_record:     subject used to frame the background section

    Output state keys (partial dict):
        section_drafts: {section_key: drafted_prose} (no resident personal data)
    """

    # The manifest's declared caller level. The request boundary
    # (PreProcessNode) refuses anything below it before this node is
    # reachable, so this is defence in depth rather than the primary gate —
    # and declaring a HIGHER level here would refuse the declared caller and
    # make the public path unreachable.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        section_plan: List[Dict[str, Any]] = from_json(state.get("section_plan"), []) or []
        extracted_facts: Dict[str, Any] = from_json(state.get("extracted_facts"), {}) or {}
        case_record: Dict[str, Any] = from_json(state.get("case_record"), {}) or {}
        subject = str(case_record.get("subject", ""))

        section_drafts: Dict[str, str] = {}
        for section in section_plan:
            key = section.get("key")
            if not key:
                continue
            section_drafts[key] = _draft_section(key, extracted_facts, subject)

        emit_trace_event(
            "section_drafts_generated",
            {"drafted_sections": list(section_drafts.keys())},
            state,
        )

        logger.info("ReportDraftingNode: drafted %d sections", len(section_drafts))

        return {
            "section_drafts": to_json(section_drafts),
        }
