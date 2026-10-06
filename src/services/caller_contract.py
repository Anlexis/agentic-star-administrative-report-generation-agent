"""AgentCore Platform v1.0"""

# Caller-request contract for the administrative report generator.
#
# One module validates everything a caller can send, so there is exactly one
# answer to "what is accepted?" — the pre_process node calls into here and
# nothing downstream re-parses raw request data.
#
# Two request channels reach this module:
#   * the free-text field of a request, which may either be a plain narrative
#     note or a JSON object carrying the whole case payload;
#   * the structured invocation parameters carried by the framework, which may
#     carry the same case payload as real fields rather than as a string.
#
# Rules that hold for every field:
#   * numbers are parsed by a finite + bounded parser. NaN and the infinities
#     survive float() and every comparison against them is False, so an
#     unchecked non-finite figure would be summed into a report total and
#     rendered as "nan" with nothing in the log to say why;
#   * strings that end up in the rendered report are whitespace-collapsed and
#     length-capped, so caller text cannot open a new Markdown block and forge
#     report structure;
#   * labels and section keys are restricted to an inert alphabet;
#   * a value that fails any check REFUSES the request, naming the field but
#     never repeating the value;
#   * absent data is not an error — the report is simply sparser.

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_INPUT_CHARS = 40_000
MAX_SUBJECT_CHARS = 300
MAX_PERIOD_CHARS = 120
MAX_DEPARTMENT_CHARS = 160
MAX_FREE_TEXT_CHARS = 8_000
MAX_FIELDS = 40
MAX_FIELD_VALUE_CHARS = 4_000
MAX_DATA_POINTS = 200
MAX_DATA_POINT_KEYS = 20
MAX_DATA_POINT_TEXT_CHARS = 200
MAX_CONTEXT_DEPTH = 6

# Magnitude ceiling for any caller-supplied figure. Report totals are sums of
# these, and the ceiling keeps a total inside the range the renderer formats
# with thousands separators — which is also what keeps a long digit run from
# ever reaching the output gate's personal-data shapes.
NUMBER_MIN = -1e12
NUMBER_MAX = 1e12

# ── Inert alphabets ───────────────────────────────────────────────────────────
# Report type, section keys, field names and data-point keys are all rendered
# into the report and into audit payloads, so they are restricted rather than
# escaped.
_LABEL_RE = re.compile(r"^[a-z0-9_]{1,64}$")
# Field names arrive in officer spelling ("Response Rate"); the normalisation
# below is the documented route from that to the inert alphabet.
_LABEL_SEPARATORS_RE = re.compile(r"[\s\-/.]+")
_TEMPLATE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")

# Field names are caller-controlled too. One is repeated back in a refusal only
# when it is short, inert, and carries no disallowed pattern of its own.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\- ]{1,64}$")

_WHITESPACE_RE = re.compile(r"\s+")
# Zero-width and bidi controls: invisible in a rendered report, so they can hide
# a directive from a human reviewer while a model still reads it.
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Control characters (including the NUL that arrives in probe payloads) have no
# meaning in report prose and break renderers downstream.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Simple markup tags. Used ONLY to build a screening candidate — the stored text
# is never rewritten by it. A tag inserted mid-word hides a directive from a
# pattern while a renderer puts the word back together.
_MARKUP_RE = re.compile(r"</?[A-Za-z][^<>]{0,64}>")

# ── Resident personal-data shapes ─────────────────────────────────────────────
# ONE definition, used by both directions of the personal-data guarantee: the
# inbound strip that rewrites these shapes out of caller text, and the outbound
# gate that refuses to release anything still matching them. Two lists would
# drift, and the drift would always favour the leak.
#
# The dialling forms are separate patterns on purpose. A single "digits and
# separators" pattern either misses the international form (no leading zero) or
# matches ordinary figures; splitting them keeps each one anchored to a shape a
# telephone number actually has.
PERSONAL_DATA_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # The 12-digit resident identification number, contiguous or grouped 4-4-4,
    # and any longer unbroken digit run that could carry one.
    ("long_id_number", re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{2,11}\b")),
    ("international_phone_number", re.compile(r"\+\d{1,3}[-\s]\d{1,4}[-\s]\d{2,4}[-\s]\d{3,4}\b")),
    ("phone_number", re.compile(r"\b0\d{1,4}[-\s]\d{2,4}[-\s]\d{4}\b")),
)
REDACTION_STUB = "[REDACTED]"

