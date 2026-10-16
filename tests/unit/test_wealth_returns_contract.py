"""收益录制跨语言协议校验；不宣称真实银行收益计算已完成。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.schemas.wealth_returns import ReturnsQuery, WealthReturns

ROOT = Path(__file__).resolve().parents[2]


def test_recorded_frontend_responses_match_shared_backend_contract():
    node = shutil.which('node')
    if node is None:
        pytest.skip('跨语言录制校验需要Node运行时')
    script = """
import {recordedReturns,returnsScenarios} from './frontend/src/components/Wealth/FixtureReturnsProvider.ts';
const samples=[];
for(const scenario of returnsScenarios){
  const all=recordedReturns({scope:'all',period:'cumulative'},scenario.id);
  for(const product of [null,...all.products.map(row=>row.product_id)]){
    for(const query of [{period:'cumulative'},...['day','month','year'].flatMap(period=>
      ['2026-10-07','2026-10-09','2026-09-01','2026-11-01'].map(anchor_date=>({period,anchor_date})))]){
      samples.push(recordedReturns({...query,scope:product?'product':'all',...(product?{product_id:product}:{})},scenario.id));
    }
  }
}
process.stdout.write(JSON.stringify(samples));
"""
    response = subprocess.run([node, '--experimental-strip-types', '--input-type=module', '-e', script],
                              cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True, timeout=30)
    samples = json.loads(response.stdout)
    assert len(samples) > 100
    for sample in samples:
        result = WealthReturns.model_validate(sample)
        assert result.data_source == 'contract_fixture_only'
        assert result.formula_version == 'recorded-contract-expectations-v1'


def test_returns_query_preserves_exact_share_and_calendar_scope():
    query = ReturnsQuery(scope='product', product_id='fixture-S2-C', period='day', anchor_date='2026-10-08')
    assert query.product_id == 'fixture-S2-C'
    assert query.anchor_date.isoformat() == '2026-10-08'
    for invalid in [dict(scope='product'), dict(scope='all', product_id='fixture-S2-C'),
                    dict(period='cumulative', anchor_date='2020-01-01'), dict(period='day', user_id='other'),
                    dict(period='quarter'), dict(anchor_date='2026-02-30')]:
        with pytest.raises(ValidationError):
            ReturnsQuery.model_validate(invalid)


def test_response_rejects_false_complete_and_inconsistent_contributions():
    record = dict(schema_version='wealth-returns-v1', asset_scope_id='fixture', snapshot_id='snapshot',
                  scope='all', product_id=None, period='day', start_date='2026-10-07', end_date='2026-10-07',
                  as_of='2026-10-09T12:00:00+08:00', timezone='Asia/Shanghai', coverage_start='2026-10-07',
                  status='complete', missing_fields=[], data_source='contract_fixture_only', valuation_as_of='2026-10-07',
                  formula_version='recorded-contract-expectations-v1',
                  summary=dict(period_pnl_fen=0,cumulative_pnl_fen=0,realized_pnl_fen=0,unrealized_pnl_fen=0,fees_fen=0),
                  points=[dict(period_start='2026-10-07',period_end='2026-10-07',pnl_fen=0,status='complete')],
                  products=[dict(product_id='fixture-S1-A', product_name='fixture',period_pnl_fen=0,cumulative_pnl_fen=0,status='complete')])
    WealthReturns.model_validate(record)
    for change in [dict(missing_fields=['cost']), dict(as_of='2026-10-09T12:00:00'),
                   dict(summary={**record['summary'],'period_pnl_fen':1}),
                   dict(points=[{**record['points'][0],'pnl_fen':None}]),
                   dict(summary={**record['summary'],'fees_fen':-1})]:
        with pytest.raises(ValidationError):
            WealthReturns.model_validate({**record, **change})
