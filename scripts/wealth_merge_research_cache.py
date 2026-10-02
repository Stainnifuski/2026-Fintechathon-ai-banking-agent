"""Administrator-only SQLite research cache transfer; no ledger tables copied.

Run against an isolated wealth-demo destination after schema creation. The source
is read-only. Existing account, order, settlement and authorization rows stay intact.
"""
import argparse
import sqlite3
from pathlib import Path


def transfer(source, destination, macros_only=False):
    source = Path(source).resolve(); destination = Path(destination).resolve()
    if source == destination or 'wealth-demo' not in destination.name:
        raise ValueError('ISOLATED_WEALTH_DEMO_DESTINATION_REQUIRED')
    if not source.is_file() or not destination.is_file():
        raise ValueError('EXISTING_DATABASES_REQUIRED')
    counts = {}
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(destination) as dst:
        src.execute('BEGIN')
        for table in (('wealth_macro',) if macros_only else ('wealth_fund_directory', 'wealth_fund_cache')):
            columns = [r[1] for r in dst.execute(f'PRAGMA table_info({table})')]
            if not columns or any(not c.replace('_','').isalnum() for c in columns):
                raise ValueError('RESEARCH_SCHEMA_REQUIRED')
            names = ','.join(columns); rows = src.execute(f'SELECT {names} FROM {table}').fetchall()
            dst.executemany(f'INSERT OR REPLACE INTO {table} ({names}) VALUES ({",".join("?" for _ in columns)})', rows)
            counts[table] = len(rows)
        dst.commit()
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True); parser.add_argument('--destination', required=True)
    parser.add_argument('--macros-only', action='store_true')
    args = parser.parse_args()
    print(transfer(args.source,args.destination,args.macros_only))
