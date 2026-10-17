import pytest
from backend.app.integrations.local_runtime import configure_local_test_limits
from backend.app.integrations import wealth_authorization as policy


def test_regular_runtime_preserves_limits(monkeypatch):
    monkeypatch.delenv('BANK_LOCAL_TEST_MODE', raising=False)
    monkeypatch.setenv('WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT', 'true')
    monkeypatch.setenv('WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT', 'true')
    monkeypatch.setattr(policy, 'ASSESSMENT_DAILY_LIMIT', 2)
    monkeypatch.setattr(policy, 'ASSESSMENT_ROLLING_YEAR_LIMIT', 8)
    assert not configure_local_test_limits('postgresql+asyncpg://unused')
    assert (policy.ASSESSMENT_DAILY_LIMIT, policy.ASSESSMENT_ROLLING_YEAR_LIMIT) == (2, 8)


def test_explicit_sqlite_test_runtime_keeps_existing_test_preference(monkeypatch):
    monkeypatch.setenv('BANK_LOCAL_TEST_MODE', 'true')
    monkeypatch.setenv('WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT', 'true')
    monkeypatch.setenv('WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT', 'true')
    monkeypatch.setattr(policy, 'ASSESSMENT_DAILY_LIMIT', 2)
    monkeypatch.setattr(policy, 'ASSESSMENT_ROLLING_YEAR_LIMIT', 8)
    with pytest.raises(ValueError): configure_local_test_limits('postgresql+asyncpg://unused')
    assert configure_local_test_limits('sqlite+aiosqlite:////app/runtime/banking_agent.db')
    assert (policy.ASSESSMENT_DAILY_LIMIT, policy.ASSESSMENT_ROLLING_YEAR_LIMIT) == (0, 0)