# Field names whose VALUE is resident personal data whatever its shape. A name
# has no pattern, so the key is the only thing that can identify it.
PERSONAL_DATA_KEYS = frozenset(
    {
        "my_number",
        "mynumber",
        "individual_number",
        "resident_id",
        "resident_name",
        "name",
        "full_name",
        "applicant_name",
        "address",
        "home_address",
        "phone",
        "phone_number",
        "tel",
        "email",
        "e_mail",
        "date_of_birth",
        "dob",
        "birthday",
    }
)


def strip_direct_identifiers(text: str) -> str:
    """Replace direct-identifier shapes in free text with a fixed stub.

    Applied to every free-text field before it is stored, whichever channel it
    arrived on: the framework's own masking covers the request's free-text field
    only, so case notes arriving on the structured channel would otherwise skip
    it entirely.
    """
    for _name, pattern in PERSONAL_DATA_PATTERNS:
        text = pattern.sub(REDACTION_STUB, text)
    return text


def find_personal_data(text: str) -> Optional[str]:
    """Name the first resident personal-data shape present in a string, or None."""
    for name, pattern in PERSONAL_DATA_PATTERNS:
        if pattern.search(text):
            return name
    return None


# ── Disallowed-instruction screen ─────────────────────────────────────────────
# Chat-template control tokens are screened as a class. They are how a payload
# forges a turn boundary, they carry no meaning in administrative prose, and a
# screen written around directive phrases alone does not see them at all.
_CONTROL_TOKEN_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("chat_template_token", re.compile(r"<\|[^<>|]{0,64}\|>")),
    ("instruction_token", re.compile(r"\[/?INST\]", re.IGNORECASE)),
    ("system_token", re.compile(r"<</?SYS>>", re.IGNORECASE)),
)

# Instruction-shaped phrases. Every pattern requires a verb AND its object, so
# the surrounding prose has to actually be an instruction: a case note that
# merely mentions rules, guidance or a system does not match. Administrative
# reports quote regulations and complaint text verbatim, and a screen that fires
# on those refuses genuine work — the more damaging of the two failures.
_DIRECTIVE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "override_directive",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\s+"
            r"(?:all\s+|any\s+|the\s+|your\s+|these\s+|those\s+)*"
            r"(?:previous|prior|above|earlier|preceding|system|initial|safety)\s+"
            r"(?:instruction|rule|prompt|direction|guardrail|guideline)s?",
            re.IGNORECASE,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\b(?:you\s+are\s+now|pretend\s+to\s+be|act\s+as|behave\s+as|roleplay\s+as)\s+"
            r"(?:a|an|the)\s+"
            r"(?:system|assistant|language\s+model|ai\s+model|unrestricted|jailbroken|"
            r"admin(?:istrator)?|developer\s+mode|dan\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_disclosure",
        re.compile(
            r"\b(?:reveal|print|repeat|show|output|display|disclose)\s+(?:me\s+)?"
            r"(?:your|the)\s+"
            r"(?:(?:system|initial|original|hidden|full|exact|configured|underlying)\s+){1,3}"
            r"(?:prompt|instruction|rule)s?",
            re.IGNORECASE,
        ),
    ),
)

_ALL_SCREEN_PATTERNS = _CONTROL_TOKEN_PATTERNS + _DIRECTIVE_PATTERNS


class CallerDataError(ValueError):
    """A caller field failed its contract. Carries a field reference, never a value."""


def _reassembled(text: str) -> str:
    """What a reader sees once the inert runs between words are taken out.

    A strip is not a refusal, and it can make an attack HARDER to see: replacing
    a number with a stub, or a renderer dropping a markup tag, can turn a
    directive that no pattern matched into plain prose that reads perfectly.
    This candidate removes the redaction stub and simple markup tags and closes
    the resulting gaps, so the screen sees the re-assembled sentence.
    """
    without_stub = strip_direct_identifiers(text).replace(REDACTION_STUB, " ")
    return _WHITESPACE_RE.sub(" ", _MARKUP_RE.sub("", without_stub))


def screen_text(text: str) -> Optional[str]:
    """Name the first disallowed pattern in one string, or None.

    Screens the string four ways, and no pass subsumes another:
      * as received — control tokens have to be seen before any rewrite could
        consume them;
      * after the personal-data strip;
      * with zero-width and bidi characters removed — those are invisible to a
        human reviewer and transparent to a reader;
      * re-assembled — see _reassembled above.
    """
    for candidate in (text, strip_direct_identifiers(text), _INVISIBLE_RE.sub("", text), _reassembled(text)):
        for name, pattern in _ALL_SCREEN_PATTERNS:
            if pattern.search(candidate):
                return name
    return None


