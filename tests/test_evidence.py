from telegram.error import RetryAfter
from local_ai.tool_errors import ToolFailure
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from local_ai.store import Store
from local_ai.evidence import claims_media, media_state, snapshot, review_answer
from local_ai.bot import LocalBot


@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path/'db.sqlite3')
    yield value
    value.db.close()


def test_old_file_and_delivered_flag_cannot_prove_new_request(store,tmp_path):
    file=tmp_path/'old.png';Image.new('RGB',(8,8)).save(file)
    old=store.enqueue(1,1,'image','a cat')
    store.update(old,status='succeeded',result=str(file),delivered=1)
    request=store.enqueue(1,1,'chat','画一艘纸船')
    store.update(request,status='succeeded',result='图片正在生成中，完成后会自动发送给你。')
    follow=store.enqueue(1,1,'chat','你没发啊')
    facts=snapshot(store,store.get(follow))
    assert facts['recent_media'][0]['artifact_exists']
    assert facts['recent_media'][0]['delivery_state']=='legacy_or_suppressed_unverified'
    assert facts['recent_turns'][0]['submitted_media_ids']==[]
    verdict=review_answer(store,store.get(follow),'图片已经生成成功了，应该已经发送到你这里了，请检查聊天记录。')
    assert not verdict['accepted']
    assert '没有对应的新' in verdict['answer']
    assert '不能确认已送达' in verdict['answer']


def test_current_submission_gets_pending_not_completed_ack(store):
    parent=store.enqueue(1,1,'chat','画猫')
    child=store.enqueue(1,1,'image','cat',parent_job_id=parent)
    result=review_answer(store,store.get(parent),'图片已经生成并发送给你了。')
    assert result['answer']=='正在生成，完成后会发到这里。'
    assert snapshot(store,store.get(parent))['current_turn_media'][0]['id']==child


def test_receipts_and_snapshots_are_user_scoped(store,tmp_path):
    jid=store.enqueue(1,1,'image','cat')
    store.record_receipt(jid,'media',1,123)
    assert media_state(store,store.get(jid))['telegram_message_id']==123
    other=store.enqueue(2,2,'chat','我的图呢')
    assert snapshot(store,store.get(other))['recent_media']==[]


@pytest.mark.parametrize('text,expected',[
    ('图片已经生成好了，应该已经自动发送到你这里了。',True),
    ('图片正在生成中，完成后会自动发送给你。',True),
    ('没有实际提交生成请求，不能说已经生成。',False),
    ('你好，我可以帮你查资料。',False),
])
def test_media_claim_detection(text,expected):
    assert claims_media(text)==expected


@pytest.mark.asyncio
async def test_send_failure_then_receipt_persists_without_duplicate(store,tmp_path):
    file=tmp_path/'cat.png';Image.new('RGB',(8,8)).save(file)
    jid=store.enqueue(1,1,'image','cat');store.update(jid,status='succeeded',result=str(file))
    bot=LocalBot.__new__(LocalBot);bot.store=store;bot.is_allowed=lambda uid:True
    api=SimpleNamespace(send_photo=AsyncMock(side_effect=[RetryAfter(1),SimpleNamespace(message_id=77)]))
    app=SimpleNamespace(bot=api)
    with pytest.raises((RetryAfter,ToolFailure)):await bot.deliver(app,store.get(jid))
    failed=store.get(jid)
    assert failed['delivered']==0 and failed['retry_after']>0
    assert failed['delivery_attempts']==1 and failed['delivery_error']=='RetryAfter'
    assert store.receipt(jid,'media') is None
    await bot.deliver(app,store.get(jid))
    assert store.receipt(jid,'media')['message_id']==77
    assert store.get(jid)['delivered']==1
    assert store.get(jid)['delivery_error'] is None
    await bot.deliver(app,store.get(jid))
    assert api.send_photo.await_count==2


@pytest.mark.asyncio
async def test_missing_file_and_missing_receipt_never_count_as_delivery(store,tmp_path):
    jid=store.enqueue(1,1,'image','cat');store.update(jid,status='succeeded',result=str(tmp_path/'missing.png'))
    bot=LocalBot.__new__(LocalBot);bot.store=store;bot.is_allowed=lambda uid:True
    api=SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=None)))
    with pytest.raises(FileNotFoundError):await bot.deliver(SimpleNamespace(bot=api),store.get(jid))
    api.send_photo.assert_not_awaited()
    Image.new('RGB',(8,8)).save(tmp_path/'missing.png')
    with pytest.raises((RetryAfter,ToolFailure)):await bot.deliver(SimpleNamespace(bot=api),store.get(jid))
    assert store.get(jid)['delivered']==0
    assert store.receipt(jid,'media') is None



def test_internal_review_markers_never_become_user_reply(store):
    jid=store.enqueue(1,1,'chat','查图片')
    result=review_answer(store,store.get(jid),'[此前助手曾声称媒体生成或发送；聊天文字不是证据，必须核对当前工具状态。]')
    assert not result['accepted']
    assert '[此前' not in result['answer']


@pytest.mark.asyncio
async def test_final_review_cannot_replay_side_effects(tmp_path,monkeypatch):
    import asyncio
    import local_ai.pi_agent as module
    node=(module.ROOT/'vendor/node').resolve()
    (tmp_path/'vendor').mkdir();(tmp_path/'pi').mkdir()
    (tmp_path/'vendor/node').symlink_to(node)
    (tmp_path/'pi/runner.mjs').write_text('''
import {createInterface} from 'node:readline';
const send=x=>process.stdout.write(JSON.stringify(x)+'\\n');
createInterface({input:process.stdin}).on('line',line=>{
 const m=JSON.parse(line);
 if(m.type==='init')send({type:'final_check',id:'draft',answer:'图片已经生成成功了，已经发送给你了。'});
 if(m.type==='review_result')send({type:'tool_call',id:'mutation',name:'generate_image',args:{prompt:'a cat'}});
 if(m.type==='tool_result'){
  if(!m.error)throw Error('verification unexpectedly allowed mutation');
  send({type:'result',answer:'没有提交生成请求，之前的提示不准确。'});
 }
});
''')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'model_status',lambda _:[])
    store=Store(tmp_path/'db')
    try:
        jid=store.enqueue(1,1,'chat','核对输出')
        engine=SimpleNamespace(lock=asyncio.Lock(),start=AsyncMock(),stop=AsyncMock())
        answer=await module.PiAgent(engine,store,lambda _:True,tmp_path).run(store.get(jid))
        assert '没有提交' in answer
        assert store.db.execute("select count(*) from jobs where kind='image'").fetchone()[0]==0
        assert '校验阶段' in store.db.execute("select error from pi_tools where name='generate_image'").fetchone()[0]
    finally:store.db.close()


@pytest.mark.asyncio
async def test_permanent_delivery_failure_notifies_without_media_receipt(store,tmp_path):
    jid=store.enqueue(1,1,'image','cat');store.update(jid,status='succeeded',result=str(tmp_path/'missing.png'))
    bot=LocalBot.__new__(LocalBot);bot.store=store;bot.is_allowed=lambda uid:True
    api=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=88)))
    with pytest.raises(FileNotFoundError):await bot.deliver(SimpleNamespace(bot=api),store.get(jid))
    assert store.receipt(jid,'delivery_failure_notice')['message_id']==88
    assert store.receipt(jid,'media') is None
    assert media_state(store,store.get(jid))['delivery_state']=='failed_not_delivered'
