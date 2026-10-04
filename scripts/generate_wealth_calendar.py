"""Explicit development command; runtime only reads the committed daily calendar."""
import json
import hashlib
from pathlib import Path
from datetime import date,timedelta


def main():
    root=Path(__file__).resolve().parents[1]/'config/wealth'
    sources=json.loads((root/'calendar_sources.json').read_text(encoding='utf-8'))
    start=date.fromisoformat(sources['coverage_start']);end=date.fromisoformat(sources['coverage_end'])
    rows=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset);source=sources['years'][str(day.year)]
        holiday=next((name for name,first,last in source['closures'] if f'{day.year}-{first}'<=day.isoformat()<=f'{day.year}-{last}'),None)
        rows.append({'date':day.isoformat(),'trading':day.weekday()<5 and holiday is None,
            'reason':holiday or ('周末休市' if day.weekday()>=5 else '工作日开市'),
            'source_status':source['source_status'],'source_url':source['source_url']})
    result={'schema_version':'cn-daily-calendar-v1','calendar_version':sources['calendar_version'],
        'coverage_start':start.isoformat(),'coverage_end':end.isoformat(),'days':rows,
        'source_sha256':hashlib.sha256(json.dumps(sources,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    path=root/'data/calendar.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n').encode())
    print(json.dumps({'days':len(rows),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__=='__main__':main()
