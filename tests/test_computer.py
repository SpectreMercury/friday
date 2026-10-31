import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_ai.approvals import Approvals
from local_ai.computer_tools import ComputerTools
from local_ai.pi_agent import UserTools
from local_ai.process_tools import run_process
from local_ai.store import Store
from local_ai.web_tools import PublicResolver, public_url


@pytest.mark.asyncio
async def test_non_admin_cannot_bypass_tool_visibility(tmp_path):
    store=Store(tmp_path/'db')
    jid=store.enqueue(2,2,'chat','list files')
    tools=UserTools(store,store.get(jid),lambda _:True,tmp_path/'users',admin=1)
    try:
        for name,args in [('computer_list',{}),('terminal',{'command':'pwd'})]:
            with pytest.raises(ValueError,match='管理员'):
                await tools.execute(name,args)
    finally:store.db.close()


@pytest.mark.asyncio
async def test_files_and_commands_require_exact_approval(tmp_path):
    approve=AsyncMock(return_value=False)
    tools=ComputerTools({'user_id':1},1,approve,home=tmp_path)
    file=tmp_path/'hello.txt';file.write_text('original')
    assert (await tools.execute('computer_read',{'path':str(file)}))['text']=='original'
    with pytest.raises(ValueError):
        await tools.execute('computer_write',{'path':str(file),'content':'replacement'})
    assert file.read_text()=='original'
    assert 'replacement' in approve.await_args.args[1]
    approve.return_value=True
    await tools.execute('computer_write',{'path':str(file),'content':'replacement'})
    assert file.read_text()=='replacement'
    with pytest.raises(ValueError):tools.path('/etc/passwd')
    hidden=tmp_path/'.env';hidden.write_text('secret')
    (tmp_path/'alias.txt').symlink_to(hidden)
    with pytest.raises(ValueError):tools.path(str(tmp_path/'alias.txt'))
    before=approve.await_count
    assert (await tools.execute('terminal',{'command':'pwd'}))['exit_code']==0
    assert approve.await_count==before
    approve.return_value=False
    with pytest.raises(ValueError):
        await tools.execute('terminal',{'command':'touch denied.txt'})
    assert not (tmp_path/'denied.txt').exists()


@pytest.mark.parametrize('url',['http://127.0.0.1','http://[::1]/','http://192.168.1.1',
    'http://169.254.169.254','http://localhost','file:///etc/passwd','https://user:pass@example.com','https://example.com:8080'])
def test_web_rejects_nonpublic_addresses(url):
    with pytest.raises(ValueError):public_url(url)


@pytest.mark.asyncio
async def test_dns_cannot_rebind_to_internal_address(monkeypatch):
    import socket
    loop=asyncio.get_running_loop()
    monkeypatch.setattr(loop,'getaddrinfo',AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))]))
    with pytest.raises(ValueError):await PublicResolver().resolve('example.com',80)


@pytest.mark.asyncio
async def test_approval_bound_to_user_job_and_one_execution(tmp_path):
    store=Store(tmp_path/'db');jid=store.enqueue(1,1,'chat','write');store.update(jid,status='running')
    manager=Approvals(store,1)
    manager.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(edit_reply_markup=AsyncMock())))
    task=asyncio.create_task(manager.request(store.get(jid),'write reviewed content'))
    await asyncio.sleep(0)
    token=next(iter(manager.pending))
    query=SimpleNamespace(data=f'approve:{token}:yes',answer=AsyncMock())
    update=SimpleNamespace(callback_query=query,effective_user=SimpleNamespace(id=2),effective_chat=SimpleNamespace(id=1,type='private'))
    await manager.callback(update,None)
    assert not task.done()
    update.effective_user.id=1
    await manager.callback(update,None)
    assert await task is True
    assert not manager.pending
    await manager.callback(update,None)
    assert '过期' in query.answer.await_args.args[0]
    store.close() if hasattr(store,'close') else store.db.close()


@pytest.mark.asyncio
async def test_cancel_invalidates_pending_approval(tmp_path):
    store=Store(tmp_path/'db');jid=store.enqueue(1,1,'chat','write');store.update(jid,status='running')
    manager=Approvals(store,1)
    message=SimpleNamespace(edit_reply_markup=AsyncMock())
    manager.bot=SimpleNamespace(send_message=AsyncMock(return_value=message))
    task=asyncio.create_task(manager.request(store.get(jid),'operation'))
    await asyncio.sleep(0)
    store.update(jid,status="cancelled")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert not manager.pending
    message.edit_reply_markup.assert_awaited_once()
    store.db.close()


@pytest.mark.asyncio
async def test_process_timeout_and_output_bound(tmp_path):
    import sys
    with pytest.raises(ValueError,match='已终止'):
        await run_process([sys.executable,'-c','import time;time.sleep(30)'],cwd=tmp_path,timeout=.1)
    result=await run_process([sys.executable,'-c','print("a"*100000)'],cwd=tmp_path)
    assert result['exit_code']==0 and result['truncated']
    assert len(result['stdout'])==32000
