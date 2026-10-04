import json
from decimal import Decimal
from pathlib import Path
from datetime import datetime
import pytest
from backend.app.domain.wealth_fixed_data import synthetic_payload,bank_products,DIRECTORY,load_file
from backend.app.domain.wealth_synthetic import fingerprint
from backend.app.domain.wealth_calendar import day_info
from datetime import date


def test_all_fixed_prices_match_manifest_and_have_no_preinception_rows():
    manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    products=synthetic_payload()['products']+bank_products()
    assert len(products)==78
    assert {p['product_id'] for p in products}=={f'S{i:03}' for i in range(1,61)}|{f'B{i:03}' for i in range(1,19)}
    entries={r['product_id']:r for r in manifest['products']}
    for p in products:
        history=p['history'];entry=entries[p['product_id']]
        assert entry['history_sha256']==fingerprint(history)
        assert entry['rows']==len(history)
        assert history[0]['date']>=p['inception_date'] and history[-1]['date']=='2027-12-31'
        assert all(Decimal(r['nav'])>0 and len(r['nav'].split('.')[1])==8 and day_info(date.fromisoformat(r['date']))['trading'] for r in history)
        assert all(datetime.fromisoformat(r['available_at']).hour==10 and datetime.fromisoformat(r['available_at']).utcoffset().total_seconds()==0 for r in history)
        assert p['rules']['calendar_version']==manifest['calendar_version']


def test_runtime_readers_do_not_generate(monkeypatch):
    from backend.app.domain import wealth_synthetic,wealth_bank_simulation
    def forbidden(*args,**kwargs):raise AssertionError('runtime generation forbidden')
    monkeypatch.setattr(wealth_synthetic,'generate',forbidden)
    monkeypatch.setattr(wealth_bank_simulation,'_generate_bank_products',forbidden)
    from backend.app.services.bank.wealth_preload import bank_products as runtime_banks
    first=runtime_banks();second=runtime_banks()
    assert fingerprint(first)==fingerprint(second)
    assert len(synthetic_payload()['products'])==60


def test_fixed_file_change_and_missing_rejected(tmp_path):
    (tmp_path/'manifest.json').write_text(json.dumps({'files':{'synthetic.json':{'sha256':'wrong'}}}))
    (tmp_path/'synthetic.json').write_text('{}')
    with pytest.raises(ValueError,match='FINGERPRINT'):load_file('synthetic.json',tmp_path)
    with pytest.raises(ValueError,match='MISSING'):load_file('other.json',tmp_path)
