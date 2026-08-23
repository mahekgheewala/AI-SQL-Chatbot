import re
import pandas as pd
import numpy as np
import hashlib
from typing import List, Dict, Any, Optional

from utils.entity_resolver import EntityResolver
from utils.resolution_result import ResolutionStatus

# Schema-driven resolver used in place of a fixed per-domain synonym table —
# resolves free-text query words against the *real* column/table names of
# the connected database (exact -> normalized -> alias -> fuzzy).
_resolver = EntityResolver()


class TableClarificationRequired(Exception):
    def __init__(self, candidates: List[str], db: str, user_message: str):
        self.candidates = candidates
        self.db = db
        self.user_message = user_message
        super().__init__(f"Ambiguous tables found: {candidates}")

def clean_token(text: str) -> str:
    return re.sub(r'[^a-z0-9_]', '', text.lower())

def find_target_columns(query: str, columns: List[str]) -> List[str]:
    """
    Finds column matches within the query. Uses exact matches,
    snake_case token matches, and predefined synonyms.
    """
    normalized_query = query.lower()
    query_tokens = set(re.split(r'[^a-zA-Z0-9_]+', normalized_query))
    
    matches = []
    
    # 1. Look for exact substring or token match of actual column names
    for col in columns:
        col_lower = col.lower()
        if col_lower in normalized_query:
            matches.append(col)
            continue
            
        if col_lower in query_tokens:
            matches.append(col)
            continue
            
        col_parts = set(col_lower.split('_'))
        if col_parts.intersection(query_tokens) and len(col_parts.intersection(query_tokens)) == len(col_parts):
            matches.append(col)
            continue
            
    if matches:
        return list(dict.fromkeys(matches))

    # 2. Schema-driven fallback: resolve each query word against the real
    # column names (exact/normalized/alias/fuzzy) instead of a hardcoded
    # per-domain synonym table. Generalizes to any connected schema.
    for token in query_tokens:
        if len(token) < 3:
            continue
        result = _resolver.resolve(token, columns, entity_type="column")
        if result.status == ResolutionStatus.SUCCESS:
            matches.append(result.value)
        elif result.status == ResolutionStatus.MULTIPLE_MATCHES:
            matches.extend(result.alternatives or [])

    return list(dict.fromkeys(matches))

def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts a value to float, handling NaNs, Inf, and None."""
    if val is None or pd.isna(val) or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

# ─────────────────────────────────────────────────────────────────────────────
# Cache Redesign Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def extract_tables_from_select(sql: str) -> List[str]:
    """
    Extracts all table names from a SELECT statement's FROM and JOIN clauses.
    Case-insensitive, returning sorted unique table names.
    """
    sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql_clean = re.sub(r'\s+', ' ', sql_clean).strip().lower()
    matches = re.findall(r'\b(?:from|join)\s+([a-z0-9_]+)\b', sql_clean)
    return sorted(list(set(matches)))

def get_sql_fingerprint(sql: str) -> str:
    """
    Normalizes a SQL query and computes its SHA-256 hash.
    Ensures identical queries with minor whitespace/comment variations yield the same key.
    """
    sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    sql_clean = re.sub(r'\s+', ' ', sql_clean).strip().lower()
    return hashlib.sha256(sql_clean.encode('utf-8')).hexdigest()

def is_unaggregated_select(sql: str) -> bool:
    """
    Checks if a SQL query is a SELECT statement that does not perform aggregations.
    Excludes statements containing AVG, SUM, COUNT, MIN, MAX, STDDEV, VARIANCE, or GROUP BY.
    """
    sql_upper = sql.upper()
    if "SELECT " not in sql_upper:
        return False
    aggregates = ["AVG(", "SUM(", "COUNT(", "MIN(", "MAX(", "STDDEV(", "VARIANCE("]
    if any(agg in sql_upper for agg in aggregates) or "GROUP BY" in sql_upper:
        return False
    return True

def score_candidate(table_name: str, query: str) -> float:
    score = 0.0
    tbl_clean = table_name.lower()
    query_clean = query.lower()
    tbl_words = set(re.split(r'[^a-zA-Z0-9_]+', tbl_clean))
    query_words = set(re.split(r'[^a-zA-Z0-9_]+', query_clean))
    
    # 1. Exact match of table name as a word in query (handling singular/plural)
    for word in query_words:
        if tbl_clean == word or tbl_clean + "s" == word or word + "s" == tbl_clean:
            score += 10.0
            break
        
    # 2. Word overlap between table name parts and query words
    overlap = tbl_words.intersection(query_words)
    score += len(overlap) * 2.0
    
    # 3. Substring match
    for word in query_words:
        if len(word) > 3:
            if word in tbl_clean or tbl_clean in word:
                score += 1.0
                
    # 4. Schema-driven synonym/abbreviation boost (dept<->department,
    # emp<->employee, ...) via EntityResolver's generic alias generation and
    # small generic-abbreviation table — resolved against the *real* table
    # name, never a hand-picked per-domain synonym set.
    for word in query_words:
        if len(word) < 3:
            continue
        result = _resolver.resolve(word, [table_name], entity_type="table")
        if result.status == ResolutionStatus.SUCCESS and result.match_type in ("ALIAS", "FUZZY"):
            score += 5.0
            break

    return score

