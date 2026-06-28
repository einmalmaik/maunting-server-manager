"""Tests for SQL parser logic - all offline, no DB needed."""
import sys
sys.path.insert(0, "/opt/msm/backend")

from services import postgres_service


def test_split_simple_statements():
    out = postgres_service._split_sql_statements("SELECT 1; SELECT 2; SELECT 3;")
    assert out == ["SELECT 1", "SELECT 2", "SELECT 3"], f"got {out}"


def test_split_no_trailing_semicolon():
    out = postgres_service._split_sql_statements("SELECT 1; SELECT 2")
    assert out == ["SELECT 1", "SELECT 2"]


def test_split_skips_empty_and_comments():
    out = postgres_service._split_sql_statements("""
    -- ein Kommentar
    SELECT 1;
    /* block
       comment */
    SELECT 2;
    """)
    assert out == ["SELECT 1", "SELECT 2"], f"got {out}"


def test_split_does_not_break_on_semicolon_in_string_literal():
    out = postgres_service._split_sql_statements("INSERT INTO t (a) VALUES ('a;b'); SELECT 1;")
    assert out == ["INSERT INTO t (a) VALUES ('a;b')", "SELECT 1"], f"got {out}"


def test_split_does_not_break_on_escaped_quote():
    out = postgres_service._split_sql_statements("INSERT INTO t VALUES ('it''s ok'); SELECT 2;")
    assert out == ["INSERT INTO t VALUES ('it''s ok')", "SELECT 2"], f"got {out}"


def test_split_does_not_break_on_dollar_quoted_string():
    sql = "CREATE FUNCTION f() RETURNS void AS $$ BEGIN RAISE NOTICE 'a;b'; END $$ LANGUAGE plpgsql; SELECT 1;"
    out = postgres_service._split_sql_statements(sql)
    assert len(out) == 2, f"got {out}"
    assert "RAISE NOTICE 'a;b'" in out[0]
    assert out[1] == "SELECT 1"


def test_split_does_not_break_on_named_dollar_quoted():
    sql = "CREATE FUNCTION f() RETURNS void AS $func$ BEGIN END $func$ LANGUAGE plpgsql; SELECT 2;"
    out = postgres_service._split_sql_statements(sql)
    assert len(out) == 2
    assert "BEGIN END" in out[0]
    assert out[1] == "SELECT 2"


def test_split_handles_parentheses_with_semicolons():
    sql = "SELECT COUNT(CASE WHEN x > 1 THEN 1; ELSE 0 END) FROM t;"
    out = postgres_service._split_sql_statements(sql)
    # Semicolon in CASE-WHEN ist syntaktisch falsch, aber unser Parser darf nicht
    # fälschlich splitten — wir respektieren Klammern.
    assert len(out) == 1


def test_is_read_only_select():
    assert postgres_service._is_read_only("SELECT 1")
    assert postgres_service._is_read_only("  select 1")
    assert postgres_service._is_read_only("-- comment\nSELECT 1")


def test_is_read_only_with():
    assert postgres_service._is_read_only("WITH x AS (SELECT 1) SELECT * FROM x")


def test_is_read_only_write_keywords():
    assert not postgres_service._is_read_only("INSERT INTO t VALUES (1)")
    assert not postgres_service._is_read_only("UPDATE t SET a=1")
    assert not postgres_service._is_read_only("DELETE FROM t")
    assert not postgres_service._is_read_only("CREATE TABLE x (id int)")
    assert not postgres_service._is_read_only("DROP TABLE x")
    assert not postgres_service._is_read_only("ALTER TABLE x ADD COLUMN y int")
    assert not postgres_service._is_read_only("TRUNCATE t")
    assert not postgres_service._is_read_only("VACUUM")
    assert not postgres_service._is_read_only("EXPLAIN ANALYZE INSERT INTO t VALUES (1)")


def test_is_read_only_show_explain():
    assert postgres_service._is_read_only("EXPLAIN SELECT 1")
    assert postgres_service._is_read_only("SHOW search_path")