"""Outbound message validation gate.

Checks every outbound Milo message for system internals leaking into
user-facing coaching messages. Three detection categories:

1. Machine output: JSON blobs, SQL, stack traces, log lines, HTTP errors
2. Internal vocabulary: DB columns, API paths, service names, Python identifiers
3. Structural anomalies: diagnostic dumps, system health reports

This is a deterministic filter (no LLM calls). The vocabulary list grows
over time as new leak patterns are discovered.
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("health-engine.outbound_gate")

# --- Detection patterns ---

# JSON blob: { followed by "key": or 'key': within ~50 chars
_JSON_BLOB = re.compile(r'\{[\s\S]{0,50}"[a-z_]+"\s*:', re.IGNORECASE)

# Stack traces
_STACK_TRACE = re.compile(r'Traceback \(most recent call last\)|File ".*", line \d+')

# Python errors: common exception class names
_PYTHON_ERROR = re.compile(
    r'(?:ModuleNotFoundError|ImportError|AttributeError|KeyError|TypeError'
    r'|ValueError|RuntimeError|FileNotFoundError|ConnectionError'
    r'|TimeoutError|PermissionError|OSError):'
)

# SQL fragments: require SELECT/INSERT/UPDATE/DELETE as anchor, not just FROM/WHERE
_SQL_FRAGMENT = re.compile(
    r'\b(?:SELECT|INSERT INTO|UPDATE|DELETE FROM|CREATE TABLE|DROP TABLE|ALTER TABLE)\b'
    r'.*\b(?:FROM|WHERE|INTO|SET|VALUES)\b',
    re.IGNORECASE,
)

# Log lines: ISO timestamp + level
_LOG_LINE = re.compile(
    r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}.*\b(?:INFO|WARNING|ERROR|DEBUG|CRITICAL)\b'
)

# HTTP error codes in error context
_HTTP_ERROR = re.compile(
    r'\b(?:4\d{2}|5\d{2})\s+(?:Internal Server Error|Not Found|Forbidden'
    r'|Bad Request|Unauthorized|Service Unavailable|Bad Gateway'
    r'|Gateway Timeout)\b',
    re.IGNORECASE,
)

_MACHINE_OUTPUT_PATTERNS = [
    (_JSON_BLOB, "json_blob"),
    (_STACK_TRACE, "stack_trace"),
    (_PYTHON_ERROR, "python_error"),
    (_SQL_FRAGMENT, "sql_fragment"),
    (_LOG_LINE, "log_line"),
    (_HTTP_ERROR, "http_error"),
]

# Internal vocabulary that should never appear in coaching messages.
# Lowercase for case-insensitive matching. Each entry is (term, is_word_boundary).
# is_word_boundary=True means match as whole word; False means substring.
_INTERNAL_TERMS = [
    # Database internals
    ("wearable_token", True),
    ("wearable_daily", True),
    ("person_id", True),
    ("user_id", True),
    ("channel_target", True),
    ("conversation_message", True),
    ("focus_plan", True),
    ("supplement_log", True),
    ("medication_log", True),
    ("health_engine_user_id", True),
    # API paths
    ("/health/deep", False),
    ("/api/v1/", False),
    ("/api/ingest_message", False),
    # Services and infrastructure
    ("gunicorn", True),
    ("launchd", True),
    ("uvicorn", True),
    ("cloudflare tunnel", True),
    # openclaw: removed from hard block. Users may see "OpenClaw" in
    # troubleshooting context (e.g. "WhatsApp listener needs reconnecting").
    # Diagnostic leaks are caught by co-occurring terms (cron, remediation,
    # gateway status, etc.).
    # Python identifiers
    ("_get_db", False),
    ("_get_daily_snapshot", False),
    ("sync_garmin_tokens", False),
    ("_compose_message", False),
    ("_send_via_openclaw", False),
    ("init_db", False),
    # Python literals: only flag in code context (returned/is/= prefix).
    # Case-sensitive: Python "True"/"False"/"None" are capitalized.
    # "that's true" (lowercase) is natural language, not a code leak.
    # System diagnostic keywords
    ("auto-remediation", True),
    ("remediation", True),
    ("cron re-triggered", False),
    ("stale (>", False),
    # System health check messages (leaked 5x on April 5)
    ("system health check", False),
    ("action needed", False),
    ("briefing stale", False),
    ("threshold 72h", False),
    ("threshold 48h", False),
    ("stuck 7 days", False),
    # Agent process narration (leaked 10+ times on April 5)
    ("human judgment needed", False),
    ("delivered to andrew", False),
    ("delivered to paul", False),
    ("delivered to mike", False),
    ("delivered to grigoriy", False),
    ("reading from disk", False),
    ("logging the rest in parallel", False),
    ("all on disk", False),
]

# Compile word-boundary patterns for efficiency
_INTERNAL_PATTERNS = []
for term, word_boundary in _INTERNAL_TERMS:
    if word_boundary:
        _INTERNAL_PATTERNS.append(re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE))
    else:
        _INTERNAL_PATTERNS.append(re.compile(re.escape(term), re.IGNORECASE))

# Python literals: case-SENSITIVE. "returned None" is code, "none of" is English.
_PYTHON_LITERAL_PATTERNS = [
    re.compile(r'(?:returned|is|=)\s*None'),
    re.compile(r'(?:returned|is|=)\s*True'),
    re.compile(r'(?:returned|is|=)\s*False'),
]

# Allowed terms that look internal but are user-facing.
# Strip full URLs (including query params like user_id=X, token=X).
_ALLOWLIST = re.compile(
    r'https?://dashboard\.mybaseline\.health\S*'
    r'|https?://auth\.mybaseline\.health\S*',
)


@dataclass
class ValidationResult:
    """Result of outbound message validation."""
    ok: bool = True
    flags: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


def validate_outbound(message: str) -> ValidationResult:
    """Validate an outbound coaching message for system internal leaks.

    Returns a ValidationResult with ok=True if the message is clean,
    or ok=False with flags and details describing what was found.
    """
    if not message or not message.strip():
        return ValidationResult()

    result = ValidationResult()

    # Category 0: Structural sanity — messages under 15 chars are process narration
    # ("On it.", "Logged.", "All on disk." etc.) Real coaching is 50+ chars.
    stripped = message.strip()
    if 0 < len(stripped) < 15:
        result.ok = False
        result.flags.append("too_short")
        result.details.append(f"too_short:{len(stripped)}_chars")
        return result

    # Strip allowlisted content before checking
    cleaned = _ALLOWLIST.sub("", message)

    # Category 1: Machine output
    for pattern, name in _MACHINE_OUTPUT_PATTERNS:
        if pattern.search(cleaned):
            result.ok = False
            result.flags.append("machine_output")
            result.details.append(f"machine_output:{name}")
            break  # One machine output flag is enough

    # Category 2: Internal vocabulary
    for pattern in _INTERNAL_PATTERNS:
        if pattern.search(cleaned):
            match = pattern.search(cleaned)
            result.ok = False
            result.flags.append("internal_vocabulary")
            result.details.append(f"internal_vocabulary:{match.group()}")
            break  # One internal vocab flag is enough

    # Category 2b: Python literals (case-sensitive, checked separately)
    if result.ok:  # Only if not already flagged
        for pattern in _PYTHON_LITERAL_PATTERNS:
            if pattern.search(cleaned):
                match = pattern.search(cleaned)
                result.ok = False
                result.flags.append("internal_vocabulary")
                result.details.append(f"internal_vocabulary:{match.group()}")
                break

    # Deduplicate flags
    result.flags = list(dict.fromkeys(result.flags))

    return result
