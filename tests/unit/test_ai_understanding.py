import pytest
from backend.app.services.ai.nlu import NLUEngine, Extraction, amount_fen, offline_extract


@pytest.mark.parametrize('text,fen', [('300',30000), ('0.01',1), ('三百',30000), ('三百零五',30500), ('十二',1200), ('一万零三',1000300), ('三点零一',301)])
def test_exact_amount(text, fen):
    assert amount_fen(text) == fen


@pytest.mark.parametrize('text', ['0', '-1', '1.001', '1.000', 'NaN', 'Infinity', '三百五', '一百百', '二点一二三', '999999999999999999'])
def test_invalid_amount(text):
    with pytest.raises(ValueError): amount_fen(text)


@pytest.mark.parametrize('text', ['给李四转300元', '转三百块给李四，备注聚餐', '转给 李四 300元'])
def test_common_forms(text):
    result = offline_extract(text, {})
    assert result.recipient == '李四'
    assert amount_fen(result.amount_text) == 30000


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['明天转给李四300元', '每月转300元给李四', '给李四转300美元'])
async def test_scope_before_model(text):
    class Never:
        async def extract(self, *args): raise AssertionError('must not call model')
    assert (await NLUEngine(Never()).extract(text, {})).intent == 'unsupported'


@pytest.mark.asyncio
async def test_injectable_model_validation():
    class Bad:
        async def extract(self, *args): return {'intent': 'transfer', 'risk': 'green', 'execute': True}
    with pytest.raises(ValueError): await NLUEngine(Bad()).extract('转账', {})
