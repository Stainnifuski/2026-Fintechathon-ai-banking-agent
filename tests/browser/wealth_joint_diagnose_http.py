"""Local public HTTP diagnosis. Credentials stay in memory, results are facts only."""
import os,json,re
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[2]
out=Path(os.environ['WEALTH_DIAG_OUTPUT']);out.parent.mkdir(parents=True,exist_ok=True)
credential=re.search(r"hash_password\('([^']+)'\)",(root/'tests/browser/wealth_joint_server.py').read_text(encoding='utf-8')).group(1)
with httpx.Client(base_url='http://127.0.0.1:8013',timeout=180,trust_env=False) as client:
    login=client.post('/api/v1/auth/login',json={'phone':'13800000001','password':credential})
    login.raise_for_status();client.headers['Authorization']='Bearer '+login.json()['access_token']
    questionnaire=client.get('/api/v1/wealth/questionnaire');questionnaire.raise_for_status()
    q=questionnaire.json()
    assessment=client.post('/api/v1/wealth/assessments',json={'questionnaire_version':q['version'] if 'version' in q else q['questionnaire_version'],
        'profile_version':1,'adult':True,'full_capacity':True,'answers':{p['question_id'] if 'question_id' in p else p['id']:2 for p in q['questions']}})
    assessment.raise_for_status();confirmed=client.post('/api/v1/wealth/assessments/'+assessment.json()['assessment_id']+'/confirm')
    confirmed.raise_for_status()
    initial=client.post('/api/v1/joint-test/diagnose',json={'derive_current':False});initial.raise_for_status()
    corrected=client.post('/api/v1/joint-test/diagnose',json={'derive_current':True});corrected.raise_for_status()
    result={'public_endpoints':[{'path':'/auth/login','http':login.status_code},{'path':'/wealth/questionnaire','http':questionnaire.status_code},
        {'path':'/wealth/assessments','http':assessment.status_code},{'path':'/wealth/assessments/{id}/confirm','http':confirmed.status_code}],
        'initial':initial.json(),'corrected':corrected.json()}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'initial':result['initial']['counts'],'corrected':result['corrected']['counts'],'data_version':result['corrected']['data_version']},ensure_ascii=False))
