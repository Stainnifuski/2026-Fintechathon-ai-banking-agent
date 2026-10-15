"""Real bank serializers on isolated SQLite/PostgreSQL stores, no collection."""
from copy import deepcopy
import pytest
from backend.app.services.bank import wealth_service as bank
from backend.app.models.wealth import WealthProductVersionORM
from backend.app.services.orchestration.wealth_portfolio_evidence import product_content
from backend.app.services.orchestration.wealth_portfolio_data import digest
from tests.integration.test_wealth_bank import store
from tests.integration.test_wealth_dynamic_quotes import setup


@pytest.mark.asyncio
async def test_fresh_import_matches_installed_snapshot_binding(store):
    import json
    from pathlib import Path
    from backend.app.services.orchestration.wealth_portfolio_joint import DATA
    root=Path(__file__).resolve().parents[2]
    path=root/'.deps/wealth_portfolio_data/snapshots'/f'{DATA}.json'
    if not path.exists():
        pytest.skip('Install the documented offline snapshot for fresh-bank binding verification')
    snapshot=json.loads(path.read_bytes())
    expected=next(p['current_binding']['content_sha256'] for p in snapshot['products'] if p['product_id']=='000003')
    del snapshot
    async with store() as db:
        await setup(db)
        for compact in (False,True):
            row=(await bank.products(db,'U1',facts_only=compact,product_ids=['000003']))[0]
            assert digest(product_content(row))==expected


@pytest.mark.asyncio
async def test_compact_full_nested_body_mutation_and_quotes(store):
    async with store() as db:
        _,_,cmd=await setup(db)
        full=(await bank.products(db,'U1',product_ids=['000003']))[0]
        slim=(await bank.products(db,'U1',facts_only=True,product_ids=['000003']))[0]
        expected=digest(product_content(full))
        assert digest(product_content(slim))==expected
        version=await db.get(WealthProductVersionORM,f'000003:{cmd.product_version}')
        version.disclosure={**version.disclosure,'fees':{'purchase':'0','notice':'nested rule'}}
        await db.flush()
        full=(await bank.products(db,'U1',product_ids=['000003']))[0]
        slim=(await bank.products(db,'U1',facts_only=True,product_ids=['000003']))[0]
        assert isinstance(slim['disclosure']['fees'],dict)
        assert digest(product_content(full))==digest(product_content(slim))!=expected
        changed=deepcopy(slim);changed.update(nav='99',nav_date='2099-01-01',available_at='2099-01-01T00:00:00Z')
        assert product_content(changed)==product_content(slim)
        version.disclosure={**version.disclosure,'risk':'modified without version bump'}
        await db.flush()
        current=(await bank.products(db,'U1',facts_only=True,product_ids=['000003']))[0]
        assert digest(product_content(current))!=digest(product_content(slim))
