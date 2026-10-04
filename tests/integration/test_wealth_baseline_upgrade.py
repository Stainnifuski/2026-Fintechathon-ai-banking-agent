"""Scoped cleanup exercised in isolated SQLite and PostgreSQL, not production data."""
from copy import deepcopy
import pytest
from sqlalchemy import select,func
from tests.integration.test_wealth_bank import store,assess
from backend.app.domain.wealth_fixed_data import synthetic_payload
from backend.app.models import AccountORM,TransactionORM
from backend.app.models.wealth import WealthAssessmentORM,WealthOrderORM,WealthLotORM
from backend.app.models.wealth_research import WealthContextORM,WealthContextSelectionORM
from backend.app.services.bank import wealth_research
from backend.app.services.bank.wealth_baseline_upgrade import upgrade


@pytest.mark.asyncio
async def test_scoped_upgrade_preserves_other_account_and_assessment(store,monkeypatch):
    monkeypatch.setenv('WEALTH_DEMO_TOOLS_ENABLED','true')
    payload=synthetic_payload();payload['products']=deepcopy(payload['products'][:1])
    payload['products'][0]['history']=payload['products'][0]['history'][-3:]
    payload.update(dataset_id='upgrade-fixture',dataset_version='v1',mode='snapshot')
    async with store() as db:
        await assess(db)
        imported=await wealth_research.import_dataset(db,payload)
        assert imported['ok']
        db.add(WealthContextORM(context_id='owned-old',user_id='U1',account_id='A1',dataset_key=imported['dataset_key'],
            version=1,data={'disposable':True,'run_state':'ACTIVE','mode':'snapshot','business_at':'2026-09-30T06:00:00+00:00'}))
        db.add(WealthContextSelectionORM(user_id='U1',context_id='owned-old',version=1))
        await db.commit()
        assessments=await db.scalar(select(func.count()).select_from(WealthAssessmentORM))
        other=(await db.get(AccountORM,'A2')).balance_fen
        result=await upgrade(db,'U1','owned-old','a'*64,payload)
        assert result['balance_fen']==2000000 and result['deleted']['deleted_run_ids']==['owned-old']
        assert await db.get(AccountORM,'A1') is None
        assert (await db.get(AccountORM,'A2')).balance_fen==other
        assert await db.scalar(select(func.count()).select_from(WealthAssessmentORM))==assessments
        new=await db.get(WealthContextORM,result['run_id'])
        assert new.data['fixture_ready'] and all(not rows for rows in new.data['fixture_baseline']['tables'].values())
        assert new.dataset_key==imported['dataset_key']
        assert await db.scalar(select(func.count()).select_from(WealthOrderORM))==0
        assert await db.scalar(select(func.count()).select_from(WealthLotORM))==0
