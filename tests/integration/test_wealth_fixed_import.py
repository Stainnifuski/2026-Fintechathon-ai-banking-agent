from copy import deepcopy
import pytest
from tests.integration.test_wealth_bank import store
from backend.app.domain.wealth_fixed_data import synthetic_payload
from backend.app.services.bank.wealth_research import import_dataset,catalog
from backend.app.services.bank.transfer_service import BankError
from datetime import datetime


@pytest.mark.asyncio
async def test_fixed_import_is_immutable_and_publication_filters_future(store):
    # Minimal two-product slice of actual fixed files, explicitly a test dataset.
    payload=synthetic_payload();payload['dataset_id']='fixed-import-test';payload['products']=payload['products'][:2]
    for p in payload['products']:
        p['history']=[r for r in p['history'] if '2026-09-28'<=r['date']<='2026-09-30']
    payload.pop('fingerprint',None)
    async with store() as db:
        first=await import_dataset(db,payload);await db.commit()
        again=await import_dataset(db,payload)
        assert again['duplicate'] and again['written']==0
        before=await catalog(db,first['dataset_key'],datetime.fromisoformat('2026-09-30T17:59:00+08:00'))
        after=await catalog(db,first['dataset_key'],datetime.fromisoformat('2026-09-30T18:00:00+08:00'))
        assert before[0]['nav_date']=='2026-09-29' and after[0]['nav_date']=='2026-09-30'
        changed=deepcopy(payload);changed['products'][0]['history'][-1]['nav']='2.00000000'
        with pytest.raises(BankError,match='不可覆盖'):await import_dataset(db,changed)
