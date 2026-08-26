"""SemanticFrame interpretation pipeline — the single NL-understanding engine.

Called directly by agent/universal_gateway.py — the gateway is the only
classifier in the request pipeline; nothing downstream re-interprets the
message or re-grounds entities.

Turns one user message into a single grounded SemanticFrame:
    action detection first  → typed role parsing → reference resolution →
    entity grounding        → capability completeness check.

Design invariants:
  * Entity grounding only accepts tokens that match live per-user metadata,
    are fresh names a create/drop/rename capability explicitly allows, or are
    explicit CREATE_TABLE column definitions. It never invents entities.
  * A grounded frame that fails a capability's required-role contract becomes
    a structured clarification — never a silent fallback.
  * Raw SQL is one gateway-derived capability, never a pre-gateway shortcut.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from models.schemas import (
    SemanticFrame, ColumnSpec, AlterSpec, ChartSpec, SampleDataSpec,
    QueryFilter, QueryOrdering, QueryAggregation,
    QueryIntent, QueryEntities, QueryConstraints, UnderstandingResult,
)
from agent.capabilities import capability_for, get_capability
from agent import grounding as G
from agent import capability_check
from agent import context_resolution

STATUS_SUCCESS = "SUCCESS"
STATUS_CLARIFY = "NEEDS_CLARIFICATION"
STATUS_FAILED = "UNDERSTANDING_FAILED"


@dataclass
class FrameResult:
    frame: SemanticFrame
    status: str
    capability_id: Optional[str] = None
    clarification_type: Optional[str] = None
    clarification_message: Optional[str] = None
    reason: str = ""


ACTION_WORDS = {
    "create", "make", "build", "define", "setup", "set", "construct",
    "prepare", "generate", "new",
    "add", "insert", "append",
    "alter", "modify", "change", "rename", "update",
    "switch", "use", "connect", "show", "display", "list", "describe", "explain",
    "plot", "chart", "graph", "visualize",
    "drop", "delete", "remove", "destroy",
    "give", "tell", "get", "see", "select", "filter", "group", "count",
}

# Value-assignment language ("give Alice a raise", "give it a discount") that
# looks like a read request ("give me the total sales") only on the surface —
# there is no update/delete-row capability in CAPABILITIES yet, so these must
# fail into a clarification rather than be silently answered with a SELECT
# (or, for delete/remove, mistaken for dropping the whole table).
_VALUE_CHANGE_WORDS = {"raise", "bonus", "discount", "promotion", "increment", "increase", "decrease"}

LEADING_POLITE = {
    "please", "can", "could", "would", "i'd", "i", "want", "need", "to",
    "you", "let", "us", "help", "me", "like", "kindly",
}

GREETINGS = {
    "hello", "hi", "hey", "heyaa", "heyy", "heyya", "hola", "hiya", "howdy",
    "yo", "namaste", "greetings", "morning", "evening", "afternoon", "bye",
    "goodbye", "thanks", "thank", "thx", "ty", "okay", "ok", "sure",
}

CHART_WORDS = {
    "pie", "bar", "line", "scatter", "area", "donut", "histogram", "box",
    "funnel", "gauge", "radar",
}

# Generic chart-request nouns — distinct from CHART_WORDS (specific chart
# *types*). A message can request a chart without naming a type at all
# ("create a chart of sales"), so callers check both sets together.
_GENERIC_CHART_WORDS = {"chart", "graph", "visualization", "visualisation"}


def _is_chart_request(tokens: List[str]) -> bool:
    return any(t in CHART_WORDS or t in _GENERIC_CHART_WORDS for t in tokens)

_DDL_OPENERS = frozenset({"create", "alter", "drop", "rename"})

# SQL/database terminology — a closed set of computer-science terms this
# assistant needs to recognize as "explain a concept" rather than "run a
# query", the same defensible class as GREETINGS/CHART_WORDS above (not
# business/domain vocabulary tied to any particular connected schema).
_SQL_CONCEPT_WORDS = {
    "sql", "query", "queries", "foreign", "primary", "normalize",
    "normalization", "constraint", "constraints", "join", "joins", "index",
    "indexes", "transaction", "transactions", "aggregate", "aggregation",
    "schema", "relational", "database", "denormalize", "trigger", "triggers",
    "view", "views", "cascade", "orm",
}

# English number words — a language constant (not domain/business data), kept
# static on the same footing as GREETINGS/CHART_WORDS above.
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "dozen": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}

# Superlative *forms* — closed set of English grammatical constructs, not
# business vocabulary. The attribute they apply to ("paid", "cost", ...) is
# resolved against the real schema via grounding.resolve_attribute_word,
# never a hardcoded word->column map.
_SUPERLATIVE_ASC = {"lowest", "smallest", "least", "minimum", "min", "oldest", "earliest", "cheapest"}
_SUPERLATIVE_DESC = {"highest", "largest", "greatest", "most", "maximum", "max", "top", "newest", "latest", "costliest"}
_SUPERLATIVE_SCALAR_AGG = {
    "highest": "MAX", "maximum": "MAX", "max": "MAX", "greatest": "MAX", "largest": "MAX", "costliest": "MAX",
    "lowest": "MIN", "minimum": "MIN", "min": "MIN", "smallest": "MIN", "cheapest": "MIN",
    "average": "AVG", "avg": "AVG", "mean": "AVG",
    "total": "SUM", "sum": "SUM",
}
_SUPERLATIVE_RE = re.compile(
    r"\b(" + "|".join(sorted(_SUPERLATIVE_ASC | _SUPERLATIVE_DESC | {"average", "avg", "mean", "total", "sum"}, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_SUPERLATIVE_SKIP_WORDS = {"the", "a", "an", "of", "in", "is", "was", "record", "row", "rows", "entry"}

# Comparative *operator* forms — English grammar, not business vocabulary.
# Third element is an implicit attribute hint for phrases where the
# comparative word itself names the attribute ("older than" implies an
# age/date-like column) rather than requiring a preceding noun ("salary
# over 50000"). Either way the hint word is resolved against real schema
# columns via grounding.resolve_attribute_word — never assumed to exist.
_COMPARATIVE_OPS = [
    (re.compile(r"\bat\s+least\b|\bgreater\s+than\s+or\s+equal\s+to\b|>=", re.IGNORECASE), ">=", None),
    (re.compile(r"\bat\s+most\b|\bless\s+than\s+or\s+equal\s+to\b|<=", re.IGNORECASE), "<=", None),
    (re.compile(r"\bis\s+not\b|!=|<>", re.IGNORECASE), "!=", None),
    (re.compile(r"\bolder\s+than\b", re.IGNORECASE), ">", "older"),
    (re.compile(r"\byounger\s+than\b", re.IGNORECASE), "<", "younger"),
    (re.compile(r"\bhigher\s+than\b", re.IGNORECASE), ">", "higher"),
    (re.compile(r"\blower\s+than\b", re.IGNORECASE), "<", "lower"),
    (re.compile(r"\bgreater\s+than\b|\bmore\s+than\b|\babove\b|\bover\b|\bexceeds\b", re.IGNORECASE), ">", None),
    (re.compile(r"\bless\s+than\b|\bfewer\s+than\b|\bbelow\b|\bunder\b", re.IGNORECASE), "<", None),
]


def _worded_number(text: str) -> Optional[int]:
    """Digit or spelled-out English number (language constant, not domain data)."""
    m = re.search(r"\b(\d+)\b", text)
    if m:
        return int(m.group(1))
    for word, val in _NUMBER_WORDS.items():
        if re.search(r"\b" + word + r"\b", text, re.IGNORECASE):
            return val
    return None


def _tokens(message: str) -> List[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", message.lower())


def _strip_polite(tokens: List[str]) -> List[str]:
    i = 0
    while i < len(tokens) and tokens[i] in LEADING_POLITE:
        i += 1
    return tokens[i:]


def _is_raw_sql(message: str) -> bool:
    from agent.sql_detector import is_raw_sql
    is_sql, _reason = is_raw_sql(message)
    if not is_sql:
        return False
    first = _tokens(message)[0] if _tokens(message) else ""
    return first not in _DDL_OPENERS


def _is_greeting(tokens: List[str]) -> bool:
    lead = _strip_polite(tokens)
    if not lead:
        return False
    return lead[0] in GREETINGS and len(lead) <= 3


def _has_grounded_table(tokens: List[str], metadata: dict) -> bool:
    for tok in tokens:
        if G.ground_table(tok, metadata, allow_fresh=False):
            return True
    return False


def _has_grounded_table_or_column(tokens: List[str], metadata: dict) -> bool:
    """True if any token is a real table name, or unambiguously names a
    column that belongs to exactly one table ("average salary by department"
    grounds via the "salary"/"department" columns even though no table name
    is mentioned)."""
    if _has_grounded_table(tokens, metadata):
        return True
    for tok in tokens:
        if G.find_table_for_column(tok, metadata):
            return True
    return False


def _detect_action(tokens: List[str], message: str,
                   metadata: dict) -> Optional[Tuple[str, Optional[str]]]:
    lead = _strip_polite(tokens)
    if not lead:
        return None
    first = lead[0]

    if _is_greeting(tokens):
        return ("converse", None)

    if first in {"who", "what", "why", "how"} and not _has_grounded_table_or_column(tokens, metadata):
        if any(t in _SQL_CONCEPT_WORDS for t in tokens):
            return ("ask", "KNOWLEDGE")
        return ("converse", None)

    for idx, tok in enumerate(lead):
        if tok in ACTION_WORDS:
            return _resolve_action(tok, idx, lead, tokens, message, metadata)

    if _has_grounded_table_or_column(tokens, metadata):
        return ("select", "DATA")
    return None


def _resolve_action(word: str, idx: int, lead: List[str], tokens: List[str],
                    message: str, metadata: dict) -> Optional[Tuple[str, Optional[str]]]:
    if word == "create":
        # Checked before the table/column heuristics below — "create a pie
        # chart for column marks" contains the literal word "column" (naming
        # the chart's dimension, not a schema change), which would otherwise
        # misroute the whole request into CREATE TABLE.
        if _is_chart_request(tokens):
            return ("visualize", "CHART")
        if "database" in tokens or "db" in tokens:
            return ("create", "DATABASE")
        if "table" in tokens or "column" in tokens or "columns" in tokens or "(" in message:
            return ("create", "TABLE")
        return None
    if word in ("add", "insert"):
        if "sample" in tokens and "data" in tokens:
            return ("add", "DATA")
        if any(r in tokens for r in ("row", "rows", "record", "records")):
            return ("add", "DATA")
        if "column" in tokens or "columns" in tokens:
            return ("alter", "COLUMN")
        if "table" in tokens:
            return ("create", "TABLE")
        if "database" in tokens or "db" in tokens:
            return ("create", "DATABASE")
        if word == "insert":
            # Unlike "add" (which is also used loosely for schema changes —
            # "add a column"), a bare "insert" with no row/column/table
            # cues is unambiguously a write; there is no insert_row
            # capability yet, so fail into a clarification rather than
            # falling through to the "select" catch-all below.
            return None
        if "to" in tokens or "in" in tokens:
            return ("alter", "COLUMN")
        return None
    if word == "alter":
        return ("alter", "COLUMN")
    if word == "change":
        if "database" in tokens or "db" in tokens:
            return ("switch", "DATABASE")
        return ("alter", "COLUMN")
    if word == "rename":
        if "column" in tokens or "columns" in tokens:
            return ("alter", "COLUMN")
        return ("rename", "TABLE")
    if word in ("switch", "use", "connect"):
        return ("switch", "DATABASE")
    if word in ("show", "display", "list"):
        if _is_chart_request(tokens):
            return ("visualize", "CHART")
        obj = ""
        for j in range(idx + 1, len(lead)):
            if lead[j] in ("me", "the", "a", "an", "all"):
                continue
            obj = lead[j]
            break
        if obj in ("chart", "charts"):
            return ("visualize", "CHART")
        if obj in ("table", "tables", "schema"):
            return ("list", "TABLE")
        if obj in ("database", "databases", "db", "dbs"):
            return ("list", "DATABASE")
        return ("select", "DATA")
    if word in ("describe", "explain"):
        return ("describe", "TABLE")
    if word in ("plot", "chart", "graph", "visualize"):
        return ("visualize", "CHART")
    if word in ("drop", "delete", "remove", "destroy"):
        if "database" in tokens or "db" in tokens:
            return ("drop", "DATABASE")
        if "column" in tokens or "columns" in tokens:
            return ("alter", "COLUMN")
        return ("drop", "TABLE")
    if word == "update":
        # No update_row capability exists yet (see capabilities.py) — fail
        # into a clarification rather than falling through to the "select"
        # catch-all below, which would silently run a SELECT and report a
        # requested data change as if it had succeeded.
        return None
    if word == "give" and any(t in _VALUE_CHANGE_WORDS for t in tokens):
        return None
    if word in ("give", "tell", "get", "see", "select", "filter", "group", "count"):
        return ("select", "DATA")
    return None


def _column_specs(part_tokens: List[str]) -> List[ColumnSpec]:
    if not part_tokens:
        return []
    if len(part_tokens) == 1:
        return [ColumnSpec(name=part_tokens[0])]
    for i in range(1, len(part_tokens)):
        if G.is_type_word(part_tokens[i]):
            return [ColumnSpec(name=part_tokens[0], type=" ".join(part_tokens[i:]).upper())]
    return [ColumnSpec(name=t) for t in part_tokens]


def _split_column_text(text: str) -> List[str]:
    parts = re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _column_list_from_fragment(tokens: List[str], message: str) -> Optional[List[ColumnSpec]]:
    if "(" in message:
        m = re.search(r"\(([^)]*)\)", message)
        if m:
            parts = _split_column_text(m.group(1))
            cols = []
            for p in parts:
                cols.extend(_column_specs(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p.lower())))
            return cols or None
    m = re.search(r"\b(?:with\s+columns|columns|with|containing|fields)\s+(.+)$", message.lower())
    if m:
        parts = _split_column_text(m.group(1))
        cols = []
        for p in parts:
            cols.extend(_column_specs(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p.lower())))
        return cols or None
    plain = [t for t in tokens if G.is_identifier(t) and not G.is_non_entity(t) and not G.is_pronoun(t)]
    if len(plain) >= 1:
        parts = _split_column_text(" ".join(plain))
        cols = []
        for p in parts:
            cols.extend(_column_specs(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", p.lower())))
        if cols:
            return cols
    return None


# ─── Role parsers ────────────────────────────────────────────────────────────

def _after_marker(tokens: List[str], markers: Tuple[str, ...]) -> Optional[str]:
    for i, tok in enumerate(tokens):
        if tok in markers:
            for j in range(i + 1, len(tokens)):
                t = tokens[j]
                if t in LEADING_POLITE or t in ("a", "an", "the", "new", "fresh"):
                    continue
                if not G.is_identifier(t):
                    return None
                if G.is_non_entity(t) or G.is_pronoun(t):
                    continue
                return t
            return None
    return None


def _before_marker(tokens: List[str], markers: Tuple[str, ...]) -> Optional[str]:
    """Find an identifier immediately BEFORE a marker token — the "X table"
    phrasing ("create employees table"), as opposed to _after_marker's
    "table X" phrasing ("create table employees")."""
    for i, tok in enumerate(tokens):
        if tok in markers and i > 0:
            t = tokens[i - 1]
            if G.is_identifier(t) and not G.is_non_entity(t) and not G.is_pronoun(t):
                return t
    return None


def _parse_create_database(tokens: List[str], message: str) -> Optional[str]:
    if "called" in tokens or "named" in tokens:
        name = _after_marker(tokens, ("called", "named"))
        if name:
            return name
    return _after_marker(tokens, ("database", "db"))


def _parse_create_table(tokens: List[str], message: str) -> Tuple[Optional[str], List[ColumnSpec]]:
    table_name = None
    cols: List[ColumnSpec] = []

    if "(" in message:
        cols = _column_list_from_fragment(tokens, message) or []
        before = message.split("(")[0].strip()
        before_tokens = _tokens(before)
        table_name = _after_marker(before_tokens, ("called", "named", "table"))
        if not table_name:
            table_name = _before_marker(before_tokens, ("table",))
    else:
        m = re.search(r"\bwith\s+(?:columns\s+)?(.+)$", message.lower())
        has_with_clause = bool(m) and bool(_tokens(m.group(1)))
        name = _after_marker(tokens, ("called", "named"))
        if not name:
            name = _after_marker(tokens, ("table",))
        if not name:
            name = _before_marker(tokens, ("table",))
        if has_with_clause:
            cols = _column_list_from_fragment(tokens, message) or []
            with_idx = next((i for i, t in enumerate(tokens) if t == "with"), len(tokens))
            if name:
                name_idx = next((i for i, t in enumerate(tokens) if t == name), with_idx)
                table_name = name if name_idx < with_idx else None
            else:
                table_name = None
        else:
            table_name = name

    if not table_name and not cols:
        table_name = _after_marker(tokens, ("table",)) or _before_marker(tokens, ("table",))
    return table_name, cols


def _type_from(tokens: List[str], idx: int) -> Optional[str]:
    """Collect a run of consecutive SQL type words starting at idx."""
    parts = []
    while idx < len(tokens) and G.is_type_word(tokens[idx]):
        parts.append(tokens[idx])
        idx += 1
    return " ".join(parts).upper() if parts else None


def _dedupe_filters(filters: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    seen = set()
    out = []
    for (col, op, val) in filters:
        key = (col, op, str(val))
        if key not in seen:
            seen.add(key)
            out.append((col, op, val))
    return out


def _parse_alter_column(tokens: List[str], message: str) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    table = None
    column = None
    column_type = None
    operation = "ADD_COLUMN"

    sub_op = "add"
    for t in tokens:
        if t in ("drop", "delete", "remove"):
            sub_op = "drop"
        elif t == "rename":
            sub_op = "rename"

    for i, tok in enumerate(tokens):
        if tok == "table":
            table = _after_marker(tokens[i:], ("table",))
            break
    if not table:
        table = _after_marker(tokens, ("to", "in", "from"))
        if table and table.lower() in ("table",):
            table = None

    for i, tok in enumerate(tokens):
        if tok == "column":
            for j in range(i + 1, len(tokens)):
                if G.is_identifier(tokens[j]) and not G.is_non_entity(tokens[j]):
                    column = tokens[j]
                    if j + 1 < len(tokens) and G.is_type_word(tokens[j + 1]):
                        column_type = _type_from(tokens, j + 1)
                    break
            break

    if not column:
        for i, tok in enumerate(tokens):
            if tok == "add":
                for j in range(i + 1, len(tokens)):
                    if G.is_identifier(tokens[j]) and not G.is_non_entity(tokens[j]):
                        column = tokens[j]
                        if j + 1 < len(tokens) and G.is_type_word(tokens[j + 1]):
                            column_type = _type_from(tokens, j + 1)
                        break
                break

    if sub_op == "rename":
        return table, column, column_type, "RENAME_COLUMN"
    if sub_op == "drop":
        return table, column, column_type, "DROP_COLUMN"
    return table, column, column_type, "ADD_COLUMN"


def _parse_add_sample_data(tokens: List[str], message: str) -> Tuple[Optional[int], Optional[str]]:
    count = None
    m = re.search(r"\b(\d+)\s*(?:sample\s+)?(?:rows?|records?)\b", message.lower())
    if m:
        count = int(m.group(1))
    else:
        m2 = re.search(r"\b([a-z]+)\s*(?:sample\s+)?(?:rows?|records?)\b", message.lower())
        if m2:
            count = _worded_number(m2.group(1))
    table = _after_marker(tokens, ("to", "in"))
    if table and table.lower() in ("table",):
        table = None
    return count, table


_AGG_WORD_TO_FUNCTION = {
    "total": "SUM", "sum": "SUM",
    "average": "AVG", "avg": "AVG", "mean": "AVG",
    "count": "COUNT", "number": "COUNT",
    "min": "MIN", "minimum": "MIN",
    "max": "MAX", "maximum": "MAX",
}


def _parse_visualize(tokens: List[str], message: str, metadata: dict,
                     context_table_hint: Optional[str] = None) -> Tuple[ChartSpec, Optional[str]]:
    """Parse a chart request and resolve its table. Returns (ChartSpec, table)
    — table is returned separately (not just via the caller's own loop) since
    the raw measure/dimension words below must be excluded from table
    candidacy first (the same "salary" vs "salaries" conflation the retrieve
    capability guards against — see _filter_reference_words)."""
    chart_type = None
    dimension_word = None
    measure_word = None
    aggregation = None

    for t in tokens:
        if t in CHART_WORDS:
            chart_type = t
            break

    text = message.lower()
    m = re.search(r"\bby\s+([a-z_]+)\s*$", text)
    if m:
        dimension_word = m.group(1)
    m2 = re.search(r"\b(?:(total|average|avg|mean|count|number|min|minimum|max|maximum)\s+)?([a-z_]+)\s+by\s+", text)
    if m2:
        if m2.group(1):
            aggregation = _AGG_WORD_TO_FUNCTION.get(m2.group(1))
        measure_word = m2.group(2)

    # Only exclude dimension/measure words that actually ground as a real
    # column somewhere — "employees" in "chart of employees by department"
    # is the table noun, not a measure column, and must stay eligible for
    # table matching below even though it sits in the "X by Y" measure slot.
    exclude = {
        w for w in (dimension_word, measure_word)
        if w and G.ground_column(w, metadata, table=None) is not None
    }
    exclude |= _filter_reference_words(message)

    table = None
    for tok in tokens:
        if tok in exclude:
            continue
        tbl = G.ground_table(tok, metadata, allow_fresh=False)
        if tbl:
            table = tbl
            break
    if not table:
        for tok in tokens:
            if tok in exclude:
                continue
            inferred = G.find_table_for_column(tok, metadata)
            if inferred:
                table = inferred
                break
    attr_table = table or context_table_hint

    measure = G.ground_column(measure_word, metadata, table=attr_table) if measure_word else None
    dimension = G.ground_column(dimension_word, metadata, table=attr_table) if dimension_word else None

    return ChartSpec(chart_type=chart_type, dimension=dimension, measure=measure,
                     aggregation=aggregation), table


def _parse_filters(message: str) -> List[Tuple[str, str, str]]:
    text = message.lower()
    filters = []

    m = re.search(r"\bin\s+(?:the\s+)?(?P<val>[a-z_][a-z0-9_]*)\s+(?P<col>[a-z_]+)\s*$", text)
    if m and not G.is_non_entity(m.group("col")) and not G.is_type_word(m.group("col")):
        filters.append((m.group("col"), "=", m.group("val")))

    m = re.search(r"\b(?P<col>[a-z_]+)\s+(?:is|are|was|were|has|have)\s+(?:been\s+)?(?:greater than|more than|over)\s+(?P<val>\d+)", text)
    if m:
        filters.append((m.group("col"), ">", m.group("val")))
    else:
        m = re.search(r"\b(?P<col>[a-z_]+)\s+(?:greater than|less than|over|more than|>\s*)\s*(?P<val>\d+)", text)
        if m:
            op = "<" if "less than" in text[m.start():m.end()] else ">"
            filters.append((m.group("col"), op, m.group("val")))

    m = re.search(r"\b(?:hired|joined|after|since)\s+(?:in\s+)?(?P<val>\d{4})\b", text)
    if m:
        filters.append(("hire_date", ">", m.group("val")))

    m = re.search(r"\bwhere\s+(?P<col>[a-z_]+)\s*(?P<op>=|!=|>|<|>=|<=)\s*(?P<val>[a-z0-9_.'\s-]+?)\s*(?:and|,|$)", text)
    if m:
        filters.append((m.group("col"), m.group("op"), m.group("val").strip()))

    m = re.search(r"\b(?:with|having)\s+(?P<col>[a-z_]+)\s*(?:=|\bis\b|\bare\b)\s*(?P<val>[a-z0-9_.'\s-]+?)\s*(?:and|,|$)", text)
    if m:
        filters.append((m.group("col"), "=", m.group("val").strip()))

    return filters


def _parse_comparative_filters(message: str, table: Optional[str],
                               metadata: dict) -> Tuple[List[Tuple[str, str, str]], bool]:
    """Parse "X than N" style comparatives ("older than 30", "salary at least
    50000"), resolving the attribute word against the connected schema's real
    numeric/date columns (grounding.resolve_attribute_word) instead of a
    hardcoded word->column map. Returns (filters, ambiguous).
    """
    text = message.lower()
    for op_re, op, implicit_word in _COMPARATIVE_OPS:
        m = op_re.search(text)
        if not m:
            continue
        after = text[m.end():].strip()
        val_m = re.search(r"\d+", after)
        if not val_m:
            continue
        value = val_m.group(0)
        if implicit_word:
            head_word = implicit_word
        else:
            before = text[:m.start()].strip()
            head_tokens = re.findall(r"[a-z_][a-z0-9_]*", before)
            head_word = head_tokens[-1] if head_tokens else None
        if not head_word or G.is_non_entity(head_word):
            continue
        col, ambiguous = G.resolve_attribute_word(head_word, table, metadata)
        if col:
            return [(col, op, value)], False
        if ambiguous:
            return [], True
        return [], False
    return [], False


def _filter_reference_words(message: str) -> set:
    """Words used as the referenced attribute in a comparative/filter clause
    ("salary above 110000", "with status = active"). These name a column,
    never a table — even when the word also happens to alias-match a table
    name (e.g. singular "salary" aliasing the plural "salaries" table via
    EntityResolver's pluralization rules). Used to keep the table-grounding
    loop below from mistaking a filtered-on column for a table switch."""
    text = message.lower()
    words = set()

    for op_re, op, implicit_word in _COMPARATIVE_OPS:
        m = op_re.search(text)
        if not m or implicit_word:
            continue
        before = text[:m.start()].strip()
        head_tokens = re.findall(r"[a-z_][a-z0-9_]*", before)
        if head_tokens:
            words.add(head_tokens[-1])

    for pattern in (
        r"\bin\s+(?:the\s+)?[a-z_][a-z0-9_]*\s+(?P<col>[a-z_]+)\s*$",
        r"\b(?P<col>[a-z_]+)\s+(?:is|are|was|were|has|have)\s+(?:been\s+)?(?:greater than|more than|over)\s+\d+",
        r"\b(?P<col>[a-z_]+)\s+(?:greater than|less than|over|more than|>\s*)\s*\d+",
        r"\bwhere\s+(?P<col>[a-z_]+)\s*(?:=|!=|>|<|>=|<=)",
        r"\b(?:with|having)\s+(?P<col>[a-z_]+)\s*(?:=|\bis\b|\bare\b)",
    ):
        m = re.search(pattern, text)
        if m:
            words.add(m.group("col"))

    return words


def _parse_retrieve(tokens: List[str], message: str, metadata: dict,
                    context_table_hint: Optional[str] = None) -> dict:
    roles: dict = {
        "table": None, "columns": [], "filters": [], "limit": None,
        "group_by": [], "aggregations": [], "ordering": [],
        "_unresolved_attribute": False,
    }

    filter_words = _filter_reference_words(message)
    for tok in tokens:
        if tok in filter_words:
            continue
        tbl = G.ground_table(tok, metadata, allow_fresh=False)
        if tbl:
            roles["table"] = tbl
            break

    if not roles["table"]:
        # No table named directly ("average salary by department") — infer it
        # from a mentioned column that unambiguously belongs to one table.
        for tok in tokens:
            inferred = G.find_table_for_column(tok, metadata)
            if inferred:
                roles["table"] = inferred
                break

    m = re.search(r"\b(?:top|first|limit|only)\s+(\d+)\b", message.lower())
    if m:
        roles["limit"] = int(m.group(1))
    else:
        m0 = re.search(r"\b(?:top|first|limit|only)\s+([a-z]+)\b", message.lower())
        if m0:
            val = _worded_number(m0.group(1))
            if val is not None:
                roles["limit"] = val

    m = re.search(r"\b(?:group\s+by|grouped\s+by)\s+([a-z_]+)", message.lower())
    if m:
        roles["group_by"].append(m.group(1))
    else:
        m = re.search(r"\btrends?\s+by\s+([a-z_]+)\b", message.lower())
        if m:
            roles["group_by"].append(m.group(1))

    m = re.search(r"\b(avg|average|mean|sum|total|count|min|max)\s+(?:of\s+)?([a-z_]+)\b", message.lower())
    if m:
        fn_map = {"avg": "AVG", "average": "AVG", "mean": "AVG", "sum": "SUM",
                  "total": "SUM", "count": "COUNT", "min": "MIN", "max": "MAX"}
        roles["aggregations"].append(QueryAggregation(function=fn_map[m.group(1)], column=m.group(2)))

    m = re.search(r"\b(?:show|give|get)\s+me\s+([a-z0-9_,\s]+?)\s+(?:of|from)\s+", message.lower())
    if m:
        col_text = m.group(1)
        roles["columns"] = [c.strip() for c in re.split(r",|\sand\s", col_text) if c.strip()]

    m = re.search(r"\b(?:order\s+by|sorted?\s+by|sort\s+on)\s+(.+?)(?:\s*,\s*|\s+limit\b|\s+offset\b|\s*$)", message.lower())
    if m:
        order_text = m.group(1).strip()
        for oc in re.split(r"\s*,\s*", order_text):
            oc = oc.strip()
            if not oc:
                continue
            direction = "DESC"
            col = oc
            if re.search(r"\b(?:desc|descending)\b", oc, re.IGNORECASE):
                direction = "DESC"
                col = re.sub(r"\b(?:desc|descending)\b", "", oc, flags=re.IGNORECASE).strip()
            elif re.search(r"\b(?:asc|ascending)\b", oc, re.IGNORECASE):
                direction = "ASC"
                col = re.sub(r"\b(?:asc|ascending)\b", "", oc, flags=re.IGNORECASE).strip()
            col = col.strip().rstrip(",")
            if col:
                roles["ordering"].append(QueryOrdering(column=col, direction=direction))

    roles["filters"] = _parse_filters(message)

    # When this message doesn't name its own table, resolve filter/superlative
    # attribute words against the prior turn's table (context_table_hint) —
    # otherwise a word that's a column on multiple tables (e.g. "salary" on
    # both "employees" and "salaries") looks ambiguous even though the
    # conversation has already established which table is meant. The final
    # `roles["table"]` is left None either way; context_resolution still fills
    # it in from the prior frame afterward.
    attr_table = roles["table"] or context_table_hint
    comp_filters, comp_ambiguous = _parse_comparative_filters(message, attr_table, metadata)
    if comp_filters:
        roles["filters"].extend(comp_filters)
    elif comp_ambiguous:
        roles["_unresolved_attribute"] = True

    # Superlatives ("highest paid", "oldest employee") — only when nothing
    # else already produced an aggregation/ordering, and never a hardcoded
    # word->column guess: resolve the attribute against real schema columns.
    if not roles["aggregations"] and not roles["ordering"]:
        sup_m = _SUPERLATIVE_RE.search(message.lower())
        # "top 5" is a quantity limiter (already parsed above), not a
        # superlative attribute request; "at least"/"at most" are comparative
        # idioms whose "least"/"most" is not a standalone superlative either.
        if sup_m and re.search(r"\btop\s+\d", message.lower()) and sup_m.group(1).lower() == "top":
            sup_m = None
        if sup_m and sup_m.group(1).lower() in ("least", "most") and \
                re.search(r"\bat\s+" + sup_m.group(1).lower() + r"\b", message.lower()):
            sup_m = None
        if sup_m:
            sup_word = sup_m.group(1).lower()
            is_scalar = bool(re.search(
                r"^\s*(?:what|tell\s+me)\s+(?:is|was)\s+(?:the\s+)?" + re.escape(sup_word),
                message.strip().lower(),
            ))
            after_text = message.lower().split(sup_word, 1)[-1]
            after_tokens = [t for t in re.findall(r"[a-z_][a-z0-9_]*", after_text)
                            if t not in _SUPERLATIVE_SKIP_WORDS]
            attr_word = after_tokens[0] if after_tokens else sup_word
            col, ambiguous = G.resolve_attribute_word(attr_word, attr_table, metadata)
            if not col and attr_word != sup_word:
                col, ambiguous = G.resolve_attribute_word(sup_word, attr_table, metadata)
            if col:
                is_agg_only_word = sup_word in {"average", "avg", "mean", "total", "sum"}
                if (is_scalar or is_agg_only_word) and sup_word in _SUPERLATIVE_SCALAR_AGG:
                    roles["aggregations"].append(QueryAggregation(function=_SUPERLATIVE_SCALAR_AGG[sup_word], column=col))
                else:
                    direction = "ASC" if sup_word in _SUPERLATIVE_ASC else "DESC"
                    roles["ordering"].append(QueryOrdering(column=col, direction=direction))
                    if roles["limit"] is None:
                        roles["limit"] = 1
            elif ambiguous:
                roles["_unresolved_attribute"] = True

    return roles


# ─── Orchestration ───────────────────────────────────────────────────────────

def interpret_message(message: str, metadata: dict, context: dict = None) -> FrameResult:
    context = context or {}

    # If the caller's active-database context disagrees with metadata's own
    # (stale) active_database field, rescope "tables" to the actually-active
    # database so table grounding matches what the user is really working
    # in — without widening grounding to search other databases' tables too
    # (which would let a fresh CREATE TABLE name collide with an unrelated
    # database's similarly-named table).
    active_override = context.get("active_database")
    if active_override and active_override != metadata.get("active_database"):
        db_tables = (metadata.get("all_tables_by_db") or {}).get(active_override)
        overlay = {"active_database": active_override}
        if db_tables is not None:
            overlay["tables"] = db_tables
        metadata = {**metadata, **overlay}

    msg = message.strip()
    if not msg:
        return FrameResult(
            frame=SemanticFrame(source="SEMANTIC_FRAME", action="none",
                                capability_id="understanding_failed"),
            status=STATUS_FAILED, capability_id="understanding_failed",
            reason="empty message",
        )

    tokens = _tokens(msg)

    if _is_raw_sql(msg):
        frame = SemanticFrame(
            action="execute", object_type="RAW_SQL", raw_sql=msg,
            capability_id="raw_sql", source="SEMANTIC_FRAME",
        )
        return _finish(frame, metadata, context, reason="raw-sql")

    # A pending create_table clarification takes priority over fresh action
    # detection — "with columns name text, salary integer" should fill the
    # pending frame even if a token happens to also match a live column name
    # (which would otherwise look like a stray retrieve request).
    pending = context.get("pending_frame")
    if pending is not None and pending.capability_id == "create_table":
        fragment = _fragment_for_pending(tokens, msg, context, metadata)
        if fragment is not None:
            return _finish(fragment, metadata, context, reason="continuation")

    action = _detect_action(tokens, msg, metadata)

    if action is None:
        if _is_greeting(tokens):
            frame = SemanticFrame(action="converse", capability_id="general_conversation",
                                  source="SEMANTIC_FRAME")
            return _finish(frame, metadata, context, reason="greeting")
        fragment = _fragment_for_pending(tokens, msg, context, metadata)
        if fragment is not None:
            return _finish(fragment, metadata, context, reason="continuation")
        frame = SemanticFrame(action="none", capability_id="understanding_failed",
                              source="SEMANTIC_FRAME")
        return _finish(frame, metadata, context, reason="no action")

    action_word, object_type = action
    prior = context.get("prior_frames") or []
    context_table_hint = prior[-1].table if prior else None
    frame = _build_frame(action_word, object_type, tokens, msg, metadata, context_table_hint)
    if frame is None:
        frame = SemanticFrame(action="none", capability_id="understanding_failed",
                              source="SEMANTIC_FRAME")
        return _finish(frame, metadata, context, reason="unsupported structure")

    frame.object_type = object_type
    frame.action = action_word
    frame = context_resolution.resolve_references(frame, context)
    return _finish(frame, metadata, context, reason="interpreted")


def _fragment_for_pending(tokens: List[str], message: str,
                          context: dict, metadata: dict = None) -> Optional[SemanticFrame]:
    pending = context.get("pending_frame")
    if pending is None:
        return None
    if pending.capability_id != "create_table":
        return None
    missing = pending.missing_required or []

    # Fast path: single-token table name.
    if "table" in missing and len(tokens) == 1:
        cand = tokens[0]
        if G.is_identifier(cand) and not G.is_non_entity(cand) and not G.is_pronoun(cand):
            frame = pending.model_copy(deep=True)
            frame.table = cand
            frame.is_clarification_response = True
            frame.missing_required = [r for r in missing if r != "table"]
            return frame

    # Fast path: column list via parens / "with columns" marker / plain-token
    # fallback. Only trusted when a table name isn't ALSO still missing —
    # this fallback can't distinguish "the table name" from "a column name"
    # in the same reply (a multi-word reply naming both, e.g. "name it books,
    # and add two columns: name and id", needs the LLM fallback below to
    # resolve both slots from one completion instead of guessing).
    if "table" not in missing:
        cols = _column_list_from_fragment(tokens, message)
        if cols:
            frame = pending.model_copy(deep=True)
            frame.columns = cols
            frame.is_clarification_response = True
            frame.missing_required = [r for r in missing if r != "columns"]
            return frame

    # Fast paths couldn't cleanly resolve what's still missing — fall back
    # to LLM-based extraction with full context (original request + this
    # reply + live schema), then validate whatever it proposes against the
    # real schema before trusting it.
    if missing:
        from agent.clarification_resolver import resolve_create_table_slots
        result = resolve_create_table_slots(
            original_request=context.get("original_request") or "",
            missing=missing,
            user_reply=message,
            metadata=metadata or {},
            known_table=pending.table,
        )
        if result.ok and (result.table_name or result.columns):
            frame = pending.model_copy(deep=True)
            if result.table_name and "table" in missing:
                frame.table = result.table_name
            if result.columns and "columns" in missing:
                frame.columns = result.columns
            frame.is_clarification_response = True
            frame.missing_required = [
                r for r in missing
                if not (r == "table" and frame.table) and not (r == "columns" and frame.columns)
            ]
            return frame
    return None


def _build_frame(action_word: str, object_type: Optional[str], tokens: List[str],
                 message: str, metadata: dict,
                 context_table_hint: Optional[str] = None) -> Optional[SemanticFrame]:
    cap = capability_for(action_word, object_type)
    if cap is None:
        return None

    frame = SemanticFrame(action=action_word, object_type=object_type,
                          capability_id=cap.id, source="SEMANTIC_FRAME")
    frame.legacy_intent = cap.legacy_intent

    if cap.id == "create_database":
        frame.database = _parse_create_database(tokens, message)
    elif cap.id == "create_table":
        table_name, cols = _parse_create_table(tokens, message)
        frame.table = table_name
        frame.columns = cols
    elif cap.id in ("add_column", "drop_column", "alter_column"):
        table, column, column_type, op = _parse_alter_column(tokens, message)
        frame.table = table
        frame.alter = AlterSpec(operation=op, column=column, new_type=column_type)
        if op == "DROP_COLUMN":
            frame.capability_id = "drop_column"
        elif op == "RENAME_COLUMN":
            frame.capability_id = "alter_column"
        else:
            frame.capability_id = "add_column"
    elif cap.id == "add_sample_data":
        count, table = _parse_add_sample_data(tokens, message)
        frame.table = table
        frame.sample_data = SampleDataSpec(count=count, table=table)
    elif cap.id == "visualize":
        frame.chart, frame.table = _parse_visualize(tokens, message, metadata, context_table_hint)
    elif cap.id == "switch_database":
        frame.database = _after_marker(tokens, ("database", "db"))
        if frame.database is None:
            m = re.search(r"\b(?:use|connect\s+to|switch\s+to|to)\s+([a-z_][a-z0-9_]*)\s*$", message.lower())
            if m:
                frame.database = m.group(1)
    elif cap.id in ("list_tables", "list_databases"):
        pass
    elif cap.id == "describe_table":
        frame.table = _after_marker(tokens, ("describe", "explain", "table"))
    elif cap.id == "retrieve":
        roles = _parse_retrieve(tokens, message, metadata, context_table_hint)
        frame.table = roles["table"]
        frame.columns = [ColumnSpec(name=c) for c in roles["columns"]]
        frame.filters = [
            QueryFilter(column=c, operator=op, value=v)
            for (c, op, v) in _dedupe_filters(roles["filters"])
        ]
        frame.limit = roles["limit"]
        frame.group_by = roles["group_by"]
        frame.aggregations = roles["aggregations"]
        frame.ordering = roles["ordering"]
        if roles.get("_unresolved_attribute"):
            frame.missing_required = list(frame.missing_required or []) + ["ordering_column"]
    elif cap.id == "drop_table":
        frame.table = _after_marker(tokens, ("table", "drop", "delete", "remove", "destroy"))
    elif cap.id == "drop_database":
        frame.database = _after_marker(tokens, ("database", "db", "drop", "delete", "remove", "destroy"))
    elif cap.id == "rename_table":
        frame.table = _after_marker(tokens, ("table", "rename"))
        frame.new_name = _after_marker(tokens, ("to",))
    elif cap.id in ("list_tables", "list_databases"):
        pass

    return frame


def _ground_frame(frame: SemanticFrame, metadata: dict,
                  trusted: set) -> Tuple[SemanticFrame, Optional[str]]:
    cap = get_capability(frame.capability_id or "")
    if cap is None:
        return frame, None
    hint = None
    missing = list(frame.missing_required or [])

    if frame.database and not _trusted(frame.database, trusted):
        allow_fresh = cap.allows_fresh_name and cap.id == "create_database"
        grounded = G.ground_database(frame.database, metadata, allow_fresh=allow_fresh)
        if grounded is None:
            if cap.id == "switch_database":
                missing.append("database")
                available = [str(d) for d in metadata.get("databases") or [] if d]
                suffix = f" Available databases: {', '.join(available)}." if available else ""
                hint = (
                    f"The database '{frame.database}' was not found in your accessible databases."
                    f"{suffix}"
                )
            elif cap.id == "drop_database":
                missing.append("database")
                hint = f"The database '{frame.database}' was not found. Please check the name."
            else:
                missing.append("database")
        else:
            frame.database = grounded

    if frame.table and not _trusted(frame.table, trusted):
        allow_fresh = cap.allows_fresh_name and cap.id == "create_table"
        grounded = G.ground_table(frame.table, metadata, allow_fresh=allow_fresh)
        if grounded is None:
            if cap.id == "retrieve":
                missing.append("table")
                hint = (f"The table '{frame.table}' was not found in the current database. "
                        "Please check the table name or select another database.")
            elif cap.id in ("add_column", "drop_column", "alter_column", "add_sample_data", "describe_table"):
                missing.append("table")
                hint = f"The table '{frame.table}' was not found. Please check the table name."
            elif cap.id in ("drop_table", "rename_table"):
                missing.append("table")
                hint = f"The table '{frame.table}' was not found. Please check the table name."
            else:
                missing.append("table")
        else:
            frame.table = grounded

    if cap.id == "rename_table":
        if frame.new_name and not (G.is_identifier(frame.new_name) and not G.is_non_entity(frame.new_name)):
            frame.new_name = None
        if not frame.new_name:
            missing.append("new_name")
            hint = f"What would you like to rename '{frame.table}' to?" if frame.table else hint

    if cap.id in ("add_column", "drop_column", "alter_column") and frame.alter and frame.alter.column:
        if cap.id == "drop_column":
            allow = False
        else:
            allow = True
        grounded_col = G.ground_column(frame.alter.column, metadata, table=frame.table, allow_fresh=allow)
        if grounded_col is None and not allow:
            missing.append("column")
            hint = (f"The column '{frame.alter.column}' does not exist in table "
                    f"'{frame.table}'. Please specify an existing column.")
        elif grounded_col:
            frame.alter.column = grounded_col

    if frame.filters:
        kept = []
        for f in frame.filters:
            col = f.column
            grounded_col = G.ground_column(col, metadata, table=frame.table, allow_fresh=False)
            if grounded_col:
                f.column = grounded_col
                kept.append(f)
            else:
                hint = (f"The column '{col}' does not exist in table '{frame.table}'. "
                        "Please specify an existing column.")
        frame.filters = kept
        if hint and "column" not in missing:
            missing.append("column")

    if cap.id == "retrieve" and frame.columns:
        kept = []
        for col in frame.columns:
            grounded_col = G.ground_column(col.name, metadata, table=frame.table, allow_fresh=False)
            if grounded_col:
                kept.append(ColumnSpec(name=grounded_col))
            else:
                hint = (f"The column '{col.name}' does not exist in table '{frame.table}'. "
                        "Please specify an existing column.")
        frame.columns = kept
        if hint and "column" not in missing:
            missing.append("column")

    frame.missing_required = list(dict.fromkeys(missing))
    return frame, hint


def _trusted(value: str, trusted: set) -> bool:
    return str(value).lower() in {str(v).lower() for v in trusted}


def _finish(frame: SemanticFrame, metadata: dict, context: dict,
            reason: str) -> FrameResult:
    trusted = set()
    for prior in context.get("prior_frames") or []:
        if prior.table:
            trusted.add(prior.table)
        if prior.database:
            trusted.add(prior.database)
    if context.get("active_database"):
        trusted.add(context["active_database"])

    frame, hint = _ground_frame(frame, metadata, trusted)

    merged, superseded = context_resolution.unify_with_pending(frame, context)
    if superseded:
        merged.missing_required = []
    frame = merged
    frame.missing_required = list(frame.missing_required or [])

    cap = get_capability(frame.capability_id or "")
    if frame.database is None and cap and cap.id in (
        "list_tables", "describe_table", "add_column", "drop_column",
        "alter_column", "add_sample_data", "retrieve",
    ):
        active = context.get("active_database") or metadata.get("active_database")
        frame.database = active

    if frame.capability_id == "understanding_failed":
        return FrameResult(
            frame=frame, status=STATUS_FAILED,
            capability_id="understanding_failed",
            clarification_type="UNDERSTANDING_FAILED",
            reason=reason,
        )

    result = capability_check.check(frame, metadata)
    if result.status != STATUS_SUCCESS:
        frame.missing_required = list(
            dict.fromkeys(result.missing_required or frame.missing_required or [])
        )
    if hint:
        return FrameResult(
            frame=frame, status=STATUS_CLARIFY,
            capability_id=frame.capability_id,
            clarification_type="MISSING_ROLE",
            clarification_message=hint, reason=reason,
        )
    if result.status != STATUS_SUCCESS:
        return FrameResult(
            frame=frame, status=result.status,
            capability_id=frame.capability_id,
            clarification_type="MISSING_ROLE",
            clarification_message=result.message, reason=reason,
        )

    frame.legacy_intent = (cap.legacy_intent if cap else frame.legacy_intent)
    return FrameResult(
        frame=frame, status=STATUS_SUCCESS,
        capability_id=frame.capability_id, reason=reason,
    )


# ─── Gateway integration ─────────────────────────────────────────────────────
# The gateway (agent/universal_gateway.py) is the only caller of everything
# below — it builds the grounding metadata/context, runs interpret_message()
# once, and adapts the result into the legacy UnderstandingResult/QueryIntent
# shape the rest of the pipeline (routing, handlers, deterministic SQL
# builder) already understands. Nothing downstream re-interprets the message.

def build_grounding_metadata(meta: dict, active_db: Optional[str] = None) -> dict:
    """Translate the metadata_store shape into the grounding shape consumed by
    agent.grounding and agent.semantic_frame (databases, active_database,
    tables, all_tables_by_db, columns_by_table, column_types_by_table)."""
    columns_by_table: dict = {}
    column_types_by_table: dict = {}
    for tbl, schemas in (meta.get("table_schemas") or {}).items():
        if isinstance(schemas, dict):
            columns_by_table[tbl] = list(schemas.keys())
            column_types_by_table[tbl] = dict(schemas)
        else:
            columns_by_table[tbl] = list(schemas) if schemas else []
    for tbl, cols in (meta.get("schema") or {}).items():
        if tbl not in columns_by_table:
            columns_by_table[tbl] = list(cols)
    return {
        "databases": list(meta.get("databases") or []),
        "active_database": active_db or meta.get("selected_db"),
        "tables": list(meta.get("tables") or []),
        "all_tables_by_db": dict(meta.get("routing_summaries") or {}),
        "columns_by_table": columns_by_table,
        "column_types_by_table": column_types_by_table,
    }


def build_context(session: dict, active_db: Optional[str] = None) -> dict:
    """Build the cross-turn context object consumed by interpret_message()."""
    session = session or {}
    return {
        "pending_frame": session.get("semantic_pending_frame"),
        "prior_frames": session.get("semantic_prior_frames") or [],
        "active_database": active_db,
        "original_request": session.get("semantic_pending_original_request"),
    }


def frame_to_query_intent(frame: SemanticFrame) -> Optional[QueryIntent]:
    """Build a deterministic-SQL-builder-ready QueryIntent for a fully-grounded
    'retrieve' frame. Aggregations and GROUP BY are included — the builder
    supports both, so there is no reason to force these to the LLM planner."""
    if not frame.table:
        return None
    return QueryIntent(
        operation="SELECT",
        entities=QueryEntities(
            database=frame.database,
            tables=[frame.table],
            columns=[c.name for c in (frame.columns or [])],
        ),
        constraints=QueryConstraints(
            limit=frame.limit,
            filters=list(frame.filters or []),
        ),
        ordering=list(frame.ordering or []),
        aggregations=list(frame.aggregations or []),
        grouping=list(frame.group_by or []),
    )


def frame_to_chart_query_intent(frame: SemanticFrame) -> Optional[QueryIntent]:
    """Build a deterministic-SQL-builder-ready QueryIntent that fetches the
    data a chart needs: the measure aggregated by dimension when both are
    grounded, or the bare measure column otherwise (e.g. a histogram)."""
    if not frame.table or not frame.chart or not frame.chart.measure:
        return None
    chart = frame.chart
    if chart.dimension:
        aggregation = chart.aggregation
        if not aggregation:
            # A dimension means this WILL be aggregated — defaulting a
            # non-numeric measure (e.g. a grade/category stored as text) to
            # SUM/AVG would build SQL Postgres rejects outright ("function
            # sum(text) does not exist"). COUNT works on any column type and
            # is usually what's actually wanted for a non-numeric measure
            # ("how many rows per category").
            aggregation = "SUM" if _is_numeric_column(frame.table, chart.measure) else "COUNT"
        return QueryIntent(
            operation="SELECT",
            entities=QueryEntities(database=frame.database, tables=[frame.table]),
            constraints=QueryConstraints(),
            aggregations=[QueryAggregation(function=aggregation, column=chart.measure)],
            grouping=[chart.dimension],
        )
    return QueryIntent(
        operation="SELECT",
        entities=QueryEntities(database=frame.database, tables=[frame.table], columns=[chart.measure]),
        constraints=QueryConstraints(limit=1000),
    )


def _is_numeric_column(table: str, column: str) -> bool:
    """Best-effort live-schema type check — fails open (treats an unknown
    type as numeric, preserving today's SUM-by-default behavior) so a
    metadata-cache miss never blocks a chart that would have worked."""
    try:
        from state.metadata_store import get_metadata
        meta = get_metadata()
        col_type = (meta.get("table_schemas") or {}).get(table, {}).get(column)
        if col_type is None:
            return True
        return any(h in str(col_type).upper() for h in G._NUMERIC_TYPE_HINTS)
    except Exception:
        return True


def instruction_for_frame(frame: SemanticFrame, message: str) -> str:
    """Reconstruct a tool instruction from a grounded frame where the frame's
    structured content is authoritative (e.g. a clarification continuation).
    Otherwise the original message is used — the tool-side NL parsers handle it."""
    cap_id = frame.capability_id
    if cap_id == "create_database" and frame.database:
        return f"CREATE DATABASE {frame.database}"
    if cap_id == "create_table":
        if frame.table and frame.columns:
            cols = ", ".join(f"{c.name} {c.type or 'TEXT'}" for c in frame.columns)
            return f"CREATE TABLE {frame.table} ({cols})"
        if frame.table:
            return f"CREATE TABLE {frame.table}"
        return message
    if cap_id == "drop_table" and frame.table:
        return f"DROP TABLE {frame.table}"
    if cap_id == "rename_table" and frame.table and frame.new_name:
        return f"ALTER TABLE {frame.table} RENAME TO {frame.new_name}"
    if cap_id == "drop_database" and frame.database:
        return f"DROP DATABASE {frame.database}"
    if cap_id in ("add_column", "drop_column", "alter_column"):
        if frame.table and frame.alter and frame.alter.column:
            col = frame.alter.column
            if frame.alter.operation == "DROP_COLUMN":
                return f"ALTER TABLE {frame.table} DROP COLUMN {col}"
            type_part = f" {frame.alter.new_type}" if frame.alter.new_type else ""
            return f"ALTER TABLE {frame.table} ADD COLUMN {col}{type_part}"
        return message
    if cap_id == "describe_table" and frame.table:
        return f"describe table {frame.table}"
    if cap_id == "raw_sql" and frame.raw_sql:
        # The frame's own raw_sql is authoritative here — `message` can be
        # stale/mismatched when this frame came from a reconstructed
        # clarification reply that was re-classified (routers/chat.py always
        # passes the ORIGINAL user-typed text into the handler, not the
        # reconstructed one the gateway actually classified this frame from).
        return frame.raw_sql
    return message


def clarification_data_for_frame(frame: SemanticFrame, clarification_message: Optional[str],
                                 meta: dict, target_db: Optional[str], session: dict,
                                 message: str) -> dict:
    """Build the rich clarification_data payload (type/options/metadata) for a
    STATUS_CLARIFY frame — the shape agent/pending_resolution.py's
    resolve_pending_clarification() expects for its round-trip resolution."""
    dbs = list(meta.get("databases") or [])
    tables = list(meta.get("tables") or [])
    active_db = target_db or meta.get("selected_db") or (session or {}).get("selected_database")
    missing = frame.missing_required or []
    question = clarification_message or "Could you clarify your request?"

    clar_type = "MISSING_ROLE"
    clar_data: dict = {
        "type": clar_type,
        "original_request": message,
        "target_db": active_db,
        "question": question,
        "attempts": 0,
        "metadata": {},
        "capability_id": frame.capability_id,
    }
    if frame.table:
        # Surfaced regardless of clarification type — any LLM-backed resolver
        # for the pending state (e.g. a chart clarification) benefits from
        # knowing the table was already grounded, even when the specific
        # clar_type branch below doesn't itself need it.
        clar_data["table_name"] = frame.table
    if frame.capability_id == "create_table":
        if "table" in missing:
            clar_type = "CREATE_TABLE_NAME"
        elif "columns" in missing:
            clar_type = "CREATE_TABLE_COLUMNS"
            if frame.table:
                clar_data["table_name"] = frame.table
    elif frame.capability_id == "add_sample_data" and frame.sample_data and frame.sample_data.count:
        clar_data["sample_count"] = frame.sample_data.count
        if "table" in missing:
            clar_type = "MISSING_TABLE"
            clar_data["options"] = tables
    elif "database" in missing:
        clar_type = "MISSING_DATABASE"
        clar_data["options"] = dbs
    elif "table" in missing:
        clar_type = "MISSING_TABLE"
        clar_data["options"] = tables
        if frame.table:
            clar_data["table_name"] = frame.table
    elif "column" in missing or "columns" in missing or "ordering_column" in missing:
        clar_type = "MISSING_ROLE"
        clar_data["metadata"]["role"] = "ordering_column" if "ordering_column" in missing else "column"
    clar_data["type"] = clar_type

    return clar_data


def frame_result_to_understanding(frame_result: FrameResult, raw_message: str,
                                  cleaned_msg: str) -> UnderstandingResult:
    """Adapt a FrameResult into the legacy UnderstandingResult/QueryIntent
    shape the rest of the pipeline (routing policy, handlers, the
    deterministic SQL builder) already consumes — the single seam that lets
    the gateway route through interpret_message() without reshaping every
    downstream consumer."""
    frame = frame_result.frame

    intent = frame.legacy_intent or "UNKNOWN"
    if frame.capability_id == "raw_sql":
        # capabilities.py's raw_sql entry uses legacy_intent="QUERY" for
        # response-payload compatibility, but routing needs the literal
        # "RAW_SQL" intent string to be authoritative here (no downstream
        # re-detection of raw SQL).
        intent = "RAW_SQL"

    if frame_result.status == STATUS_SUCCESS:
        status = "SUCCESS"
    elif frame_result.status == STATUS_CLARIFY:
        status = "AMBIGUOUS"
    else:
        status = "UNDERSTANDING_FAILED"

    if frame.capability_id == "retrieve":
        query_intent = frame_to_query_intent(frame) or QueryIntent(
            operation="SELECT",
            entities=QueryEntities(database=frame.database, tables=[frame.table] if frame.table else []),
        )
    else:
        query_intent = QueryIntent(
            operation=intent,
            entities=QueryEntities(
                database=frame.database,
                tables=[frame.table] if frame.table else [],
                columns=[c.name for c in (frame.columns or [])],
            ),
        )

    return UnderstandingResult(
        status=status,
        intent=intent,
        raw_input=raw_message,
        normalized_input=cleaned_msg,
        query_intent=query_intent,
        needs_clarification=(frame_result.status == STATUS_CLARIFY),
        clarification_type=frame_result.clarification_type,
        clarification_message=frame_result.clarification_message,
        confidence=frame.confidence if frame.confidence is not None else 1.0,
        source="SEMANTIC_FRAME",
        semantic_frame=frame,
    )
