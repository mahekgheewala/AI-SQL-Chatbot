"""
Phase 10.5 — Universal Entity Resolver

Resolves user-referenced names (databases, tables, columns, views) through
four ordered stages:

  1. Normalization  — canonical form of both query and candidates
  2. Exact Match    — normalized equality check
  3. Alias Match    — dynamic naming-variation generation + static synonyms
  4. Fuzzy Match    — pluggable similarity engine (Levenshtein by default)

Compatibility checking (e.g. "can AVG be applied to this column?") is NOT
performed here.  That responsibility belongs to CompatibilityValidator.
"""

import re
from typing import Callable, Optional

from utils.resolution_result import ResolutionResult, ResolutionStatus


# ---------------------------------------------------------------------------
# Default similarity engine — pure-Python Levenshtein ratio
# ---------------------------------------------------------------------------

def _levenshtein_ratio(s1: str, s2: str) -> float:
    """
    Returns edit-distance similarity in [0.0, 1.0].

    Replace with RapidFuzz or embedding-based similarity by passing a custom
    callable to EntityResolver.__init__.
    """
    m, n = len(s1), len(s2)
    if m == 0 and n == 0:
        return 1.0
    if m == 0 or n == 0:
        return 0.0

    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    dist = dp[m][n]
    return 1.0 - dist / max(m, n)


# ---------------------------------------------------------------------------
# Static synonym registry
# ---------------------------------------------------------------------------

# True cross-domain synonyms that should never be auto-generated.
# Map: normalized_alias -> canonical normalized form (also normalized).
# Example: "dept" -> "department"
_STATIC_SYNONYMS: dict[str, str] = {
    "dept":          "department",
    "depts":         "departments",
    "emp":           "employee",
    "emps":          "employees",
    "sal":           "salary",
    "pay":           "salary",
    "compensation":  "salary",
    "hr":            "humanresources",
    "humanresource": "humanresources",
    "mgr":           "manager",
    "addr":          "address",
    "qty":           "quantity",
    "amt":           "amount",
    "num":           "number",
    "cnt":           "count",
    "rev":           "revenue",
    "trx":           "transaction",
    "txn":           "transaction",
    "cfg":           "config",
    "config":        "configuration",
    "prd":           "product",
    "prod":          "product",
    "cust":          "customer",
    "usr":           "user",
    "org":           "organization",
    "cat":           "category",
    "desc":          "description",
}


# ---------------------------------------------------------------------------
# EntityResolver
# ---------------------------------------------------------------------------

