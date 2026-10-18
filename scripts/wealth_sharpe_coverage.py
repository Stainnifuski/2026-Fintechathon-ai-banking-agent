"""Read-only audit of the pinned pool against AKShare's Xueqiu analysis source.

Uses the same URL/field as fund_individual_analysis_xq, without importing the
optional pandas/AKShare runtime. Writes research evidence only, never bank data.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.request

URL = 'https://danjuanfunds.com/djapi/fund/base/quote/data/index/analysis/{}'


def fetch(code):
    started = datetime.now(timezone.utc).isoformat()
    for attempt in range(2):
        try:
            req = urllib.request.Request(URL.format(code), headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read()
            body = json.loads(raw)
            data = body.get('data')
            if body.get('result_code') != 0 or not isinstance(data, dict):
                return dict(code=code, status='source_error', result_code=body.get('result_code'), observed_at=started)
            if data.get('fund_code') != code:
                return dict(code=code, status='identity_unavailable' if not data.get('fund_code') else 'identity_mismatch',
                            observed_at=started, raw=body, sha256=hashlib.sha256(raw).hexdigest())
            values = {}
            for row in data.get('index_data_list') or []:
                value = (row.get('self_index') or {}).get('sharpe_rank')
                try:
                    number = float(value) if value is not None and not isinstance(value, bool) else math.nan
                except (ValueError, TypeError):
                    number = math.nan
                if math.isfinite(number):
                    values[row.get('index_time_period', row.get('range', 'unknown'))] = number
            return dict(code=code, status='available' if values else 'no_numeric_sharpe',
                        values=values, observed_at=started, sha256=hashlib.sha256(raw).hexdigest(), raw=body)
        except Exception as error:
            if attempt == 0:
                time.sleep(.5)
            else:
                return dict(code=code, status='request_failed', error=type(error).__name__, observed_at=started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.deps/sharpe_coverage_latest')
    parser.add_argument('--resume', action='store_true', help='Keep successful observations; retry only unavailable codes')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pool = json.loads((root/'config/wealth/compact_pool.json').read_text(encoding='utf-8'))
    products = json.loads((root/'config/wealth/paper_products.json').read_text(encoding='utf-8'))['products']
    categories = {p['source_product_code']: p['category'] for p in products if p.get('source_product_code') in pool['codes']}
    assert len(categories) == pool['count']
    assert dict(Counter(categories.values())) == pool['categories']
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)
    rows = {}
    if args.resume and (output/'observations.jsonl').exists():
        previous=[json.loads(line) for line in (output/'observations.jsonl').read_text(encoding='utf-8').splitlines()]
        rows={row['code']:row for row in previous if row['code'] in pool['codes'] and row['status'] in {'available','no_numeric_sharpe'}}
    with ThreadPoolExecutor(max_workers=6) as workers, (output/'observations.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows.values():stream.write(json.dumps(row,ensure_ascii=False)+'\n')
        jobs = [workers.submit(fetch, code) for code in pool['codes'] if code not in rows]
        for job in as_completed(jobs):
            row = job.result(); row['category'] = categories[row['code']]
            rows[row['code']] = row
            stream.write(json.dumps(row, ensure_ascii=False)+'\n'); stream.flush()
            if len(rows) % 100 == 0:
                print(f"checked={len(rows)}/{pool['count']} statuses={dict(Counter(r['status'] for r in rows.values()))}", flush=True)
    summary = dict(observed_at=datetime.now(timezone.utc).isoformat(), source=URL,
                   method='AKShare fund_individual_analysis_xq source; self_index.sharpe_rank',
                   pool_version=pool['version'], groups={})
    for category in ['all', 'bond', 'mixed', 'equity']:
        group = [r for r in rows.values() if category == 'all' or r['category'] == category]
        counts = dict(Counter(r['status'] for r in group))
        periods = {p: sum(p in r.get('values', {}) for r in group) for p in ['近1年', '近3年', '近5年']}
        summary['groups'][category] = dict(total=len(group), statuses=counts, periods=periods,
            any_period=sum(bool(r.get('values')) for r in group),
            coverage_percent=round(100*sum(bool(r.get('values')) for r in group)/len(group), 2))
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
