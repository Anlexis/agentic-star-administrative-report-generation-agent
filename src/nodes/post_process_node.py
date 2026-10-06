"""AgentCore Platform v1.0"""

# PostProcessNode — the output boundary of the agent.
#
# The stated output invariant of this template is the one printed at the foot of
# every report it generates: no resident personal data is stored or reproduced,
# and nothing credential-shaped leaves the agent. This node enforces that on the
# whole released surface — the rendered report AND the operational fields the
# deployment monitors — with the module-level scan below, called from execute().
#
# The scan is RECURSIVE. Section plans and intake notes are structured values,
# and a gate that only looked at top-level strings would report zero findings on
# a payload whose leak sits one level down.
#
# Two independent layers, each with its own audit event:
#   * inbound — the request boundary rewrites personal-data shapes out of caller
#     text before it is ever stored (src/services/caller_contract.py);
#   * outbound — this gate refuses to release anything still matching. Both
#     directions read ONE pattern definition, so they cannot drift apart.
#
# Credential shapes are checked with the FRAMEWORK's own detector rather than a
# local pattern list. A local list that is narrower anywhere is a containment
# bypass: the framework raises on a value it catches and this node misses, the
# node wrapper turns that into a bare error result, and the clearing below never
# runs. Using the same detector makes the two sets identical by construction.
#
# Containment on violation: returning an error is not enough on its own. The
# framework's output envelope falls back to state["result"] whatever the status,
# so a gate that raised — or that set an error status without clearing the
# fields — would still ship the un-gated report inside the error envelope. This
# node therefore CLEARS every output-bearing field as it blocks.
#
# No _extra_security_gate_input/_output methods are defined here: the framework
# auto-wraps such hooks, and defining them would change the node's call pipeline.

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event
from src.services.caller_contract import find_personal_data
from src.services.llm_factory import resolve_llm
from src.services.llm_review import render_review, review_result

logger = logging.getLogger(__name__)

# Credential shapes the framework detector does not carry. These WIDEN the
# framework set; they never replace it, so this gate can only ever block more
# than the framework does — which is the property that keeps the framework from
# raising on something this node let through.
_EXTRA_CREDENTIAL_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # Publishable/anonymous key prefixes alongside the secret one the framework
    # already knows.
    ("api_key", re.compile(r"\b(?:pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    # A secret written as an assignment rather than as a recognisable key shape.
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]

_BLOCKED_NOTICE = (
    "[OUTPUT BLOCKED — disallowed content detected in the generated report. "
    "Remove credential-like strings and resident personal data from the "
    "submitted case data and retry.]"
)

# Every state field that can carry released text. On a violation each one is
# overwritten, so no path out of the graph — including the framework's own
# fallback to state["result"] — can reach the un-gated report.
_OUTPUT_BEARING_FIELDS = (
    "result",
    "formatted_output",
    "administrative_report",
    "section_plan",
    "intake_notes",
)


def security_gate_output(content: Any) -> Optional[str]:
    """Scan released content and name the first violation, or None if clean.

    Walks nested mappings and sequences, scanning every leaf. Returns the name
    of the matched pattern — never the matched text, which would put the leak
    into the log that reports it.
    """
    if content is None:
        return None
    if isinstance(content, dict):
        for value in content.values():
            hit = security_gate_output(value)
            if hit:
                return hit
        return None
    if isinstance(content, (list, tuple)):
        for item in content:
            hit = security_gate_output(item)
            if hit:
                return hit
        return None
    text = str(content)
    findings = detect_credentials(text)
    if findings:
        return str(findings[0]["type"])
    for name, pattern in _EXTRA_CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return name
    return find_personal_data(text)


class PostProcessNode(FunctionNode):
    """Output gate: refuse to release credential-like or resident personal data."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        result = state.get("result") or ""

        if not str(result).strip():
            # Nothing was generated — there is nothing to gate.
            return {
                "formatted_output": result,
                "status": AgentStatus.SUCCESS.value,
            }

        # The operational fields are released into the same envelope as the
        # report, so they are gated with it rather than after it.
        _llm, _ = resolve_llm(None, state)
        _remarks = review_result(
            _llm,
            user_input=str(state.get("user_input") or ""),
            result=result,
            domain="GOV AdministrativeReportGeneratorAgent",
        )
        _review = render_review(_remarks)
        # Remarks are LLM text derived from the caller's raw words, so they pass through the
        # same gate the answer does -- appending after the gate would put unscanned text past
        # it. A tripped review is dropped on its own: withholding a correct answer because an
        # advisory remark quoted an identifier would let the review change the outcome, and
        # the whole design rests on it being unable to.
        if _review and isinstance(result, str) and not security_gate_output({"result": str(result + _review)}):
            result = result + _review

        released = {
            "result": str(result),
            "section_plan": state.get("section_plan"),
            "intake_notes": state.get("intake_notes"),
        }
        violation = security_gate_output(released)
        if violation:
            logger.error("PostProcessNode: output blocked — violation type: %s", violation)
            emit_trace_event("admin_report_blocked", {"violation": violation}, state)
            blocked: Dict[str, Any] = {field: None for field in _OUTPUT_BEARING_FIELDS}
            blocked.update(
                {
                    "formatted_output": _BLOCKED_NOTICE,
                    "result": _BLOCKED_NOTICE,
                    "status": AgentStatus.ERROR.value,
                    "error_log": [f"PostProcessNode: output blocked — disallowed content detected ({violation})"],
                }
            )
            return blocked

        emit_trace_event(
            "admin_report_emitted",
            {"report_chars": len(str(result))},
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS.value,
        }