def _reference(parent: str, name: object, index: int) -> str:
    """Render a caller-supplied field name safe to repeat in a refusal."""
    if isinstance(name, str) and _SAFE_NAME_RE.match(name) and screen_text(name) is None:
        return f"{parent}.{name}"
    return f"{parent}[field #{index}]"


def screen_payload(value: object, reference: str = "input_context", depth: int = 0) -> Optional[Tuple[str, str]]:
    """Depth-first screen of a parsed payload; returns (pattern, field) or None.

    Mapping KEYS are screened as well as values: a payload delivered as JSON can
    write any pattern into a key, and \\u escapes make a scan of the raw request
    text unreliable — only a scan after parsing sees what the reader will see.
    Nesting is bounded so a pathologically nested payload cannot exhaust the
    stack before the per-field checks run.
    """
    if depth > MAX_CONTEXT_DEPTH:
        return ("nesting_depth", reference)
    if isinstance(value, str):
        hit = screen_text(value)
        return (hit, reference) if hit else None
    if isinstance(value, Mapping):
        for index, (key, item) in enumerate(value.items(), start=1):
            child = _reference(reference, key, index)
            if isinstance(key, str):
                hit = screen_text(key)
                if hit:
                    return (hit, child)
            found = screen_payload(item, child, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value, start=1):
            found = screen_payload(item, f"{reference}[{index}]", depth + 1)
            if found:
                return found
        return None
    return None


# ── Field parsers (every failure refuses the request) ─────────────────────────
def parse_number(
    value: object,
    *,
    field: str,
    minimum: float = NUMBER_MIN,
    maximum: float = NUMBER_MAX,
) -> float:
    """Parse a caller figure, or refuse.

    Rejects booleans (True is an int in Python), non-numeric text, NaN and the
    infinities, and anything outside the stated range. A non-finite value that
    reaches a comparison never raises — it makes every comparison False, so a
    minimum and a maximum computed over a column containing one are both wrong,
    and the total renders as "nan" with nothing in the log to explain it.
    """
    if isinstance(value, bool) or value is None:
        raise CallerDataError(f"{field} must be a number")
    if isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except (TypeError, ValueError):
            raise CallerDataError(f"{field} must be a number") from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise CallerDataError(f"{field} must be a number")
    if not math.isfinite(number):
        raise CallerDataError(f"{field} must be a finite number")
    if not minimum <= number <= maximum:
        raise CallerDataError(f"{field} must be between {minimum:g} and {maximum:g}")
    return number


def parse_label(value: object, *, field: str) -> str:
    """Parse an inert label (report type, section key, field name), or refuse.

    Officer spelling is normalised first — trimmed, lowercased, and internal
    spaces, hyphens, slashes and dots collapsed to underscores — so "Response
    Rate" becomes "response_rate" rather than being refused. Anything still
    outside the alphabet after that refuses the request: these strings are
    rendered into the report as section and row labels, so they are restricted
    rather than escaped.
    """
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    label = _LABEL_SEPARATORS_RE.sub("_", _INVISIBLE_RE.sub("", value).strip().lower())
    if not _LABEL_RE.match(label):
        raise CallerDataError(f"{field} must be 1-64 characters of letters, digits, spaces or underscores")
    return label


def parse_render_text(value: object, *, field: str, limit: int, required: bool = False) -> str:
    """Parse free text that will be rendered into the report, or refuse.

    Whitespace is collapsed to single spaces. That is the whole defence against
    caller text forging report structure: the report is Markdown, and a block
    element only starts at the beginning of a line, so text that cannot contain
    a newline cannot open a heading, a list item or a fenced block.
    """
    if value is None and not required:
        return ""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        value = f"{value}"
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    cleaned = _CONTROL_CHARS_RE.sub(" ", _INVISIBLE_RE.sub("", value))
    text = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not text:
        if required:
            raise CallerDataError(f"{field} must not be empty")
        return ""
    if len(text) > limit:
        raise CallerDataError(f"{field} must be at most {limit} characters")
    return strip_direct_identifiers(text)


