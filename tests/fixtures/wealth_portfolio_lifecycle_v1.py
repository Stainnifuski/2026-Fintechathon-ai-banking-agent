"""显式隔离生命周期样例；不加载生产银行库。"""
from copy import deepcopy
from datetime import timedelta
from tests.fixtures.wealth_portfolio_model_v1 import model_fixture
from tests.fixtures.wealth_portfolio_candidates_v1 import NOW
from backend.app.services.orchestration.wealth_portfolio_lifecycle import PortfolioLifecycle
from backend.app.services.orchestration.wealth_portfolio_store import MemoryPortfolioStore
from backend.app.services.orchestration.wealth_portfolio_candidates import CandidateError

class FixtureAccess:
    def __init__(self):
        self.grant={'user_id':'fixture-user','context_id':'fixture-context','context_epoch':1,'session_ref':'fixture-session',
            'asset_scope_id':'fixture-linked-scope','account_ids':['fixture-account'],'can_read':True,'can_edit':True}
    async def resolve(self,user_id,context_id,*,write,portfolio_id=None):
        if user_id!=self.grant['user_id']:raise CandidateError('PORTFOLIO_NOT_FOUND',404)
        if context_id!=self.grant['context_id']:raise CandidateError('PORTFOLIO_CONTEXT_STALE')
        return deepcopy(self.grant)

def lifecycle_fixture(level=4,holding=True):
    provider,data,facts,bundle=model_fixture(level,holding);clock=[NOW]
    provider.candidates.clock=lambda:clock[0]
    provider.asset_source.bundle['context_epoch']=1
    # 使用实际builder返回的scope，不将单context账户当正式全量来源。
    access=FixtureAccess();access.grant['asset_scope_id']=bundle['summary']['asset_scope_id']
    store=MemoryPortfolioStore(lambda:clock[0])
    return PortfolioLifecycle(provider,store,access),data,facts,bundle,clock
