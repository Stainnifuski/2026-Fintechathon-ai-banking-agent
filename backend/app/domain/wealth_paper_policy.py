"""Approved competition policy: public identity/data, simulated execution.

No issuer-rating equivalence, expiry extension, fund-name inference or new cap.
"""
from datetime import datetime,date
from decimal import Decimal
import hashlib,json,re
from pathlib import Path
from functools import lru_cache

LEGACY_POLICY_VERSION='real-public-paper-risk-20261013-v1'
POLICY_VERSION='real-public-paper-core-risk-20261013-v2'
POLICY_VERSIONS={LEGACY_POLICY_VERSION,POLICY_VERSION}
LEVELS={'money_market':1,'money':1,'bond':2,'mixed':3,'equity':4}
PRICING_MODE='frozen-public-nav-v1'
MONEY_PRICING_MODE='paper-money-fixed-unit-v1'
PRICING_MODES={PRICING_MODE,MONEY_PRICING_MODE}

@lru_cache(maxsize=4)
def _registered_files(signature):
    """Read server-owned immutable registries once per file revision, not per code."""
    result={}
    for filename,mtime,size in signature:
        payload=json.loads(Path(filename).read_bytes())
        if payload.get('policy_version') not in POLICY_VERSIONS:raise ValueError('PAPER_REGISTERED_POLICY_VERSION')
        seen=set()
        for p in payload['products']:
            code=p['product_id'];policy=p['rules']['execution_policy']
            if code in seen:raise ValueError('PAPER_REGISTERED_IDENTITY_DUPLICATE')
            seen.add(code);result.setdefault(code,set()).add(policy_sha(policy))
    return result

def registered_policy_reasons(policy,code):
    """Server-controlled evidence manifest, not client-supplied classification.

    Covers the entire prepared quality intersection, never a seven-code
    production whitelist. A new evidence/policy version must be prepared and
    deployed explicitly, not fabricated by altering an order or DB rating.
    """
    try:
        paths=sorted((Path(__file__).resolve().parents[3]/'config/wealth').glob('paper_products*.json'))
        signature=tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in paths)
        expected=_registered_files(signature).get(code)
        if expected is None:return ['PAPER_REGISTERED_EVIDENCE_MISSING']
        if policy_sha(policy) not in expected:return ['PAPER_REGISTERED_EVIDENCE_CHANGED']
    except (OSError,ValueError,KeyError,TypeError):return ['PAPER_REGISTERED_EVIDENCE_UNAVAILABLE']
    return []

def policy_sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def validate_policy(policy,code,category,risk,at=None):
    if not isinstance(policy,dict):return ['PAPER_POLICY_MISSING']
    reasons=[]
    if (policy.get('version') not in POLICY_VERSIONS or policy.get('product_id')!=code
        or policy.get('underlying_source_kind')!='real' or policy.get('execution_source_kind')!='simulated'
        or ('money' if policy.get('category')=='money_market' else policy.get('category'))!=('money' if category=='money_market' else category) or policy.get('classification_status')!='verified'
        or LEVELS.get(category)!=risk or policy.get('risk_level')!=risk):reasons.append('PAPER_POLICY_IDENTITY_CONFLICT')
    required=('classification_sha256','material_sha256')
    if any(not isinstance(policy.get(k),str) or not re.fullmatch('[0-9a-f]{64}',policy[k]) for k in required):reasons.append('PAPER_EVIDENCE_UNBOUND')
    if policy.get('classification_conflict') or policy.get('identity_conflict'):reasons.append('PAPER_CLASSIFICATION_CONFLICT')
    ratings=policy.get('public_ratings',[])
    if not isinstance(ratings,list) or any(not isinstance(p,dict) for p in ratings):
        reasons.append('PUBLIC_RATING_UNBOUND');ratings=[]
    for public in ratings:
        if not re.fullmatch('[0-9a-f]{64}',str(public.get('reference_sha256',''))) or type(public.get('risk_level')) is not int:
            reasons.append('PUBLIC_RATING_UNBOUND')
        elif type(risk) is not int or public['risk_level']>risk:reasons.append('PUBLIC_HIGHER_RISK_REVIEW_REQUIRED')
    try:
        approved=datetime.fromisoformat(policy['approved_at'])
        if approved.tzinfo is None or (at is not None and approved>at):reasons.append('PAPER_POLICY_NOT_ACTIVE')
        quote=policy['quote'];available=datetime.fromisoformat(quote['available_at']);day=date.fromisoformat(quote['date'])
        price=Decimal(quote['value'])
        if available.tzinfo is None or not price.is_finite() or price<=0 or not quote.get('reference_sha256'):raise ValueError()
        if at is not None and (available>at or day>at.date()):reasons.append('PAPER_FUTURE_PRICE')
        if policy.get('pricing_mode')==MONEY_PRICING_MODE and (category not in ('money','money_market') or price!=1 or quote.get('source')!='project_simulated_fixed_one' or policy.get('real_unit_nav') is not None):
            reasons.append('PAPER_MONEY_PRICE_SOURCE_CONFLICT')
    except (KeyError,ValueError,TypeError,ArithmeticError):reasons.append('PAPER_PRICE_UNBOUND')
    # Explicit version, no artificial 30-day expiration.
    if 'expires_at' in policy:reasons.append('UNAPPROVED_POLICY_EXPIRY')
    return list(dict.fromkeys(reasons))
