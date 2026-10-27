from fastapi import APIRouter,Depends,Query
from sqlalchemy.ext.asyncio import AsyncSession
from ..database.connection import get_db
from ..middleware.auth import get_current_user
from ..schemas.bill_features import ClassificationUpdate,RuleDelete,BudgetUpdate,AnomalyReview,RecurringPlan,MerchantSplit,SplitSpec,ForecastSave
from .bill_foundations import guarded
from ..services.bank import bill_classification,bill_budgets,bill_reviews,bill_schedules,bill_forecasts,bill_merchants
router=APIRouter(prefix='/api/v1/bills',tags=['bill decisions'])

@router.put('/transactions/{tx_id}/classification')
async def correct(tx_id:str,body:ClassificationUpdate,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_classification.correct(db,user['user_id'],tx_id,body)

@router.get('/category-rules')
async def rules(user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_classification.rules(db,user['user_id'])

@router.delete('/category-rules/{merchant_id}')
async def remove_rule(merchant_id:str,body:RuleDelete,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_classification.remove_rule(db,user['user_id'],merchant_id,body.expected_version)

@router.get('/budgets')
async def budgets(month:str=Query(pattern=r'^\d{4}-\d{2}$'),user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_budgets.summary(db,user['user_id'],month)

@router.put('/budgets')
async def save_budget(body:BudgetUpdate,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_budgets.save(db,user['user_id'],body)

@router.put('/anomaly-reviews')
async def review(body:AnomalyReview,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_reviews.save_review(db,user['user_id'],body)

@router.post('/recurring/plans')
async def create_plan(body:RecurringPlan,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_schedules.save_plan(db,user['user_id'],body)

@router.put('/recurring/plans/{stream_id}')
async def update_plan(stream_id:str,body:RecurringPlan,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_schedules.save_plan(db,user['user_id'],body,stream_id)

@router.get('/forecasts')
async def forecasts(user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_forecasts.comparisons(db,user['user_id'])

@router.post('/forecasts')
async def save_forecast(body:ForecastSave,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_forecasts.save(db,user['user_id'],body.scenario)

@router.get('/merchants/{merchant_id}/merge-preview')
async def merge_preview(merchant_id:str,source_merchant_id:str,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_merchants.preview(db,user['user_id'],merchant_id,source_merchant_id)

@router.get('/merchant-history')
async def history(user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_merchants.history(db,user['user_id'])

@router.post('/merchant-history/{event_id}/undo')
async def undo(event_id:str,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_merchants.undo(db,user['user_id'],event_id)

@router.get('/merchants/{merchant_id}/split-preview')
async def split_preview(merchant_id:str,page:int=Query(1,ge=1,le=10000),user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_merchants.split_candidates(db,user['user_id'],merchant_id,page)

@router.post('/merchants/{merchant_id}/split')
async def split(merchant_id:str,body:MerchantSplit,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db,write=True):return await bill_merchants.split(db,user['user_id'],merchant_id,body)

@router.post('/merchants/{merchant_id}/split-preview')
async def split_selection_preview(merchant_id:str,body:SplitSpec,user=Depends(get_current_user),db:AsyncSession=Depends(get_db)):
    async with guarded(db):return await bill_merchants.split_preview(db,user['user_id'],merchant_id,body)
