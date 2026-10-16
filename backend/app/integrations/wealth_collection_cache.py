"""A1研究原料只读入口；复用现有基金缓存ORM，不触发联网或批准交易。"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..models.wealth_research import WealthFundCacheORM


def read_collection_evidence(db: Session, cache_key: str, repository_root: Path) -> dict:
    """读取已采集原始证据，校验文件路径、指纹及原始函数/参数绑定。"""
    if not cache_key.startswith('a1:'):
        raise ValueError('A1_CACHE_NAMESPACE_REQUIRED')
    cached=db.get(WealthFundCacheORM,cache_key)
    if cached is None or cached.data_type!='a1_collection' or cached.status!='READY':
        raise ValueError('LOCAL_EVIDENCE_UNAVAILABLE')
    metadata=cached.data
    raw=read_protected_json(repository_root, metadata['raw_path'], metadata['raw_sha256'], '.deps/a1_collection/raw')
    if raw['function']!=metadata['collection_function'] or raw['kwargs']!=metadata['kwargs']:
        raise ValueError('EVIDENCE_BINDING_MISMATCH')
    return {'raw':raw,'metadata':metadata,'external_requests':0,'approved_trade_product':False}


def read_protected_bytes(repository_root: Path, relative_path: str, sha256: str, allowed_directory: str) -> bytes:
    """共用本地证据保护：边界和指纹均核实后才返回内容。"""
    root=repository_root.resolve()
    directory=(root/allowed_directory).resolve()
    file=(root/relative_path).resolve()
    if not directory.is_relative_to(root) or not file.is_relative_to(directory):
        raise ValueError('EVIDENCE_PATH_DENIED')
    content=file.read_bytes()
    if hashlib.sha256(content).hexdigest()!=sha256:
        raise ValueError('EVIDENCE_FINGERPRINT_MISMATCH')
    return content

def read_protected_json(repository_root: Path, relative_path: str, sha256: str, allowed_directory: str) -> dict | list:
    content=read_protected_bytes(repository_root,relative_path,sha256,allowed_directory)
    if relative_path.endswith('.gz'):
        content=gzip.decompress(content)
    return json.loads(content)
