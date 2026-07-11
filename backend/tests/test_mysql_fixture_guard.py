from conftest import _is_destructive_test_mysql_url_allowed


def test_mysql_fixture_allows_test_database_name(monkeypatch):
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_TEST_MYSQL", raising=False)
    assert _is_destructive_test_mysql_url_allowed(
        "mysql+aiomysql://duckdock:duckdock@127.0.0.1:3306/duckdock_test?charset=utf8mb4"
    )


def test_mysql_fixture_rejects_non_test_database_name_without_opt_in(monkeypatch):
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_TEST_MYSQL", raising=False)
    assert not _is_destructive_test_mysql_url_allowed(
        "mysql+aiomysql://duckdock:duckdock@127.0.0.1:3306/duckdock?charset=utf8mb4"
    )


def test_mysql_fixture_allows_non_test_database_name_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_TEST_MYSQL", "1")
    assert _is_destructive_test_mysql_url_allowed(
        "mysql+aiomysql://duckdock:duckdock@127.0.0.1:3306/duckdock?charset=utf8mb4"
    )
