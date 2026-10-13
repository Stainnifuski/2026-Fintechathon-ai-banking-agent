"""既有原料只读适配，不导入采集器、不访问上游。"""
import hashlib
import json
import math
import re
from datetime import datetime, timezone, date
from pathlib import Path
from .wealth_collection_cache import read_protected_json, read_protected_bytes
from ..schemas.wealth_portfolio_data import RawReference, Observation, FieldFact, EvidenceRecord

PLACEHOLDERS={'','-','--','---','none','null','na','n/a','nan','暂无','暂无评级','该基金无跟踪标的','全部'}
ALIASES={
 'issuer_raw':['基金管理人','基金公司','管理人'],
 'objective':['投资目标'], 'strategy':['投资策略'],
 'benchmark':['业绩比较基准','比较基准'], 'tracking_target':['跟踪标的'],
 'raw_risk':['风险等级','风险评级','基金评级'],
 'purchase_status':['申购状态'], 'redemption_status':['赎回状态'],
 'purchase_minimum':['购买起点','起购金额'], 'purchase_limit':['日累计限定金额'],
}


def clean(value):
    if value is None or (isinstance(value,float) and not math.isfinite(value)):
        return None
    if isinstance(value,str):
        value=value.strip()
        if value.lower() in PLACEHOLDERS: return None
    return value


def records(payload: dict | list) -> list[dict]:
    """支持产品列表、products字典及item/value键值表，不静默丢异常结构。"""
    rows=payload if isinstance(payload,list) else payload.get('records',payload.get('products'))
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ValueError('RAW_STRUCTURE_UNSUPPORTED')
    if rows and all('字段' in row and '值' in row for row in rows):
        return [{str(row['字段']):row['值']} for row in rows]
    if rows and all('item' in row and 'value' in row for row in rows):
        # 重复键保留为多个观察，不取最后一个。
        return [{str(row['item']):row['value']} for row in rows]
    return rows


def merged_record(rows: list[dict]) -> dict:
    """仅用于识别实体；事实提取直接遍历所有行，避免重复键覆盖。"""
    value={}
    for row in rows: value.update(row)
    return value


def field_fact(observations: list[Observation], *, simulated=False) -> FieldFact:
    valid=[o for o in observations if o.value is not None]
    if not valid:
        return FieldFact(status='missing',reason='no_valid_saved_field',observations=observations)
    values={json.dumps(o.value,ensure_ascii=False,sort_keys=True,allow_nan=False) for o in valid}
    units={o.unit for o in valid}
    if len(values)>1 or len(units)>1:
        return FieldFact(status='conflict',reason='unaligned_or_conflicting_saved_observations',observations=observations)
    return FieldFact(value=valid[0].value,status='simulated' if simulated else 'observed',observations=observations)


def observation(value, field: str, ref: RawReference, *, unit=None, as_of=None) -> Observation:
    raw=value
    if isinstance(raw,float) and not math.isfinite(raw): raw=str(raw)
    normalized=clean(value)
    if normalized is not None and unit in ('yuan_per_share','yuan_per_10000_shares','percent'):normalized=str(normalized)
    return Observation(value=normalized,raw_value=raw,source=ref.function,field=field,
        reference=ref,observed_at=ref.observed_at,as_of=as_of,unit=unit,
        reason='placeholder_or_missing' if clean(value) is None else None)


def read_raw(root: Path, ref: RawReference) -> dict | list:
    payload=read_protected_json(root,ref.path,ref.sha256,'.deps/a1_collection/raw')
    if not isinstance(payload,dict) or payload.get('function')!=ref.function or payload.get('kwargs')!=ref.kwargs:
        raise ValueError('EVIDENCE_BINDING_MISMATCH')
    if payload.get('observed_at')!=ref.observed_at.isoformat().replace('+00:00','Z') and datetime.fromisoformat(payload['observed_at'].replace('Z','+00:00'))!=ref.observed_at:
        raise ValueError('EVIDENCE_TIME_MISMATCH')
    return payload


def validate_evidence(root: Path, payload: dict, authority_sources: dict, expected_codes: set[str] | None = None) -> EvidenceRecord:
    """登记正式证据：出处白名单、原文、实体、评级/关联文字均须匹配。"""
    from urllib.parse import urlparse
    record=EvidenceRecord.model_validate(payload)
    if expected_codes is not None and not set(record.codes)<=expected_codes:raise ValueError('EVIDENCE_ENTITY_NOT_IN_DIRECTORY')
    host=urlparse(record.url).hostname
    if authority_sources.get(host)!=record.source_role:
        raise ValueError('UNRECOGNIZED_AUTHORITY')
    if record.reference.function!='public_document' or record.reference.kwargs.get('url')!=record.url:
        raise ValueError('DOCUMENT_URL_BINDING_MISMATCH')
    prefix='config/wealth/portfolio_public_evidence' if record.reference.path.startswith('config/wealth/portfolio_public_evidence/') else '.deps/wealth_portfolio_data/public'
    text=read_protected_bytes(root,record.reference.path,record.reference.sha256,prefix).decode('utf-8-sig')
    if record.excerpt not in text:
        raise ValueError('EXCERPT_NOT_IN_DOCUMENT')
    if record.published_on is not None and str(record.published_on) not in record.excerpt and str(record.published_on) not in text:
        raise ValueError('PUBLICATION_NOT_BOUND_TO_EXCERPT')
    if record.kind=='risk' and not re.search(r'(风险等级|风险评级|低风险|中低风险|中风险|中高风险|高风险).*?R'+str(record.risk_level)+r'(?!\d)',record.excerpt,re.S):
        raise ValueError('FORMAL_RATING_NOT_PROVEN')
    if record.kind=='family' and not any(term in record.excerpt for term in ('同一基金','份额类别','份额代码','A类','A：','A 类','下属分级基金')):
        raise ValueError('SHARE_RELATIONSHIP_NOT_PROVEN')
    for code,share in record.share_classes.items():
        if share is not None and not (re.search(re.escape(code)+r'.{0,40}'+re.escape(share),record.excerpt,re.S)
            or re.search(re.escape(share)+r'\s*(?:类|类别)?(?:基金代码为)?[：:（(]?\s*'+re.escape(code),record.excerpt)
            or re.search(re.escape(share)+r'\s*[（(]基金代码[：:]\s*'+re.escape(code),record.excerpt)):
            raise ValueError('SHARE_CLASS_NOT_BOUND')
    if record.kind=='issuer' and record.issuer_name not in record.excerpt:raise ValueError('ISSUER_NOT_BOUND')
    if record.field_value is not None and record.field_value not in record.excerpt:raise ValueError('FIELD_VALUE_NOT_BOUND')
    return record


def registered_for(code: str, source_kind: str, entries: list[EvidenceRecord], cutoff: datetime) -> list[EvidenceRecord]:
    return [e for e in entries if code in e.codes and e.source_kind==source_kind
            and e.observed_at<=cutoff and (e.published_on is None or e.published_on<=cutoff.date())]
