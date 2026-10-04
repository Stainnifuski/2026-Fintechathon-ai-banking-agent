"""Explicit offline development generator. Runtime cannot call this entry point."""
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib
import json
from backend.app.domain.wealth_synthetic import generate,fingerprint,SEED
from backend.app.domain.wealth_calendar import load,CALENDAR_VERSION
from backend.app.domain.wealth_fixed_data import DATASET_VERSION
from backend.app.domain.wealth_bank_simulation import _generate_bank_products


def main():
    root=Path(__file__).resolve().parents[1]/'config/wealth/data';root.mkdir(parents=True,exist_ok=True)
    calendar,_=load();days=[date.fromisoformat(row['date']) for row in calendar['days'] if row['trading']]
    payload=generate(calendar_days=days)
    payload.update(dataset_version=DATASET_VERSION,generator_version='factor-cn-fixed-v1',calendar_version=CALENDAR_VERSION)
    for p in payload['products']:
        p.update(dataset_version=DATASET_VERSION,product_version=2,disclosure_version=2,rules_version='simulation-cn-fixed-v1')
        p['rules'].update(calendar_version=CALENDAR_VERSION,dataset_version=DATASET_VERSION)
        for point in p['history']:
            instant=datetime.fromisoformat(point['published_at']).astimezone(timezone.utc).isoformat()
            point.update(published_at=instant,available_at=instant)
    banks=_generate_bank_products(source_payload=payload)
    for p in banks:
        p.update(dataset_version=DATASET_VERSION,rules_version=p['rules_version']+'-cn-fixed-v1')
        p['rules'].update(calendar_version=CALENDAR_VERSION)
    payload.pop('fingerprint',None);payload['fingerprint']=fingerprint(payload)
    files={}
    for name,value in [('synthetic.json',payload),('bank_products.json',banks)]:
        raw=(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        (root/name).write_bytes(raw);files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    calendar_raw=(root/'calendar.json').read_bytes()
    files['calendar.json']={'sha256':hashlib.sha256(calendar_raw).hexdigest(),'bytes':len(calendar_raw)}
    products=payload['products']+banks
    manifest={'schema_version':'wealth-fixed-data-v1','data_version':DATASET_VERSION,'generator_version':'factor-cn-fixed-v1',
        'seed':SEED,'calendar_version':CALENDAR_VERSION,'coverage_start':'2021-10-01','coverage_end':'2027-12-31',
        'publication_rule':'18:00 Asia/Shanghai, stored/transmitted UTC','annual_fees_included':True,
        'simulation':True,'files':files,'products':[{'product_id':p['product_id'],'family_id':p['fund_family_id'],
            'template_id':p['rules'].get('source_template_id'),'inception_date':p['inception_date'],
            'rows':len(p['history']),'first_date':p['history'][0]['date'],'last_date':p['history'][-1]['date'],
            'history_sha256':fingerprint(p['history'])} for p in products]}
    (root/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({'product_count':len(products),'files':files}))


if __name__=='__main__':main()
