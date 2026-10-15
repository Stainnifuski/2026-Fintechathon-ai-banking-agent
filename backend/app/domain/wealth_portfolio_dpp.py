"""Historical research DPP/MMR only; production uses code/category display order."""
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Sequence
from ..schemas.wealth_portfolio_candidates import FamilyCandidate
from ..services.orchestration.wealth_portfolio_data import digest

FEATURE_VERSION = 'portfolio-bigram-tfidf-80-10-10-v1'
ALGORITHM_VERSION = 'portfolio-greedy-cholesky-v1'
STOP_EPS = 1e-10
NEGATIVE_EPS = 1e-9


def tokens(fields: dict[str, str | None]) -> Counter:
    """不跨字段或标点生成二元组，单字符保留。"""
    result = Counter()
    for field in ('objective', 'strategy', 'benchmark', 'tracking_target'):
        text = unicodedata.normalize('NFKC', fields.get(field) or '').lower()
        for fragment in re.findall(r'[^\W_]+', text, flags=re.UNICODE):
            result.update([fragment] if len(fragment) == 1 else [fragment[i:i+2] for i in range(len(fragment)-1)])
    return result


def dot(a: dict[str, float], b: dict[str, float]) -> float:
    """只按非零特征点积，不构建全池Gram矩阵。"""
    if len(a) > len(b): a, b = b, a
    value = math.fsum(v*b.get(k, 0.0) for k, v in a.items())
    if not math.isfinite(value) or value < -NEGATIVE_EPS or value > 1+NEGATIVE_EPS:
        raise ValueError('FEATURE_KERNEL_INVALID')
    return value


@dataclass
class Corpus:
    version: str
    vectors: dict[str, dict[str, float]]
    vocabulary: list[str]
    idf: dict[str, float]
    counts: dict[str, Counter]
    dimension: int


def build_corpus(pool: Sequence[FamilyCandidate]) -> Corpus:
    """完整合格池建立固定语料，质量统一q=1。"""
    members = sorted(pool, key=lambda p: p.family_id)
    if len({p.family_id for p in members}) != len(members): raise ValueError('DUPLICATE_FEATURE_FAMILY')
    counts = {p.family_id: tokens(p.text_evidence) for p in members}
    if any(not c for c in counts.values()): raise ValueError('FEATURE_TEXT_EMPTY')
    if any(not p.substantive_category or not p.issuer_identity for p in members): raise ValueError('FEATURE_MATERIAL_MISSING')
    df = Counter(t for c in counts.values() for t in c)
    idf = {t: 1+math.log((len(members)+1)/(df[t]+1)) for t in sorted(df)}
    vectors = {}
    for p in members:
        weights = {t: (1+math.log(c))*idf[t] for t, c in counts[p.family_id].items()}
        norm = math.sqrt(math.fsum(v*v for v in weights.values()))
        v = {'text:'+t: math.sqrt(.8)*w/norm for t, w in weights.items()}
        v['type:'+p.substantive_category] = math.sqrt(.1)
        v['issuer:'+p.issuer_identity] = math.sqrt(.1)
        if abs(math.fsum(x*x for x in v.values())-1) > 1e-12: raise ValueError('FEATURE_NORM_INVALID')
        vectors[p.family_id] = v
    version = digest({'feature': FEATURE_VERSION, 'algorithm': ALGORITHM_VERSION,
                      'members': [p.model_dump(mode='json', exclude={'shares'}) for p in members], 'counts': counts, 'idf': idf})
    dimension = len(idf)+len({p.substantive_category for p in members})+len({p.issuer_identity for p in members})
    return Corpus(version, vectors, list(idf), idf, counts, dimension)


