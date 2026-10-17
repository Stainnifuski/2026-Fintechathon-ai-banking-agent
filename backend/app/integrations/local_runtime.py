"""Explicit local simulation switches; regular startup retains normal limits."""
import os
from sqlalchemy.engine import make_url


def configure_local_test_limits(database_url: str) -> bool:
    enabled = os.getenv('BANK_LOCAL_TEST_MODE', 'false').lower()
    if enabled not in {'true', 'false'}:
        raise ValueError('BANK_LOCAL_TEST_MODE must be true or false')
    if enabled == 'false':
        return False
    url = make_url(database_url)
    isolated_postgres = (url.drivername == 'postgresql+asyncpg'
                         and (url.database or '').startswith('bank_local_'))
    if url.drivername != 'sqlite+aiosqlite' and not isolated_postgres:
        raise ValueError('Local test switches require SQLite or a dedicated bank_local_ PostgreSQL database')
    from . import wealth_authorization as policy
    if os.getenv('WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT') == 'true':
        policy.ASSESSMENT_DAILY_LIMIT = 0
    if os.getenv('WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT') == 'true':
        policy.ASSESSMENT_ROLLING_YEAR_LIMIT = 0
    return True
