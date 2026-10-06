"""AgentCore Platform v1.0"""

# GOV-C2-007 — DataExtractionNode
# Domain step 2: extract structured facts from the case record and organise them
# by report section (background / findings / recommendation source material),
# aggregating the measurement records into a quantitative summary.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).
#
# Every figure reaching this node has already been through the finite + bounded
# parser at the request boundary. The guard below is the second half of that
# rule rather than a duplicate of it: an aggregate is only as finite as its
# inputs, and a single non-finite value would make the minimum, the maximum and
# the average all wrong at once while raising nothing.

import logging
import math
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.caller_contract import NUMBER_MAX, NUMBER_MIN

logger = logging.getLogger(__name__)

# Field-name hints that route a case field to a report section.
_BACKGROUND_FIELD_HINTS = {"background", "context", "purpose", "objective", "scope"}
_FINDINGS_FIELD_HINTS = {"findings", "results", "observations", "outcome", "issues"}
_RECOMMENDATION_FIELD_HINTS = {"recommendation", "recommendations", "proposal", "measures"}

MAX_BACKGROUND_FREE_TEXT_CHARS = 1000


def _as_number(value: Any) -> Optional[float]:
    """Read a value as a figure without judging it, or None when it is not one.

    Booleans are excluded explicitly: True is an int in Python, so an unguarded
    numeric check counts a flag as the figure 1. A numeric STRING is a figure —
    including "NaN" and "Infinity", which is the point: they have to be seen as
    numeric input for the finiteness check below to reject them, rather than be
    mistaken for a text label and quietly dropped from the summary.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except (TypeError, ValueError):
            return None
    return None


def _finite_in_range(value: Any) -> Optional[float]:
    """Return the value as a float when it is a real, finite, in-range figure.

    NaN and the infinities parse fine and then compare False against everything,
    which is why they are rejected here rather than left to a later comparison:
    a single one of them makes the minimum, the maximum and the average of a
    column all wrong at once, and raises nothing.
    """
    number = _as_number(value)
    if number is None or not math.isfinite(number):
        return None
    if not NUMBER_MIN <= number <= NUMBER_MAX:
        return None
    return number


class DataExtractionNode(FunctionNode):
    """Bucket case facts by report section and aggregate the measurement records.

    Input state keys:
        case_record: case record (from IntakeValidationNode)

    Output state keys (partial dict):
        extracted_facts: {"background": [...], "findings": [...],
                          "recommendations_source": [...], "metrics": {...},
                          "data_points": [...]}
    """

    # The manifest's declared caller level. The request boundary
    # (PreProcessNode) refuses anything below it before this node is
    # reachable, so this is defence in depth rather than the primary gate —
    # and declaring a HIGHER level here would refuse the declared caller and
    # make the public path unreachable.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        case_record: Dict[str, Any] = from_json(state.get("case_record"), {}) or {}
        fields: Dict[str, Any] = case_record.get("fields") or {}
        data_points: List[Any] = case_record.get("data_points") or []
        free_text: str = case_record.get("free_text") or ""

        background: List[str] = []
        findings: List[str] = []
        recommendations_source: List[str] = []

        # Bucket case fields into report sections by name hint.
        for key, value in fields.items():
            key_l = str(key).lower()
            text = f"{key}: {value}"
            if any(hint in key_l for hint in _BACKGROUND_FIELD_HINTS):
                background.append(text)
            elif any(hint in key_l for hint in _FINDINGS_FIELD_HINTS):
                findings.append(text)
            elif any(hint in key_l for hint in _RECOMMENDATION_FIELD_HINTS):
                recommendations_source.append(text)
            else:
                # Unclassified fields contribute to findings as raw observations.
                findings.append(text)

        # Subject and period always seed the background.
        subject = case_record.get("subject", "")
        period = case_record.get("period", "")
        if subject:
            background.insert(0, f"Subject: {subject}")
        if period:
            background.insert(1 if subject else 0, f"Reporting period: {period}")
        if free_text and not fields:
            background.append(free_text[:MAX_BACKGROUND_FREE_TEXT_CHARS])

        # Aggregate the measurement records into a quantitative summary.
        numeric_values: List[float] = []
        normalized_points: List[Dict[str, Any]] = []
        non_finite_seen = 0
        for point in data_points:
            if isinstance(point, dict):
                normalized_points.append(point)
                for item in point.values():
                    number = _finite_in_range(item)
                    if number is not None:
                        numeric_values.append(number)
                    elif _as_number(item) is not None:
                        non_finite_seen += 1
            else:
                number = _finite_in_range(point)
                if number is not None:
                    numeric_values.append(number)
                    normalized_points.append({"value": number})
                elif _as_number(point) is not None:
                    non_finite_seen += 1
                else:
                    normalized_points.append({"value": point})

        metrics: Dict[str, Any] = {"data_point_count": len(data_points)}
        if numeric_values:
            metrics["numeric_count"] = len(numeric_values)
            metrics["numeric_total"] = round(math.fsum(numeric_values), 4)
            metrics["numeric_average"] = round(math.fsum(numeric_values) / len(numeric_values), 4)
            metrics["numeric_min"] = round(min(numeric_values), 4)
            metrics["numeric_max"] = round(max(numeric_values), 4)
        if non_finite_seen:
            # Excluded rather than summed. Reported as a count so the reader can
            # see the summary was computed over fewer figures than were sent.
            metrics["excluded_non_finite_count"] = non_finite_seen

        extracted_facts: Dict[str, Any] = {
            "background": background,
            "findings": findings,
            "recommendations_source": recommendations_source,
            "metrics": metrics,
            "data_points": normalized_points,
        }

        emit_trace_event(
            "facts_extracted",
            {
                "background_items": len(background),
                "finding_items": len(findings),
                "metric_keys": sorted(metrics.keys()),
            },
            state,
        )

        logger.info(
            "DataExtractionNode: %d background, %d findings, %d records",
            len(background),
            len(findings),
            len(normalized_points),
        )

        return {
            "extracted_facts": to_json(extracted_facts),
        }