def parse_template_path(value: object, *, field: str) -> str:
    """Parse a configured report-template path, or refuse.

    Restricted to an inert path alphabet with no parent-directory segment, so a
    misconfigured value cannot walk out of the repository.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CallerDataError(f"{field} must be text")
    path = value.strip()
    if not path:
        return ""
    if not _TEMPLATE_PATH_RE.match(path) or ".." in path or path.startswith("/"):
        raise CallerDataError(f"{field} must be a relative path of letters, digits, dots, dashes and slashes")
    return path


def _is_numeric_value(value: object) -> bool:
    """Is this value a figure rather than a label?

    A real number (but not a bool) always is. A string is treated as one when it
    parses as a float AT ALL — including "NaN" and "Infinity". That is the point:
    those parse, so they are numeric input, and routing them to the number parser
    is what makes them REFUSE rather than slip through as a text label and be
    silently excluded from the summary.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value.replace(",", "").strip())
        except (TypeError, ValueError):
            return False
        return True
    return False


def _parse_fields(value: object, *, field: str) -> Dict[str, str]:
    """Parse the case fields — the officer's narrative material, keyed by topic."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CallerDataError(f"{field} must be an object")
    if len(value) > MAX_FIELDS:
        raise CallerDataError(f"{field} accepts at most {MAX_FIELDS} entries")
    parsed: Dict[str, str] = {}
    for index, (key, item) in enumerate(value.items(), start=1):
        reference = _reference(field, key, index)
        label = parse_label(key, field=reference)
        if label in PERSONAL_DATA_KEYS:
            # A field named for resident personal data is dropped rather than
            # refused: the officer's report is still producible without it, and
            # a refusal would push callers towards renaming the field instead.
            parsed[label] = REDACTION_STUB
            continue
        parsed[label] = parse_render_text(item, field=reference, limit=MAX_FIELD_VALUE_CHARS)
    return parsed


def _parse_data_points(value: object, *, field: str) -> List[Dict[str, Any]]:
    """Parse the structured measurement records, or refuse.

    Each record is a flat mapping of inert keys to a finite figure or a short
    label-like string. Nested records are refused rather than flattened: the
    report renders one row per key, so a nested value has no rendering and
    accepting it would silently drop data the caller believes was reported.
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise CallerDataError(f"{field} must be a list of records")
    if len(value) > MAX_DATA_POINTS:
        raise CallerDataError(f"{field} accepts at most {MAX_DATA_POINTS} records")

    records: List[Dict[str, Any]] = []
    for index, entry in enumerate(value, start=1):
        reference = f"{field}[{index}]"
        if isinstance(entry, Mapping):
            if len(entry) > MAX_DATA_POINT_KEYS:
                raise CallerDataError(f"{reference} accepts at most {MAX_DATA_POINT_KEYS} keys")
            record: Dict[str, Any] = {}
            for key_index, (key, item) in enumerate(entry.items(), start=1):
                key_reference = _reference(reference, key, key_index)
                label = parse_label(key, field=key_reference)
                if label in PERSONAL_DATA_KEYS:
                    record[label] = REDACTION_STUB
                elif isinstance(item, bool):
                    # Neither a figure nor a label. Accepting one as the text
                    # "True" would silently reinterpret what the caller sent,
                    # and counting it as the figure 1 would silently corrupt the
                    # summary — so it refuses instead.
                    raise CallerDataError(f"{key_reference} must be a number or text, not a true/false flag")
                elif _is_numeric_value(item):
                    record[label] = parse_number(item, field=key_reference)
                else:
                    record[label] = parse_render_text(item, field=key_reference, limit=MAX_DATA_POINT_TEXT_CHARS)
            records.append(record)
        elif isinstance(entry, bool):
            raise CallerDataError(f"{reference} must be a number or text, not a true/false flag")
        elif _is_numeric_value(entry):
            records.append({"value": parse_number(entry, field=reference)})
        elif isinstance(entry, str):
            records.append({"value": parse_render_text(entry, field=reference, limit=MAX_DATA_POINT_TEXT_CHARS)})
        else:
            raise CallerDataError(f"{reference} must be a record, a number or text")
    return records


# ── The whole contract ────────────────────────────────────────────────────────
# Keys the caller may set on either channel. Anything else on the structured
# channel is screened and then ignored rather than refused: the hosting platform
# puts its own material there (conversation history, routing metadata), and
# refusing unknown keys would break every hosted deployment.
CONTRACT_KEYS: Sequence[str] = (
    "channel",
    "report_type",
    "subject",
    "period",
    "department",
    "fields",
    "data_points",
)


