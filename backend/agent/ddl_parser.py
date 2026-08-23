import re
from dataclasses import dataclass
from typing import Optional

@dataclass
class DDLParseResult:
    operation: str
    sql: Optional[str]
    deterministic: bool
    clarification_needed: bool
    clarification_message: Optional[str]
    table_name: Optional[str] = None


def parse_simple_ddl(instruction: str, session: dict = None) -> Optional[DDLParseResult]:
    instr = instruction.strip()
    session = session or {}

    # Helper function to check if string contains only valid SQL identifier characters
    def is_valid_identifier(name: str) -> bool:
        return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))

    def split_top_level_commas(s: str) -> list[str]:
        parts = []
        current = []
        paren_depth = 0
        for char in s:
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            if char == ',' and paren_depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        if current:
            parts.append("".join(current).strip())
        return [p for p in parts if p]

    # 1. CREATE DATABASE
    match_create_db_1 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+(?:a\s+)?|let's\s+)?(?:create|make|build|generate|add|setup|construct|prepare|new)\s+(?:a\s+)?(?:new\s+)?(?:database|db)\s+(?:called\s+|named\s+|under\s+the\s+name\s+|for\s+)?(?P<db_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_create_db_2 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+(?:a\s+)?|let's\s+)?(?:create|make|build|generate|add|setup|construct|prepare|new)\s+(?P<db_name>[a-zA-Z0-9_-]+)\s+(?:database|db)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_create_db = match_create_db_1 or match_create_db_2
    if match_create_db:
        db_name = match_create_db.group("db_name")
        if is_valid_identifier(db_name):
            return DDLParseResult(
                operation="CREATE_DATABASE",
                sql=f"CREATE DATABASE {db_name};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 2. DROP DATABASE
    match_drop_db_1 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+to\s+|let's\s+)?(?:drop|delete|remove|destroy)\s+(?:the\s+|an\s+)?(?:database|db)\s+(?:called\s+|named\s+)?(?P<db_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_drop_db_2 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+to\s+|let's\s+)?(?:drop|delete|remove|destroy)\s+(?P<db_name>[a-zA-Z0-9_-]+)\s+(?:database|db)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_drop_db = match_drop_db_1 or match_drop_db_2
    if match_drop_db:
        db_name = match_drop_db.group("db_name")
        if is_valid_identifier(db_name):
            return DDLParseResult(
                operation="DROP_DATABASE",
                sql=f"DROP DATABASE {db_name};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 3. DROP TABLE
    match_drop_tbl_1 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+to\s+|let's\s+)?(?:drop|delete|remove|destroy)\s+(?:the\s+|a\s+)?table\s+(?P<table_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_drop_tbl_2 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+to\s+|let's\s+)?(?:drop|delete|remove|destroy)\s+(?P<table_name>[a-zA-Z0-9_-]+)\s+table\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_drop_tbl = match_drop_tbl_1 or match_drop_tbl_2
    if match_drop_tbl:
        table_name = match_drop_tbl.group("table_name")
        if is_valid_identifier(table_name):
            return DDLParseResult(
                operation="DROP_TABLE",
                sql=f"DROP TABLE {table_name};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 4. RENAME TABLE
    match_rename_tbl_1 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|let's\s+)?(?:rename|alter)\s+(?:the\s+|a\s+)?table\s+(?P<old_tbl>[a-zA-Z0-9_-]+)\s+to\s+(?P<new_tbl>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_rename_tbl_2 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|let's\s+)?(?:rename|alter)\s+(?P<old_tbl>[a-zA-Z0-9_-]+)\s+table\s+to\s+(?P<new_tbl>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_rename_tbl = match_rename_tbl_1 or match_rename_tbl_2
    if match_rename_tbl:
        old_tbl = match_rename_tbl.group("old_tbl")
        new_tbl = match_rename_tbl.group("new_tbl")
        if is_valid_identifier(old_tbl) and is_valid_identifier(new_tbl):
            return DDLParseResult(
                operation="RENAME_TABLE",
                sql=f"ALTER TABLE {old_tbl} RENAME TO {new_tbl};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 5. ALTER TABLE (Add column, remove column, rename column)
    # Add column:
    match_add_col = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|let's\s+)?(?:alter\s+table\s+(?P<tbl1>[a-zA-Z0-9_-]+)\s+)?add\s+(?:column\s+)?(?P<col>[a-zA-Z0-9_-]+)(?:\s+(?P<type>[a-zA-Z0-9_()]+))?(?:\s+(?:to|in)\s+(?:table\s+)?(?P<tbl2>[a-zA-Z0-9_-]+))?\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    if match_add_col:
        tbl = match_add_col.group("tbl1") or match_add_col.group("tbl2") or session.get("selected_table")
        col = match_add_col.group("col")
        typ = match_add_col.group("type")
        if typ and typ.lower() in ("column", "col"):
            typ = None
        if tbl and is_valid_identifier(tbl) and is_valid_identifier(col):
            if not typ:
                col_l = col.lower()
                if col_l == "id":
                    typ = "SERIAL PRIMARY KEY"
                elif col_l == "name":
                    typ = "VARCHAR(255)"
                elif col_l == "salary":
                    typ = "NUMERIC"
                elif col_l == "date":
                    typ = "DATE"
                elif col_l == "created_at":
                    typ = "TIMESTAMP"
                elif any(x in col_l for x in ["amount", "count", "age", "num", "year", "month", "day"]):
                    typ = "INT"
                else:
                    typ = "VARCHAR(255)"
            return DDLParseResult(
                operation="ALTER_TABLE",
                sql=f"ALTER TABLE {tbl} ADD COLUMN {col} {typ};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # Drop column:
    match_drop_col = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|let's\s+)?(?:alter\s+table\s+(?P<tbl1>[a-zA-Z0-9_-]+)\s+)?(?:drop|remove|delete)\s+(?:column\s+)?(?P<col>[a-zA-Z0-9_-]+)(?:\s+column)?(?:\s+from\s+(?:table\s+)?(?P<tbl2>[a-zA-Z0-9_-]+))?\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    if match_drop_col:
        tbl = match_drop_col.group("tbl1") or match_drop_col.group("tbl2") or session.get("selected_table")
        col = match_drop_col.group("col")
        if tbl and is_valid_identifier(tbl) and is_valid_identifier(col):
            return DDLParseResult(
                operation="ALTER_TABLE",
                sql=f"ALTER TABLE {tbl} DROP COLUMN {col};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # Rename column:
    match_rename_col = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|let's\s+)?(?:alter\s+table\s+(?P<tbl1>[a-zA-Z0-9_-]+)\s+)?rename\s+(?:column\s+)?(?P<old_col>[a-zA-Z0-9_-]+)\s+to\s+(?P<new_col>[a-zA-Z0-9_-]+)(?:\s+(?:in|of)\s+(?:table\s+)?(?P<tbl2>[a-zA-Z0-9_-]+))?\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    if match_rename_col:
        tbl = match_rename_col.group("tbl1") or match_rename_col.group("tbl2") or session.get("selected_table")
        old_col = match_rename_col.group("old_col")
        new_col = match_rename_col.group("new_col")
        if tbl and is_valid_identifier(tbl) and is_valid_identifier(old_col) and is_valid_identifier(new_col):
            return DDLParseResult(
                operation="ALTER_TABLE",
                sql=f"ALTER TABLE {tbl} RENAME COLUMN {old_col} TO {new_col};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 6. CREATE TABLE (with columns)
    match_create_tbl_cols1 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+a\s+|let's\s+)?(?:create|make|build|new)\s+table\s+(?P<table_name>[a-zA-Z0-9_-]+)\s+with\s+columns\s+(?P<cols_str>.+?)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_create_tbl_cols2 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+a\s+|let's\s+)?(?:create|make|build|new)\s+table\s+(?P<table_name>[a-zA-Z0-9_-]+)\s*\((?P<cols_str>[^)]+)\)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_create_tbl = match_create_tbl_cols1 or match_create_tbl_cols2
    if match_create_tbl:
        table_name = match_create_tbl.group("table_name")
        cols_str = match_create_tbl.group("cols_str")
        if is_valid_identifier(table_name):
            col_parts = split_top_level_commas(cols_str)
            formatted_cols = []
            for part in col_parts:
                subparts = part.split()
                if len(subparts) == 1:
                    col_name = subparts[0]
                    col_l = col_name.lower()
                    if col_l == "id":
                        typ = "SERIAL PRIMARY KEY"
                    elif col_l == "name":
                        typ = "VARCHAR(255)"
                    elif col_l == "salary":
                        typ = "NUMERIC"
                    elif col_l == "date":
                        typ = "DATE"
                    elif col_l == "created_at":
                        typ = "TIMESTAMP"
                    elif any(x in col_l for x in ["amount", "count", "age", "num", "year", "month", "day"]):
                        typ = "INT"
                    else:
                        typ = "VARCHAR(255)"
                    formatted_cols.append(f"    {col_name} {typ}")
                else:
                    formatted_cols.append(f"    {part}")
            sql = f"CREATE TABLE {table_name} (\n" + ",\n".join(formatted_cols) + "\n);"
            return DDLParseResult(
                operation="CREATE_TABLE",
                sql=sql,
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 7. CREATE TABLE (No columns specified)
    match_no_cols1 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+a\s+|let's\s+)?(?:create|make|build|new)\s+table\s+(?P<table_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_no_cols2 = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+a\s+|let's\s+)?(?:create|make|build|new)\s+(?P<table_name>[a-zA-Z0-9_-]+)\s+table\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    match_no_cols = match_no_cols1 or match_no_cols2
    if match_no_cols:
        table_name = match_no_cols.group("table_name")
        if is_valid_identifier(table_name):
            return DDLParseResult(
                operation="CREATE_TABLE",
                sql=None,
                deterministic=False,
                clarification_needed=True,
                clarification_message=f"What columns would you like in table {table_name}?\n\nExample:\n* id INTEGER\n* name TEXT",
                table_name=table_name
            )

    # 8. GENERAL DDL SEMANTIC NORMALIZATION LAYER
    # Normalizes natural-language table management requests into structured DDLParseResult
    clean_instr = re.sub(r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+(?:need|want)\s+(?:to\s+)?|let's\s+)?", "", instr, flags=re.IGNORECASE).strip()
    
    # Informational & Discussion Guard — Do NOT classify discussion/retrieval/meta requests as DDL
    if re.search(r"^\s*(?:show\s+me\s+info|explain|how\s+to|how\s+do|information\s+about|help\s+with|tell\s+me\s+about)\b", clean_instr, re.IGNORECASE):
        return None

    is_table_op = bool(re.search(r"\b(?:table|storage)\b", instr, re.IGNORECASE))
    is_create_action = bool(re.search(r"\b(?:create|make|build|add|setup|set\s+up|start|construct|prepare|new|need|want)\b", instr, re.IGNORECASE))

    if is_table_op and is_create_action and not re.search(r"\b(?:drop|delete|remove|destroy|alter|rename|list|show)\b", instr, re.IGNORECASE):
        # Extract column clause if present
        col_match = re.search(
            r"(?P<kw>\b(?:with\s+columns|with|containing|fields\s+are|fields|columns|includes|including)\b|\()\s*(?P<cols>[^;]+?)\s*;?\s*$",
            clean_instr,
            re.IGNORECASE
        )
        
        cols_str = None
        clause_start_pos = len(clean_instr)
        if col_match:
            cols_str = col_match.group("cols")
            if col_match.group("kw") == "(" and cols_str.endswith(")"):
                cols_str = cols_str[:-1].strip()
            clause_start_pos = col_match.start()
            
        tbl_part = clean_instr[:clause_start_pos].strip()

        # Check if tbl_part is actually an inline list of column names without a table name (e.g. "create table id, marks" or "add table id and two")
        if re.search(r"^\s*(?:create|make|build|add|setup|set\s+up|start|construct|prepare|new|need|want)\s+(?:a\s+|new\s+)?table\s+[a-zA-Z0-9_-]+\s*(?:and|&|,)\s*", tbl_part, re.IGNORECASE):
            extracted_tbl = None
            if not cols_str:
                cols_str = re.sub(r"^\s*(?:create|make|build|add|setup|set\s+up|start|construct|prepare|new|need|want)\s+(?:a\s+|new\s+)?table\s+", "", clean_instr, flags=re.IGNORECASE).strip()
        else:
            # Extract table name from tbl_part
            tbl_match = re.search(
                r"\b(?:table\s+(?:called\s+|named\s+|for\s+)?|storage\s+for\s+)?(?P<tbl>[a-zA-Z0-9_-]+)(?:\s+table)?\b",
                tbl_part,
                re.IGNORECASE
            )

            stopwords = {
                "create", "make", "build", "add", "setup", "set", "up", "start", "construct",
                "prepare", "new", "need", "want", "table", "storage", "called", "named", "for",
                "a", "an", "the", "with", "columns", "containing", "fields", "are", "and", "or", "in", "on"
            }

            extracted_tbl = None
            if tbl_match:
                cand = tbl_match.group("tbl")
                if cand.lower() not in stopwords and is_valid_identifier(cand):
                    extracted_tbl = cand

            if not extracted_tbl:
                tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", tbl_part)
                for t in reversed(tokens):
                    if t.lower() not in stopwords and is_valid_identifier(t):
                        extracted_tbl = t
                        break

        # Case 1: Missing table name (e.g. "add table mine, duo") -> CLARIFICATION
        if not extracted_tbl:
            formatted_cols_desc = f" ({cols_str})" if cols_str else ""
            return DDLParseResult(
                operation="CREATE_TABLE",
                sql=None,
                deterministic=False,
                clarification_needed=True,
                clarification_message=f"You specified columns{formatted_cols_desc}, but did not provide a table name. What would you like to name this new table?",
                table_name=None
            )

        # Case 2: Table name present + Columns specified
        if cols_str:
            normalized_cols = re.sub(r"\s+(?:and|&)\s+", ", ", cols_str, flags=re.IGNORECASE)
            col_parts = split_top_level_commas(normalized_cols)
            valid_col_parts = []
            for p in col_parts:
                p_clean = re.sub(r"^(?:an|a|the)\s+", "", p.strip(), flags=re.IGNORECASE).strip()
                if p_clean and p_clean.lower() not in {"an", "a", "the"}:
                    valid_col_parts.append(p_clean)

            if valid_col_parts:
                formatted_cols = []
                for part in valid_col_parts:
                    subparts = part.split()
                    if len(subparts) == 1:
                        col_name = subparts[0]
                        col_l = col_name.lower()
                        if col_l == "id":
                            typ = "SERIAL PRIMARY KEY"
                        elif col_l == "name":
                            typ = "VARCHAR(255)"
                        elif col_l == "salary":
                            typ = "NUMERIC"
                        elif col_l == "date":
                            typ = "DATE"
                        elif col_l == "created_at":
                            typ = "TIMESTAMP"
                        elif any(x in col_l for x in ["amount", "count", "age", "num", "year", "month", "day"]):
                            typ = "INT"
                        else:
                            typ = "VARCHAR(255)"
                        formatted_cols.append(f"    {col_name} {typ}")
                    else:
                        formatted_cols.append(f"    {part}")

                sql = f"CREATE TABLE {extracted_tbl} (\n" + ",\n".join(formatted_cols) + "\n);"
                return DDLParseResult(
                    operation="CREATE_TABLE",
                    sql=sql,
                    deterministic=True,
                    clarification_needed=False,
                    clarification_message=None,
                    table_name=extracted_tbl
                )

        # Case 3: Table name present, but no columns specified (e.g. "create table users")
        return DDLParseResult(
            operation="CREATE_TABLE",
            sql=None,
            deterministic=False,
            clarification_needed=True,
            clarification_message=f"What columns would you like in table {extracted_tbl}?\n\nExample:\n* id INTEGER\n* name TEXT",
            table_name=extracted_tbl
        )

    # 9. CREATE INDEX
    match_create_idx = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|let's\s+)?create\s+(?:an\s+)?index\s+(?P<idx_name>[a-zA-Z0-9_-]+)\s+on\s+(?P<tbl_name>[a-zA-Z0-9_-]+)\s*\(\s*(?P<col_name>[a-zA-Z0-9_-]+)\s*\)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    if match_create_idx:
        idx = match_create_idx.group("idx_name")
        tbl = match_create_idx.group("tbl_name")
        col = match_create_idx.group("col_name")
        if is_valid_identifier(idx) and is_valid_identifier(tbl) and is_valid_identifier(col):
            return DDLParseResult(
                operation="CREATE_INDEX",
                sql=f"CREATE INDEX {idx} ON {tbl} ({col});",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    # 9. DROP INDEX
    match_drop_idx = re.search(
        r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|i\s+need\s+to\s+|let's\s+)?(?:drop|delete|remove|destroy)\s+(?:the\s+)?index\s+(?P<idx_name>[a-zA-Z0-9_-]+)\s*;?\s*$",
        instr,
        re.IGNORECASE
    )
    if match_drop_idx:
        idx = match_drop_idx.group("idx_name")
        if is_valid_identifier(idx):
            return DDLParseResult(
                operation="DROP_INDEX",
                sql=f"DROP INDEX {idx};",
                deterministic=True,
                clarification_needed=False,
                clarification_message=None
            )

    return None
