"""
Visualization Column Inference — Phase 10.4 Refinements

Provides heuristic column matching to pair a single numeric measure with
the best categorical identifier label (e.g. name, title, department) from
the target database table schema.
"""

from typing import List, Dict, Any

def infer_missing_columns(requested_columns: List[str], table_name: str, meta: Dict[str, Any]) -> List[str]:
    """
    Checks if requested_columns contains exactly one numeric column and no categorical/datetime columns.
    If so, scans the table schema to find the best identifier column and appends it.

    Priority:
      1. name columns        (contains 'name')
      2. title columns       (contains 'title', 'subject', 'label')
      3. code columns        (contains 'code', 'slug', 'key', 'sku')
      4. pk/id columns       (equals 'id', ends with '_id', starts with 'id_')
      5. other categorical   (string/text type fields)
    """
    if not table_name:
        return requested_columns

    table_schemas = meta.get("table_schemas", {})
    cols_info = table_schemas.get(table_name, {})
    if not cols_info:
        return requested_columns

    # 1. Classify the already requested columns
    req_num_cols = []
    req_cat_cols = []
    req_dt_cols = []

    for col in requested_columns:
        # Resolve exact casing matching the schema
        col_exact = col
        for schema_col in cols_info:
            if schema_col.lower() == col.lower():
                col_exact = schema_col
                break

        col_type = str(cols_info.get(col_exact, "")).upper()
        if "DATE" in col_type or "TIME" in col_type or "TIMESTAMP" in col_type:
            req_dt_cols.append(col_exact)
        elif any(t_part in col_type for t_part in ["INT", "NUMERIC", "REAL", "DOUBLE", "FLOAT", "DECIMAL", "PRECISION", "SERIAL"]):
            req_num_cols.append(col_exact)
        else:
            req_cat_cols.append(col_exact)

    # 2. Only infer if we have exactly one numeric column requested, and no category/datetime columns
    if len(req_num_cols) == 1 and len(req_cat_cols) == 0 and len(req_dt_cols) == 0:
        numeric_col = req_num_cols[0]
        candidate_cols = [c for c in cols_info.keys() if c.lower() != numeric_col.lower()]

        # Priority 1: name columns
        name_cols = [c for c in candidate_cols if "name" in c.lower()]
        # Priority 2: title columns
        title_cols = [c for c in candidate_cols if "title" in c.lower() or "subject" in c.lower() or "label" in c.lower()]
        # Priority 3: code columns
        code_cols = [c for c in candidate_cols if "code" in c.lower() or "key" in c.lower() or "slug" in c.lower()]
        # Priority 4: id/pk columns
        pk_cols = [c for c in candidate_cols if c.lower() == "id" or c.lower().endswith("_id") or c.lower().startswith("id_")]
        # Priority 5: other categorical columns (non-numeric, non-datetime text strings)
        other_cat_cols = []
        for c in candidate_cols:
            c_type = str(cols_info.get(c, "")).upper()
            if any(char_part in c_type for char_part in ["CHAR", "TEXT", "VARCHAR", "STRING", "BPCHAR"]):
                if c not in name_cols and c not in title_cols and c not in code_cols and c not in pk_cols:
                    other_cat_cols.append(c)

        # Select best column in priority order
        best_col = None
        if name_cols:
            best_col = name_cols[0]
        elif title_cols:
            best_col = title_cols[0]
        elif code_cols:
            best_col = code_cols[0]
        elif pk_cols:
            best_col = pk_cols[0]
        elif other_cat_cols:
            best_col = other_cat_cols[0]

        if best_col:
            new_columns = list(requested_columns)
            new_columns.append(best_col)
            print(f"[Visualization Inferrer] Automatically paired measure '{numeric_col}' with label column '{best_col}' from table '{table_name}'.")
            return new_columns

    return requested_columns
