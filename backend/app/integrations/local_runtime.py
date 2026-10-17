"""Explicit local simulation switches; regular startup retains normal limits."""
import os


def configure_local_test_limits(database_url: str) -> bool:
    enabled = os.getenv('BANK_LOCAL_TEST_MODE', 'false').lower()
    if enabled not in {'true', 'false'}:
        raise ValueError('BANK_LOCAL_TEST_MODE must be true or false')
    if enabled == 'false':
        return False
    if not database_url.startswith('sqlite+aiosqlite:'):
        raise ValueError('Local test switches require an explicitly selected SQLite simulation ledger')
    from . import wealth_authorization as policy
    if os.getenv('WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT') == 'true':
        policy.ASSESSMENT_DAILY_LIMIT = 0
    if os.getenv('WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT') == 'true':
        policy.ASSESSMENT_ROLLING_YEAR_LIMIT = 0
    return True
