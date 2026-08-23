"""
Phase 10.5 — Compatibility Validator

Checks whether a resolved entity is semantically compatible with the
requested operation.  Consumes MetadataRegistry column profiles.

This service is intentionally separated from EntityResolver:
  - EntityResolver answers "which entity did the user mean?"
  - CompatibilityValidator answers "can that entity be used this way?"

Raises no exceptions — returns structured ValidationResult objects.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ValidationStatus(Enum):
    OK      = "OK"
    ERROR   = "ERROR"
    WARNING = "WARNING"


@dataclass
class ValidationResult:
    """
    Outcome of a compatibility check.

    Attributes
    ----------
    status    — OK | ERROR | WARNING
    message   — Human-readable explanation
    details   — Extra structured data for logging / debugging
    """
    status:  ValidationStatus
    message: str = ""
    details: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.status != ValidationStatus.ERROR


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class CompatibilityValidator:
    """
    Semantic compatibility checks.

    All methods accept a MetadataRegistry instance so they never perform
    their own schema lookups.

    Usage
    -----
    registry = get_registry(db_name)
    validator = CompatibilityValidator(registry)
    result = validator.validate_aggregation("employees", "name", "AVG")
    if not result.is_valid:
        return error_response(result.message)
    """

    def __init__(self, registry):
        """
        Parameters
        ----------
        registry : MetadataRegistry
            Caller-supplied registry instance so no database is selected
            implicitly.
        """
        self._registry = registry

    # ------------------------------------------------------------------
    # Aggregation compatibility
    # ------------------------------------------------------------------

    # Aggregation functions that require a numeric column
    _NUMERIC_AGGS = {"AVG", "SUM"}
    # Functions that work on any column
    _UNIVERSAL_AGGS = {"COUNT", "MIN", "MAX"}

    def validate_aggregation(
        self,
        table_name: str,
        column_name: str,
        agg_function: str,
    ) -> ValidationResult:
        """
        Verify that *agg_function* can be applied to *column_name* in *table_name*.

        Examples
        --------
        AVG(salary)  -> OK   (salary is numeric)
        AVG(name)    -> ERROR (name is text)
        SUM(created) -> ERROR (date is not numeric)
        COUNT(name)  -> OK   (COUNT works on anything)
        """
        agg = agg_function.upper().strip()
        profile = self._registry.get_column_profile(table_name, column_name)

        if profile is None:
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"Column '{column_name}' not found in table '{table_name}'.",
                details={"table": table_name, "column": column_name, "agg": agg},
            )

        if agg in self._NUMERIC_AGGS and not profile.is_numeric:
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=(
                    f"Cannot apply {agg}() to column '{column_name}' "
                    f"— its type '{profile.data_type}' is not numeric. "
                    f"Try a numeric column instead."
                ),
                details={
                    "table": table_name,
                    "column": column_name,
                    "agg": agg,
                    "data_type": profile.data_type,
                    "is_numeric": False,
                },
            )

        if agg not in self._NUMERIC_AGGS | self._UNIVERSAL_AGGS:
            return ValidationResult(
                status=ValidationStatus.WARNING,
                message=f"Unknown aggregation function '{agg}' — proceeding with caution.",
                details={"agg": agg},
            )

        return ValidationResult(
            status=ValidationStatus.OK,
            message=f"{agg}({column_name}) is compatible.",
            details={
                "table": table_name,
                "column": column_name,
                "agg": agg,
                "data_type": profile.data_type,
            },
        )

    # ------------------------------------------------------------------
    # Visualization compatibility
    # ------------------------------------------------------------------

    def validate_visualization(
        self,
        chart_type: str,
        table_name: str,
        x_column: Optional[str],
        y_column: Optional[str],
    ) -> ValidationResult:
        """
        Verify that the chosen columns are compatible with *chart_type*.

        Rules
        -----
        PIE       — 1 categorical + 1 numeric (or categorical-only for COUNT)
        HISTOGRAM — 1 numeric column
        SCATTER   — 2 numeric columns
        LINE      — 1 date/time or sortable column + 1 numeric column
        BAR       — 1 categorical + 1 numeric
        """
        ct = chart_type.upper()

        def _profile(col: Optional[str]):
            if col is None:
                return None
            return self._registry.get_column_profile(table_name, col)

        x_p = _profile(x_column)
        y_p = _profile(y_column)

        if ct == "PIE":
            # x must be categorical; y must be numeric or absent (COUNT)
            if x_column and x_p and not x_p.is_categorical:
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=(
                        f"Pie chart requires a categorical column for labels, "
                        f"but '{x_column}' is {x_p.data_type}."
                    ),
                    details={"chart": ct, "column": x_column},
                )
            if y_column and y_p and not y_p.is_numeric:
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=(
                        f"Pie chart requires a numeric column for values, "
                        f"but '{y_column}' is {y_p.data_type}."
                    ),
                    details={"chart": ct, "column": y_column},
                )

        elif ct == "HISTOGRAM":
            col = x_column or y_column
            p   = x_p or y_p
            if p and not p.is_numeric:
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=(
                        f"Histogram requires a numeric column, "
                        f"but '{col}' is {p.data_type}."
                    ),
                    details={"chart": ct, "column": col},
                )

        elif ct == "SCATTER":
            for col_name, p in ((x_column, x_p), (y_column, y_p)):
                if col_name and p and not p.is_numeric:
                    return ValidationResult(
                        status=ValidationStatus.ERROR,
                        message=(
                            f"Scatter plot requires numeric columns on both axes, "
                            f"but '{col_name}' is {p.data_type}."
                        ),
                        details={"chart": ct, "column": col_name},
                    )

        elif ct == "LINE":
            if x_p and not (x_p.is_date_time or x_p.is_sortable):
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=(
                        f"Line chart X-axis requires a date/time or ordered column, "
                        f"but '{x_column}' is {x_p.data_type}."
                    ),
                    details={"chart": ct, "column": x_column},
                )
            if y_p and not y_p.is_numeric:
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=(
                        f"Line chart Y-axis requires a numeric column, "
                        f"but '{y_column}' is {y_p.data_type}."
                    ),
                    details={"chart": ct, "column": y_column},
                )

        elif ct == "BAR":
            if x_p and not x_p.is_categorical:
                return ValidationResult(
                    status=ValidationStatus.WARNING,
                    message=(
                        f"Bar chart X-axis is typically categorical; "
                        f"'{x_column}' is {x_p.data_type}."
                    ),
                    details={"chart": ct, "column": x_column},
                )
            if y_p and not y_p.is_numeric:
                return ValidationResult(
                    status=ValidationStatus.ERROR,
                    message=(
                        f"Bar chart Y-axis requires a numeric column, "
                        f"but '{y_column}' is {y_p.data_type}."
                    ),
                    details={"chart": ct, "column": y_column},
                )

        return ValidationResult(
            status=ValidationStatus.OK,
            message=f"{ct} chart configuration is valid.",
            details={"chart": ct, "x": x_column, "y": y_column},
        )

    # ------------------------------------------------------------------
    # Column existence check (quick guard)
    # ------------------------------------------------------------------

    def validate_column_exists(
        self, table_name: str, column_name: str
    ) -> ValidationResult:
        """Simple existence check before any operation."""
        if self._registry.column_exists(table_name, column_name):
            return ValidationResult(status=ValidationStatus.OK)
        return ValidationResult(
            status=ValidationStatus.ERROR,
            message=f"Column '{column_name}' does not exist in table '{table_name}'.",
            details={"table": table_name, "column": column_name},
        )

    # ------------------------------------------------------------------
    # Sorting compatibility
    # ------------------------------------------------------------------

    def validate_sort(self, table_name: str, column_name: str) -> ValidationResult:
        """All columns are sortable — kept for future constraint support."""
        if not self._registry.column_exists(table_name, column_name):
            return ValidationResult(
                status=ValidationStatus.ERROR,
                message=f"Column '{column_name}' does not exist in table '{table_name}'.",
            )
        return ValidationResult(
            status=ValidationStatus.OK,
            message=f"Column '{column_name}' is sortable.",
        )