class EntityResolver:
    """
    Resolves entity names through a 4-stage pipeline.

    Parameters
    ----------
    similarity_engine:
        Callable ``(s1: str, s2: str) -> float`` returning a similarity score
        in [0.0, 1.0].  Defaults to Levenshtein ratio.  Swap out at any time
        without changing callers.
    fuzzy_threshold:
        Minimum similarity score for a fuzzy match to be accepted (0.0–1.0).
    """

    def __init__(
        self,
        similarity_engine: Optional[Callable[[str, str], float]] = None,
        fuzzy_threshold: float = 0.72,
    ):
        self._sim = similarity_engine or _levenshtein_ratio
        self._threshold = fuzzy_threshold

    # ------------------------------------------------------------------
    # Stage 1 — Normalization
    # ------------------------------------------------------------------

    @staticmethod
    def normalize(name: str) -> str:
        """
        Convert any naming style to a compact lowercase form with no
        separators.

        Examples
        --------
        "HR Database"     -> "hrdatabase"
        "HR-Database"     -> "hrdatabase"
        "hr.database"     -> "hrdatabase"
        "human_resources" -> "humanresources"
        """
        s = name.lower().strip()
        s = re.sub(r"[\s_.\-]+", "", s)   # remove separators
        s = re.sub(r"[^\w]", "", s)       # remove remaining non-word chars
        return s

    # ------------------------------------------------------------------
    # Stage 3 helper — Dynamic alias generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_aliases(name: str) -> set[str]:
        """
        Produce normalized naming variations for *name* automatically.

        For example "human_resources" generates:
          humanresources, hr, humanresources, humanresource (singular), hrresources …

        These are combined with the static synonym table at resolve time.
        """
        normalized = EntityResolver.normalize(name)
        aliases: set[str] = {normalized}

        # Always add both singular and plural forms of the normalized string.
        # Generic English pluralization rules (not domain vocabulary) — "-y"
        # <-> "-ies" (salary/salaries), "-s/-x/-z/-ch/-sh" <-> "-es", and the
        # plain trailing-"s" case.
        if normalized.endswith("ies") and len(normalized) > 3:
            aliases.add(normalized[:-3] + "y")
        elif normalized.endswith("y") and len(normalized) > 1 and normalized[-2] not in "aeiou":
            aliases.add(normalized[:-1] + "ies")

        if normalized.endswith("es") and len(normalized) > 2 and (
            normalized[-3] in "sxz" or normalized[-3:-1] in ("ch", "sh")
        ):
            aliases.add(normalized[:-2])
        elif normalized[-1:] in ("s", "x", "z") or normalized[-2:] in ("ch", "sh"):
            aliases.add(normalized + "es")

        if normalized.endswith("s"):
            aliases.add(normalized[:-1])          # strip trailing 's'
        else:
            aliases.add(normalized + "s")         # add trailing 's'

        # Split on camelCase, snake_case, kebab-case, spaces
        tokens = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", name)
        if len(tokens) >= 2:
            # Abbreviation: first letter of each token
            abbrev = "".join(t[0].lower() for t in tokens)
            aliases.add(abbrev)

            # Singular/plural naïve pair on each token
            for token in tokens:
                t_lower = token.lower()
                if t_lower.endswith("s"):
                    aliases.add(EntityResolver.normalize(
                        name.replace(token, t_lower[:-1])
                    ))
                else:
                    aliases.add(EntityResolver.normalize(
                        name.replace(token, t_lower + "s")
                    ))

            # Drop the last token (common for "employees_archive" -> "employees")
            aliases.add(EntityResolver.normalize(" ".join(tokens[:-1])))

        return aliases

    # ------------------------------------------------------------------
    # Public API — resolve
    # ------------------------------------------------------------------

    def resolve(
        self,
        query: str,
        candidates: list[str],
        entity_type: str = "entity",
    ) -> ResolutionResult:
        """
        Resolve *query* against *candidates* using all four stages.

        Parameters
        ----------
        query:
            User-supplied name (may contain spaces, mixed case, hyphens, …).
        candidates:
            Known valid entity names (e.g. list of tables in the database).
        entity_type:
            Label used in the ResolutionResult (e.g. "database", "table").

        Returns
        -------
        ResolutionResult with appropriate status and metadata.
        """
        if not candidates:
            return ResolutionResult(
                status=ResolutionStatus.NOT_FOUND,
                entity_type=entity_type,
                message=f"No {entity_type} candidates available.",
            )

        # ── Stage 2: Exact match (raw) ────────────────────────────────
        for c in candidates:
            if query == c:
                return ResolutionResult(
                    status=ResolutionStatus.SUCCESS,
                    value=c,
                    entity_type=entity_type,
                    confidence=1.0,
                    match_type="EXACT",
                )

        # ── Stage 1 + 2: Normalized exact match ───────────────────────
        norm_q = self.normalize(query)
        norm_candidates = {c: self.normalize(c) for c in candidates}

        norm_exact = [c for c, nc in norm_candidates.items() if norm_q == nc]
        if len(norm_exact) == 1:
            return ResolutionResult(
                status=ResolutionStatus.SUCCESS,
                value=norm_exact[0],
                entity_type=entity_type,
                confidence=0.98,
                match_type="EXACT",
            )
        if len(norm_exact) > 1:
            return ResolutionResult(
                status=ResolutionStatus.MULTIPLE_MATCHES,
                entity_type=entity_type,
                alternatives=norm_exact,
                match_type="EXACT",
                message=f"Multiple {entity_type} names match '{query}'.",
            )

        # ── Stage 3: Alias match ──────────────────────────────────────
        # Build alias set for the query
        query_aliases = self._generate_aliases(query)
        # Also check static synonyms
        if norm_q in _STATIC_SYNONYMS:
            query_aliases.add(_STATIC_SYNONYMS[norm_q])

        alias_matches = []
        for c in candidates:
            c_aliases = self._generate_aliases(c)
            # Also map candidate through static synonyms
            nc = self.normalize(c)
            if nc in _STATIC_SYNONYMS:
                c_aliases.add(_STATIC_SYNONYMS[nc])

            # Hit if query_aliases ∩ c_aliases is non-empty
            if query_aliases & c_aliases:
                alias_matches.append(c)

        if len(alias_matches) == 1:
            return ResolutionResult(
                status=ResolutionStatus.SUCCESS,
                value=alias_matches[0],
                entity_type=entity_type,
                confidence=0.90,
                match_type="ALIAS",
            )
        if len(alias_matches) > 1:
            return ResolutionResult(
                status=ResolutionStatus.MULTIPLE_MATCHES,
                entity_type=entity_type,
                alternatives=alias_matches,
                match_type="ALIAS",
                message=f"Multiple {entity_type} names match aliases for '{query}'.",
            )

        # ── Stage 4: Fuzzy match ──────────────────────────────────────
        scores = [
            (c, self._sim(norm_q, nc))
            for c, nc in norm_candidates.items()
        ]
        above = [(c, s) for c, s in scores if s >= self._threshold]

        if not above:
            return ResolutionResult(
                status=ResolutionStatus.NOT_FOUND,
                entity_type=entity_type,
                message=f"Could not find a {entity_type} matching '{query}'.",
            )

        above.sort(key=lambda x: x[1], reverse=True)
        best_c, best_s = above[0]

        # Check for ties within 0.05 of the best
        near_top = [c for c, s in above if abs(s - best_s) < 0.05]

        if len(near_top) == 1:
            return ResolutionResult(
                status=ResolutionStatus.SUCCESS,
                value=best_c,
                entity_type=entity_type,
                confidence=best_s,
                match_type="FUZZY",
                metadata={"similarity_score": best_s},
            )

        return ResolutionResult(
            status=ResolutionStatus.MULTIPLE_MATCHES,
            entity_type=entity_type,
            alternatives=near_top,
            match_type="FUZZY",
            confidence=best_s,
            message=f"Ambiguous {entity_type} candidates for '{query}'.",
            metadata={"similarity_score": best_s},
        )
