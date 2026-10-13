"""固定同池DPP/MMR、投影/等权的独立评价，不推断金融收益。"""
import math
import statistics
from .wealth_portfolio_dpp import Corpus,greedy,mmr,diversity


def latency(samples: list[float]) -> dict:
    if not samples:return {'count':0,'median_seconds':None,'p95_seconds':None}
    values=sorted(samples)
    return {'count':len(values),'median_seconds':statistics.median(values),'p95_seconds':values[max(0,math.ceil(.95*len(values))-1)]}


def evaluate_selector(corpus: Corpus,algorithm: str,qualified: set[str],matched: set[str],material_denominator: int) -> dict:
    displayed=set();batches=[];pick=greedy if algorithm=='DPP' else mmr
    while True:
        result=pick(corpus,displayed);items=result['items']
        if not items:break
        batches.append({'items':items,**diversity(corpus,items),'matching_compliance':len(set(items)&matched)/len(items),
                        'qualification_compliance':len(set(items)&qualified)/len(items),'repeat_rate':len(set(items)&displayed)/len(items),'stop_reason':result['stop_reason'],'trace':result.get('trace',[])})
        displayed.update(items)
        if len(displayed)==len(corpus.vectors):break
    return {'algorithm':algorithm,'n':len(corpus.vectors),'dimension':corpus.dimension,'corpus_version':corpus.version,
            'matching_compliance':1.0 if batches and all(x['matching_compliance']==1 for x in batches) else (None if not batches else sum(x['matching_compliance'] for x in batches)/len(batches)),'qualification_compliance':1.0 if batches and all(x['qualification_compliance']==1 for x in batches) else (None if not batches else sum(x['qualification_compliance'] for x in batches)/len(batches)),'ILAD':batches[0]['ILAD'] if batches else None,'ILMD':batches[0]['ILMD'] if batches else None,'repeat_rate':max(x['repeat_rate'] for x in batches) if batches else None,'batches':batches,'display_coverage':len(displayed)/len(corpus.vectors) if corpus.vectors else None,
            'material_coverage':len(corpus.vectors)/material_denominator if material_denominator else None,
            'exhausted':len(displayed)==len(corpus.vectors), 'quality_q':1}


def evaluate_weights(outputs: dict,current: dict[str,int],total: int,model: dict,p_user: str | None,identity_complete: bool=True) -> dict:
    projected={};equal={};HHI={};eq_ok=True
    for bucket,out in outputs.items():
        HHI[bucket]={'projection':out['HHI'],'equal':out.get('equal_HHI'),'cash_included':out.get('cash_included',False),'equal_status':out['equal_weight']['status']}
        eq_ok &= out['equal_weight']['status']=='available'
        for i,target in enumerate(out['targets']):
            projected[target['family_id']]=target['target_fen']/total
            equal[target['family_id']]=out['equal_weight']['weights'][i]
    union=set(current)|set(projected)
    turnover=lambda values:.5*math.fsum(abs(values.get(f,0)-current.get(f,0)/total) for f in union)
    return {'bucket_HHI':HHI,'projection_adjustment_proxy':turnover(projected) if identity_complete else None,
            'equal_adjustment_proxy':turnover(equal) if identity_complete and eq_ok else None,
            'equal_status':'available' if eq_ok else 'baseline_infeasible','identity_complete':identity_complete,
            'preference_error':abs(model['actual_p']-float(p_user)) if model['actual_p'] is not None and p_user is not None else None,
            'metric_boundary':'document_diversity_and_amount_adjustment_proxy; not_return_or_fee'}


