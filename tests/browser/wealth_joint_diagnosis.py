"""Gated disposable-bank diagnosis. No model, network collection or fabricated material."""
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib
from backend.app.domain.wealth_portfolio_candidates import pipeline,reliable_family,sourced_value
from backend.app.domain.wealth_portfolio_eligibility import qualify_purchase
from backend.app.schemas.wealth_portfolio_candidates import CandidateFilter
from backend.app.services.orchestration.wealth_portfolio_data import canonical,save
from backend.app.services.orchestration.wealth_portfolio_evidence import derive


async def diagnose(db,user_id,provider,bank,*,derive_current=False):
    rows=await bank.products(user_id)
    assets=await bank.assets(user_id)
    old_version=provider.data_version
    capture_hash=None
    if derive_current:
        capture={'schema_version':'portfolio-bank-observation-v1','source':'protected-local-bank',
            'url':'http://127.0.0.1:8013/api/v1/wealth/products',
            'observed_at':datetime.now(timezone.utc).isoformat(),'business_at':assets['business_at'],
            'context_id':assets['context_id'],'products':rows}
        raw=canonical(capture);capture_hash=hashlib.sha256(raw).hexdigest()
        path=provider.root/'.deps/wealth_portfolio_step08/zero_candidates'/('products-'+capture_hash+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_bytes(raw)
        snapshot=derive(provider.root,old_version,path.relative_to(provider.root).as_posix(),capture_hash,
            datetime.now(timezone.utc),allow_current_catalog=True)
        save(snapshot,provider.root/'.deps/wealth_portfolio_data/snapshots')
        provider.data_version=snapshot.version;provider.daily_version=snapshot.daily_version
    facts,service=provider.bind(db,user_id,bank)
    current,index,at=await service.provider.candidates.current(user_id)
    result=pipeline(index,CandidateFilter(mode='general'),current,user_id,at)
    codes={r['product_id'] for r in rows}
    table=[]
    for row in rows:
        product=index.products.get(row['product_id'])
        decision=qualify_purchase(product,current,user_id,at,None if product else SimpleNamespace(product_id=row['product_id']))
        family,family_reasons=reliable_family(product,at) if product else (None,['PRODUCT_NOT_FOUND'])
        fields={field:({'status':product.fields[field].status,'value':product.fields[field].value,'reason':product.fields[field].reason}
            if product and field in product.fields else {'status':'missing','value':None}) for field in ('objective','strategy','benchmark','benchmark_name_only','formal_risk','family_id')}
        table.append({'product_id':row['product_id'],'product_version':row['version'],'disclosure_version':row['disclosure'].get('version'),
            'source':'current_protected_simulated_bank','dataset_key':row['dataset_key'],'category':row['category'],
            'level':row['product_risk_level'],'share_class':row.get('share_class',row['rules'].get('share_class')),
            'channel':current.channel,'status':row['status'],'purchase_open':row['purchase_open'],'data_stale':row['data_stale'],
            'nav_date':row.get('nav_date'),'nav_available_at':row.get('available_at'),
            'a_entity':product.product_id if product else None,'a_version':product.product_version if product else None,
            'scope':product.scope_status if product else None,'scope_reason':product.scope_reason if product else 'PRODUCT_NOT_FOUND',
            'family':family,'family_reasons':family_reasons,'qualification':decision.model_dump(mode='json',exclude={'evidence'}),
            'material':fields,'material_exclusion':result.exclusions.get(row['product_id'],[]),
            'material_origin':row.get('material_origin'),'current_binding':product.current_binding if product else None})
    eligible=[t for t in table if t['qualification']['purchase_eligible']]
    families=[f for f in result.family_candidates if any(c in codes for c in f.member_codes)]
    selected=service.provider.candidates.selector.select(families,set(),3) if families else []
    return {'environment':'fresh_isolated_joint_bank','context_id':assets['context_id'],'business_at':assets['business_at'],
        'old_data_version':old_version,'data_version':provider.data_version,'daily_version':provider.daily_version,
        'capture_sha256':capture_hash,'scope_id':assets['asset_summary']['asset_scope_id'],'assessment_level':current.assessment.capacity_level if current.assessment else None,
        'counts':{'bank_directory':len(rows),'bank_ids_in_a':sum(t['a_entity'] is not None for t in table),
            'in_scope':sum(t['scope']=='verified' for t in table),'qualified_shares':len(eligible),
            'qualified_families':len({t['family'] for t in eligible if t['family']}),
            'material_complete_families':len(families),'dpp_returned':len(selected)},
        'whole_a_pipeline':{'stages':result.stages,'status':result.status,'reason_codes':result.reason_codes},
        'table':table,'model_calls':0,'external_collection_requests':0}