def greedy(corpus: Corpus, excluded: set[str], limit: int = 3) -> dict:
    """Cholesky残差贪心，非全局最优保证；每批Y从空集开始。"""
    remaining = sorted(set(corpus.vectors)-excluded)
    cap = min(3, limit, len(remaining))
    if cap < 0: raise ValueError('SELECTION_LIMIT_INVALID')
    residual = {f: dot(corpus.vectors[f], corpus.vectors[f]) for f in remaining}
    factors = {f: [] for f in remaining}
    selected, trace = [], []
    reason = 'pool_exhausted' if not remaining else 'cardinality_limit'
    while len(selected) < cap:
        winner = min(remaining, key=lambda f: (-residual[f], f))
        pivot = residual[winner]
        if not math.isfinite(pivot) or pivot < -NEGATIVE_EPS: raise ValueError('DPP_NON_PSD_RESIDUAL')
        if pivot <= STOP_EPS:
            reason = 'feature_rank_exhausted'; break
        selected.append(winner); trace.append({'family_id': winner, 'residual_squared': pivot, 'logdet_gain': math.log(pivot)})
        remaining.remove(winner)
        for f in remaining:
            cross = dot(corpus.vectors[f], corpus.vectors[winner])-math.fsum(a*b for a, b in zip(factors[f], factors[winner]))
            factor = cross/math.sqrt(pivot)
            value = residual[f]-factor*factor
            if not math.isfinite(value) or value < -NEGATIVE_EPS: raise ValueError('DPP_NON_PSD_RESIDUAL')
            factors[f].append(factor); residual[f] = max(0.0, value)
    if len(corpus.vectors)-len(excluded & set(corpus.vectors)) < min(3, limit) and reason == 'cardinality_limit': reason = 'insufficient_candidates'
    return {'items': selected, 'trace': trace, 'stop_reason': reason, 'corpus_version': corpus.version,
            'n': len(corpus.vectors), 'dimension': corpus.dimension, 'q': 1, 'algorithm': ALGORITHM_VERSION}


class DppSelector:
    version = ALGORITHM_VERSION
    fixture_only = False

    def __init__(self) -> None:
        self.cache: dict[str, Corpus] = {}; self.builds = 0; self.last_result: dict = {}

    def corpus(self, pool: list[FamilyCandidate]) -> Corpus:
        key = digest({'feature': FEATURE_VERSION, 'members': [p.model_dump(mode='json', exclude={'shares'}) for p in sorted(pool, key=lambda p: p.family_id)]})
        if key not in self.cache:
            if len(self.cache) >= 8: self.cache.pop(next(iter(self.cache)))
            self.cache[key] = build_corpus(pool); self.builds += 1
        return self.cache[key]

    def select(self, pool: list[FamilyCandidate], excluded: set[str], limit: int) -> list[str]:
        self.last_result = greedy(self.corpus(pool), excluded, limit)
        return self.last_result['items']


def mmr(corpus: Corpus, excluded: set[str], limit: int = 3) -> dict:
    """固定lambda=.75的同池对照，不作为正式provider替身。"""
    remaining = sorted(set(corpus.vectors)-excluded); selected = []
    while remaining and len(selected) < min(3, limit):
        scores = {}
        for f in remaining:
            similarity = max((dot(corpus.vectors[f], corpus.vectors[j]) for j in selected), default=0.)
            if similarity < 1-STOP_EPS: scores[f] = .75-.25*similarity
        if not scores: break
        winner = min(scores, key=lambda f: (-scores[f], f)); selected.append(winner); remaining.remove(winner)
    return {'items': selected, 'algorithm': 'portfolio-mmr-lambda075-v1', 'corpus_version': corpus.version,
            'stop_reason': 'feature_duplicate' if remaining and len(selected)<min(3, limit) else 'cardinality_or_pool_limit'}


def diversity(corpus: Corpus, selected: list[str]) -> dict:
    """资料相似度指标，不是收益相关性或金融效果。"""
    distances = [1-dot(corpus.vectors[a], corpus.vectors[b]) for i,a in enumerate(selected) for b in selected[i+1:]]
    return {'ILAD': math.fsum(distances)/len(distances) if distances else None,
            'ILMD': min(distances) if distances else None}
