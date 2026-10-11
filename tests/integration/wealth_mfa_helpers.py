"""Test-only helper: actual SMS challenge/validation via HTTP; never bypasses proof."""
import re
from backend.app.security import sms

async def sms_verify(client, base, *, version=None, reference=None):
    if version is not None and reference is None:
        preview=await client.post(base+'/preview',json={'version':version})
        assert preview.status_code==200,preview.text
        reference=preview.json()['verification_reference_id']
    reference=reference or base.rsplit('/',1)[-1]
    sent=await client.post('/api/v1/auth/sms/send',json={'purpose':'wealth','reference_id':reference})
    assert sent.status_code==200,sent.text
    inbox=await client.get('/api/v1/auth/sms/dev-inbox')
    proof=re.search(r'验证码 (\d{6})',inbox.json()[0]['content']).group(1)
    return await client.post(base+'/verify',json={'method':'sms','proof':proof,
        'challenge_id':sent.json()['challenge_id'],**({'version':version} if version is not None else {})})
