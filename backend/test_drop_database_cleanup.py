import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from state.metadata_store import (
    get_metadata,
    set_selected_db,
    set_schema,
    set_tables,
    set_table_schemas,
    refresh_metadata_after_ddl,
    clear as clear_meta
)

def test_drop_database_cache_cleanup():
    print("Running DROP DATABASE Cache Cleanup Tests...")
    
    # 1. Initialize metadata store with mock schema for database 'mahek'
    clear_meta()
    set_selected_db("mahek")
    set_schema({"girl": ["id", "name"]})
    set_tables(["girl"])
    set_table_schemas({"girl": {"id": "integer", "name": "text"}})
    
    meta = get_metadata()
    print("Initial cache state:")
    print("  selected_db:", meta.get("selected_db"))
    print("  cached_schema_db:", meta.get("cached_schema_db"))
    print("  schema:", meta.get("schema"))
    print("  tables:", meta.get("tables"))
    print("  table_schemas:", meta.get("table_schemas"))
    
    assert meta.get("selected_db") == "mahek"
    assert meta.get("cached_schema_db") == "mahek"
    assert len(meta.get("schema")) > 0
    assert len(meta.get("tables")) > 0
    assert len(meta.get("table_schemas")) > 0
    
    # 2. Simulate dropping database 'mahek'
    # We pass a successful DDL execution of DROP DATABASE
    print("\nSimulating DROP DATABASE mahek;")
    refresh_metadata_after_ddl("DROP DATABASE mahek;", None, "DROP")
    
    # 3. Verify that the cache was cleared for database 'mahek'
    print("\nPost-drop cache state:")
    print("  selected_db:", meta.get("selected_db"))
    print("  cached_schema_db:", meta.get("cached_schema_db"))
    print("  schema:", meta.get("schema"))
    print("  tables:", meta.get("tables"))
    print("  table_schemas:", meta.get("table_schemas"))
    
    assert meta.get("selected_db") is None, "selected_db was not cleared!"
    assert meta.get("cached_schema_db") is None, "cached_schema_db was not cleared!"
    assert meta.get("schema") == {}, "schema was not cleared!"
    assert meta.get("tables") == [], "tables was not cleared!"
    assert meta.get("table_schemas") == {}, "table_schemas was not cleared!"
    
    print("\nDROP DATABASE Cache Cleanup Tests PASSED.")

if __name__ == "__main__":
    test_drop_database_cache_cleanup()
