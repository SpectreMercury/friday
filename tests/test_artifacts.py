from telegram.error import RetryAfter
from local_ai.tool_errors import ToolFailure
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from local_ai.store import Store
from local_ai.pi_agent import UserTools
from local_ai.evidence import review_answer


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'db')
    path=tmp_path/'cat.png'; Image.new('RGB',(8,8)).save(path)
    def add(kind='image',uid=1,chat=1,missing=False):
        jid=store.enqueue(uid,chat,kind,'a cat')
        store.update(jid,status='succeeded',result=str(path) if not missing else str(tmp_path/'missing.png'),delivered=1)
        return jid
    yield store,add
    store.db.close()


def tools_for(store,prompt,sender):
    jid=store.enqueue(1,1,'chat',prompt)
    return UserTools(store,store.get(jid),lambda _:True,sender=sender)


@pytest.mark.asyncio
async def test_all_images_beyond_recent_five_with_actual_receipts(setup):
    store,add=setup
    ids=[add() for _ in range(7)]
    add('video');add(uid=2);add(chat=2)
    sender=AsyncMock(side_effect=[SimpleNamespace(message_id=i+100) for i in range(7)])
    tools=tools_for(store,'把所有生成的照片都给我',sender)
    assert review_answer(store,tools.job,'已整理好，请查看。')['required_tool']=='list_media'
    page=await tools.execute('list_media',{})
    tools.record('list_media','{}',page,None)
    assert {r['id'] for r in page['items']}==set(ids)
    assert review_answer(store,tools.job,'已整理出 7 张已生成的照片。')['required_tool']=='send_media'
    result=await tools.execute('send_media',{'ids':ids})
    tools.record('send_media','{}',result,None)
    assert result['accepted']==7
    assert review_answer(store,tools.job,'已发送。')['answer']=='已发送 7 个文件。'
    await tools.execute('send_media',{'ids':ids})
    assert sender.await_count==7
    assert all(store.receipt(tools.job['id'],'artifact:'+i) for i in ids)


@pytest.mark.asyncio
async def test_partial_failure_retries_only_failed_items_and_new_turn_resends(setup):
    store,add=setup
    ids=[add(),add()]
    sender=AsyncMock(side_effect=[SimpleNamespace(message_id=11),RetryAfter(1),SimpleNamespace(message_id=12),SimpleNamespace(message_id=13)])
    tools=tools_for(store,'把照片发给我',sender)
    result=await tools.execute('send_media',{'ids':ids})
    assert (result['accepted'],result['failed'])==(1,1)
    tools.record('send_media','{}',result,None)
    assert '1 个发送失败' in review_answer(store,tools.job,'全部已经发给你。')['answer']
    result=await tools.execute('send_media',{'ids':ids})
    assert result['accepted']==2 and sender.await_count==3
    store.update(tools.job['id'],status='succeeded')
    follow=tools_for(store,'把照片再发给我',sender)
    await follow.execute('send_media',{'ids':ids[:1]})
    assert sender.await_count==4


@pytest.mark.asyncio
async def test_inventory_pagination_isolation_missing_files_and_readonly_intent(setup):
    store,add=setup
    for _ in range(51):add()
    missing=add(missing=True); foreign=add(uid=2)
    tools=tools_for(store,'查询已有照片',AsyncMock())
    first=await tools.execute('list_media',{})
    second=await tools.execute('list_media',{'offset':first['next_offset']})
    assert first['total']==52 and len(second['items'])==2 and second['next_offset'] is None
    tools.record('list_media','{}',first,None)
    assert review_answer(store,tools.job,'已有 52 个已生成记录。')['accepted']
    with pytest.raises(ValueError):await tools.execute('send_media',{'ids':[missing]})
    store.update(tools.job['id'],status='succeeded')
    tools=tools_for(store,'把照片发给我',AsyncMock())
    with pytest.raises(ValueError):await tools.execute('send_media',{'ids':[first['items'][0]['id'],foreign]})
    tools.artifacts.sender.assert_not_awaited()
    result=await tools.execute('send_media',{'ids':[missing]})
    assert result['failed']==1
    assert store.receipt(tools.job['id'],'artifact:'+missing) is None


@pytest.mark.asyncio
async def test_complaint_goes_to_agent_and_repair_can_deliver(tmp_path,monkeypatch):
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
 if(m.type==='init')send({type:'final_check',id:'draft',answer:'图片已经发送了。'});
 if(m.type==='review_result')send({type:'tool_call',id:'list',name:'list_media',args:{}});
 if(m.type==='tool_result' && m.id==='list')send({type:'tool_call',id:'send',name:'send_media',args:{ids:m.result.items.map(x=>x.id)}});
 if(m.type==='tool_result' && m.id==='send')send({type:'result',answer:'已经发送。'});
});
''')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    store=Store(tmp_path/'db')
    try:
        path=tmp_path/'cat.png';Image.new('RGB',(8,8)).save(path)
        image=store.enqueue(1,1,'image','cat');store.update(image,status='succeeded',result=str(path))
        jid=store.enqueue(1,1,'chat','图呢，把照片发给我')
        engine=SimpleNamespace(lock=asyncio.Lock(),start=AsyncMock(),stop=AsyncMock())
        sender=AsyncMock(return_value=SimpleNamespace(message_id=71))
        result=await module.PiAgent(engine,store,lambda _:True,tmp_path,sender=sender).run(store.get(jid))
        # This fake runner performs only tool I/O; inference is leased on demand.
        engine.start.assert_not_awaited()
        assert result=='已发送 1 个文件。'
        assert store.receipt(jid,'artifact:'+image)['message_id']==71
    finally:store.db.close()


@pytest.mark.asyncio
async def test_missing_receipt_revocation_and_cancellation(setup):
    store,add=setup
    image=add()
    sender=AsyncMock(return_value=SimpleNamespace(message_id=None))
    tools=tools_for(store,'把照片发给我',sender)
    assert (await tools.execute('send_media',{'ids':[image]}))['failed']==1
    assert store.receipt(tools.job['id'],'artifact:'+image) is None
    tools.allowed=lambda _:False
    with pytest.raises(ValueError):await tools.execute('send_media',{'ids':[image]})
    tools.allowed=lambda _:True
    store.update(tools.job['id'],status='cancelled')
    with pytest.raises(ValueError):await tools.execute('send_media',{'ids':[image]})
    assert sender.await_count==1


@pytest.mark.parametrize('prompt',['不要发照片给我，只查一下','不用给我发图片','do not send images'])
def test_no_delivery_for_negative_request(prompt):
    from local_ai.artifacts import wants_delivery
    assert not wants_delivery(prompt)


@pytest.mark.asyncio
async def test_selected_image_does_not_force_delivery_of_whole_inventory(setup):
    store,add=setup
    ids=[add(),add()]
    tools=tools_for(store,'把上一张照片发给我',AsyncMock(return_value=SimpleNamespace(message_id=91)))
    page=await tools.execute('list_media',{});tools.record('list_media','{}',page,None)
    result=await tools.execute('send_media',{'ids':ids[-1:]});tools.record('send_media','{}',result,None)
    assert review_answer(store,tools.job,'已发送。')['accepted']
    assert tools.artifacts.sender.await_count==1


@pytest.mark.parametrize('prompt',['照片发给我了吗','图片有没有发给我'])
def test_status_question_does_not_authorize_send(prompt):
    from local_ai.artifacts import wants_delivery
    assert not wants_delivery(prompt)
