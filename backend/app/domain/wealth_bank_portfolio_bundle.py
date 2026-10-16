"""Public fixed bank supplement, explicitly installed into an owned context.

The real-fund dataset and its 13,856 identities are unchanged. Additional
datasets are server-owned scope bindings, never client purchase permissions.
"""
from pathlib import Path
import json,hashlib
from copy import deepcopy

FILE='config/wealth/portfolio_bank_supplement.json'
POLICY='bank-cash-risk-quick-no-guarantee-v2'

def read(root):
    raw=(Path(root)/FILE).read_bytes();value=json.loads(raw)
    if value.get('schema_version')!=1 or value.get('policy_version')!=POLICY:raise ValueError('BANK_SUPPLEMENT_VERSION')
    if len({p['product_id'] for p in value['products']})!=len(value['products']):raise ValueError('BANK_SUPPLEMENT_IDENTITY')
    return value,hashlib.sha256(raw).hexdigest()

def augment(snapshot,root):
    from ..services.orchestration.wealth_portfolio_data import simulated_product,simulated_daily,reference,digest
    value,sha=read(root);ref=reference(Path(root),Path(root)/FILE,'simulation_config')
    existing={p.product_id for p in snapshot.products}
    rows=[p for p in value['products'] if p['product_id'] not in existing]
    products=[simulated_product(p,ref,True) for p in rows]
    for p,row in zip(products,rows):p.forms={**p.forms,'kind':'bank_wealth','quick_redemption':bool(row['rules'].get('quick_redemption'))}
    version=digest({'base':snapshot.version,'bank':sha,'policy':POLICY})
    daily=digest({'base':snapshot.daily_version,'bank':sha,'policy':POLICY})
    return snapshot.model_copy(update={'version':version,'fixed_version':version,'daily_version':daily,
        'products':snapshot.products+products,'daily':snapshot.daily+[simulated_daily(p,ref,snapshot.as_of) for p in rows],
        'input_references':snapshot.input_references+[ref]})

def versions(base,daily,root):
    from ..services.orchestration.wealth_portfolio_data import digest
    _,sha=read(root)
    return digest({'base':base,'bank':sha,'policy':POLICY}),digest({'base':daily,'bank':sha,'policy':POLICY})

def authorized_dataset_keys(context):
    """Only stored bindings on the authenticated owned context are accepted."""
    extra=context.data.get('additional_dataset_keys',[])
    if not isinstance(extra,list) or any(not isinstance(k,str) for k in extra):raise ValueError('CONTEXT_DATASET_BINDING_INVALID')
    return {context.dataset_key,*extra}
