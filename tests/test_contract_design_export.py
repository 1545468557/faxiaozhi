from copy import deepcopy
import pytest
from docx import Document
from app.contract_notes import validate_review_notes
from app.errors import ApiError
from app.prompts import build_contract_risk_prompt
from app.render.docx import document_text
from app import server
from app.auth import guard
from tests.test_contract import run_contract

@pytest.mark.parametrize('value', [[], {'other':{'status':'accept','suggestion':'x'}}, {'r1':{'status':'verified','suggestion':'x'}}, {'r1':{'status':'accept','suggestion':' '}}, {'r1':{'status':'pending','suggestion':'x'*10001}}])
def test_invalid_human_notes_rejected(value):
    with pytest.raises(ApiError):
        validate_review_notes(value,[{'riskId':'r1'}])

async def test_human_notes_reach_word_without_changing_original_risks(monkeypatch):
    session,_ = await run_contract()
    original = deepcopy(session.contract['risks'])
    risk_id = original[0]['riskId']
    monkeypatch.setattr(server.STORE, 'get', lambda sid: session)
    result = await server.contract_export(session.id, {'review':{risk_id:{'status':'accept','suggestion':'人工调整为验收后支付尾款。'}}})
    # 导出文件按用户分目录（2026-09-27 隔离改造）。这条用例直接调函数、没有请求上下文，
    # 所以落在 `_anonymous` 桶里 —— 路径从 `guard` 取，别写死中间那一层。
    export_dir = guard.current_owner_dir(server.get_config().exports_dir)
    text = document_text(Document(export_dir / result['filename']))
    assert '人工调整为验收后支付尾款。' in text
    assert '已采纳' in text
    assert '未进行自动法律核验' in text
    assert session.contract['risks'] == original