def build_caller_contract(user_input: object, input_context: object) -> Dict[str, Any]:
    """Screen, validate and normalise everything the caller sent.

    Recognised keys are CONTRACT_KEYS on either channel. Returns the validated
    contract. Raises CallerDataError naming the offending field — and only the
    field — when anything fails.
    """
    context: Mapping[str, Any] = input_context if isinstance(input_context, Mapping) else {}

    found = screen_payload(context, "input_context")
    if found:
        raise CallerDataError(f"{found[1]} contains a disallowed instruction pattern")

    text = user_input.strip() if isinstance(user_input, str) else ""
    if not text and not context:
        raise CallerDataError("input must not be empty")
    if len(text) > MAX_INPUT_CHARS:
        raise CallerDataError(f"input must be at most {MAX_INPUT_CHARS} characters")

    hit = screen_text(text) if text else None
    if hit:
        raise CallerDataError("input contains a disallowed instruction pattern")

    # The documented request shape is a JSON case payload in the free-text
    # field. Parsing it here means its fields go through the same checks as the
    # structured channel instead of being re-parsed, unchecked, downstream.
    envelope: Mapping[str, Any] = {}
    free_text = ""
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            raise CallerDataError("input looks like a JSON payload but did not parse") from None
        if not isinstance(decoded, Mapping):
            raise CallerDataError("input must be a case payload object or narrative text")
        envelope = decoded
        found = screen_payload(envelope, "input")
        if found:
            raise CallerDataError(f"{found[1]} contains a disallowed instruction pattern")
        free_text = parse_render_text(envelope.get("free_text"), field="input.free_text", limit=MAX_FREE_TEXT_CHARS)
    elif text:
        # Narrative input: the whole note becomes the report's background
        # material and its opening becomes the subject.
        free_text = parse_render_text(text, field="input", limit=MAX_FREE_TEXT_CHARS, required=True)

    def pick(key: str) -> object:
        """The structured channel wins over the payload — one documented precedence."""
        if key in context and context.get(key) is not None:
            return context.get(key)
        return envelope.get(key)

    contract: Dict[str, Any] = {
        "channel": "",
        "report_type": "general",
        "subject": "",
        "period": "",
        "department": "",
        "fields": {},
        "data_points": [],
        "free_text": free_text,
        "missing": [],
    }

    channel = pick("channel")
    if channel is not None:
        contract["channel"] = parse_label(channel, field="input_context.channel")

    report_type = pick("report_type")
    if report_type is not None:
        contract["report_type"] = parse_label(report_type, field="input_context.report_type")

    subject = pick("subject")
    if subject is not None:
        contract["subject"] = parse_render_text(subject, field="input_context.subject", limit=MAX_SUBJECT_CHARS)
    elif free_text:
        contract["subject"] = free_text[:MAX_SUBJECT_CHARS].strip()

    period = pick("period")
    if period is not None:
        contract["period"] = parse_render_text(period, field="input_context.period", limit=MAX_PERIOD_CHARS)

    department = pick("department")
    if department is not None:
        contract["department"] = parse_render_text(
            department, field="input_context.department", limit=MAX_DEPARTMENT_CHARS
        )

    contract["fields"] = _parse_fields(pick("fields"), field="input_context.fields")
    contract["data_points"] = _parse_data_points(pick("data_points"), field="input_context.data_points")

    # Recorded, not refused: a report is still producible without them, and the
    # note travels into the report's own intake notes so the reader knows why a
    # header line is generic.
    missing: List[str] = []
    if pick("report_type") is None:
        missing.append("report_type")
    if pick("subject") is None and not free_text:
        missing.append("subject")
    contract["missing"] = missing
    return contract


__all__ = [
    "CONTRACT_KEYS",
    "CallerDataError",
    "MAX_DATA_POINTS",
    "MAX_FIELDS",
    "MAX_INPUT_CHARS",
    "NUMBER_MAX",
    "NUMBER_MIN",
    "PERSONAL_DATA_KEYS",
    "PERSONAL_DATA_PATTERNS",
    "REDACTION_STUB",
    "build_caller_contract",
    "find_personal_data",
    "parse_label",
    "parse_number",
    "parse_render_text",
    "parse_template_path",
    "screen_payload",
    "screen_text",
    "strip_direct_identifiers",
]
