import asyncio
import json
from pathlib import Path

import pytest

from local_ai.evolution import CORE_FILES
from local_ai.mcp_runtime import MCPRuntime
from local_ai.sessions import RevisionConflict, SessionSkills
from local_ai.store import Store
from scripts.friday_updater import Updater, base, build_id, atomic


@pytest.fixture
def workspace(tmp_path):
    store=Store(tmp_path/'data/db',skills_root=tmp_path/'skills')
    owner=store.create_account('Owner',is_admin=True);friend=store.create_account('Friend')
    a=store.create_session(owner['id'],'A');b=store.create_session(owner['id'],'B')
    a=store.update_session(owner['id'],a['id'],a['revision'],management=True)
    yield store,owner,friend,a,b
    store.db.close()


def stage(store,owner,session,kind,name,files):
    e=store.evolution
    return e.stage(owner['id'],session['id'],kind,name,files,'验证功能；保持旧版本可恢复',e.current(kind,name))


@pytest.mark.asyncio
async def test_skill_version_rollback_and_session_snapshot(workspace):
    store,owner,friend,a,b=workspace;e=store.evolution
    r=stage(store,owner,a,'skill','writer',{'SKILL.md':'Writer\nVersion one'})
    assert not store.skill_catalog()
    await e.publish(owner['id'],a['id'],r['id'])
    a=store.update_session(owner['id'],a['id'],a['revision'],skill_names=['writer'])
    jid=store.enqueue(owner['user_id'],None,'chat','first',session_id=a['id'],channel='app')
    updated=stage(store,owner,a,'skill','writer',{'SKILL.md':'Writer\nVersion two'})
    await e.publish(owner['id'],a['id'],updated['id'])
    assert SessionSkills(store,jid).read('writer')['text'].endswith('one')
    assert store.require_session(owner['id'],b['id'])['skill_names']==[]
    rollback=e.rollback_draft(owner['id'],a['id'],r['id'])
    await e.publish(owner['id'],a['id'],rollback['id'])
    assert (store.skills_root/'writer/SKILL.md').read_text().endswith('one')
    assert len(e.listing(owner['id'],a['id'])['items'])>=3


def test_stage_has_no_execution_and_forbids_escapes_and_other_sessions(workspace,tmp_path):
    store,owner,friend,a,b=workspace;e=store.evolution
    marker=tmp_path/'bad'
    r=stage(store,owner,a,'mcp','demo',{'server.py':f'open({str(marker)!r},"w").write("executed")'})
    assert not marker.exists()
    with pytest.raises(PermissionError):e.get(owner['id'],b['id'],r['id'])
    with pytest.raises(PermissionError):e.stage(friend['id'],a['id'],'skill','x',{'SKILL.md':'x'},'x','')
    with pytest.raises((ValueError,PermissionError)):stage(store,owner,a,'skill','../x',{'SKILL.md':'x'})
    with pytest.raises(ValueError):stage(store,owner,a,'core','friday',{'local_ai/mobile_auth.py':'pass'})
    with pytest.raises(ValueError):stage(store,owner,a,'core','friday',{'.env':'TOKEN=x'})
    with pytest.raises(SyntaxError):stage(store,owner,a,'mcp','syntax',{'server.py':'this is : not python'})


@pytest.mark.asyncio
async def test_revision_conflicts_do_not_overwrite_newer_skill(workspace):
    store,owner,_,a,_=workspace;e=store.evolution
    first=stage(store,owner,a,'skill','writer',{'SKILL.md':'One'})
    second=stage(store,owner,a,'skill','writer',{'SKILL.md':'Two'})
    await e.publish(owner['id'],a['id'],first['id'])
    with pytest.raises(RevisionConflict):await e.publish(owner['id'],a['id'],second['id'])
    assert (store.skills_root/'writer/SKILL.md').read_text()=='One'


def server(version):
    return f'''import os
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("fixture")
@mcp.tool()
def echo(value: str) -> dict:
    """Return version and supplied text."""
    return {{"version":{version!r},"value":value,"leaked":bool(os.getenv("TELEGRAM_BOT_TOKEN"))}}
if __name__=="__main__":mcp.run(transport="stdio")
'''


@pytest.mark.asyncio
async def test_real_mcp_protocol_task_pin_scope_and_no_secret_inheritance(workspace,monkeypatch):
    store,owner,_,a,b=workspace;e=store.evolution
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','must-not-leak')
    one=stage(store,owner,a,'mcp','echo',{'server.py':server('one')})
    published=await e.publish(owner['id'],a['id'],one['id'])
    assert published['report']['protocol_tools'][0]['name']=='echo'
    e.bind(owner['id'],a['id'],['echo'],0)
    jid=store.enqueue(owner['user_id'],None,'chat','first',session_id=a['id'],channel='app')
    two=stage(store,owner,a,'mcp','echo',{'server.py':server('two')})
    await e.publish(owner['id'],a['id'],two['id'])
    runtime=MCPRuntime(store)
    value=await runtime.for_job(store.get(jid),'echo','echo',{'value':'hello'})
    structured=value['result'].get('structuredContent') or json.loads(value['result']['content'][0]['text'])
    assert structured=={'version':'one','value':'hello','leaked':False}
    assert value['version']==one['id']
    store.update(jid,status='succeeded')
    new=store.enqueue(owner['user_id'],None,'chat','new',session_id=a['id'],channel='app')
    assert e.capture(new)['echo']==two['id']
    with pytest.raises(PermissionError):await runtime.for_job({**store.get(jid),'session_id':b['id']},'echo','echo',{'value':'no'})
    e.bind(owner['id'],a['id'],[],1)
    with pytest.raises(PermissionError):await runtime.for_job(store.get(jid),'echo','echo',{'value':'no'})
    assert e.capture(jid)['echo']==one['id']


@pytest.mark.asyncio
async def test_mcp_failed_probe_retains_previous_version(workspace):
    store,owner,_,a,_=workspace;e=store.evolution
    good=stage(store,owner,a,'mcp','echo',{'server.py':server('good')})
    await e.publish(owner['id'],a['id'],good['id'])
    bad=stage(store,owner,a,'mcp','echo',{'server.py':'raise RuntimeError("do not publish")'})
    from local_ai.tool_errors import ToolFailure
    with pytest.raises(ToolFailure) as failure:await e.publish(owner['id'],a['id'],bad['id'])
    assert failure.value.uncertain
    assert e.current('mcp','echo')==good['id']
    assert e.get(owner['id'],a['id'],bad['id'])['state']=='failed'


@pytest.mark.asyncio
async def test_model_publish_requires_specific_approval_and_permissions_rechecked(workspace):
    from local_ai.capabilities import CapabilityRegistry
    store,owner,_,a,_=workspace;e=store.evolution
    jid=store.enqueue(owner['user_id'],None,'chat','create skill',session_id=a['id'],channel='app')
    r=stage(store,owner,a,'skill','writer',{'SKILL.md':'Writer'})
    registry=CapabilityRegistry(admin=True);e.register(registry,store.get(jid))
    with pytest.raises(PermissionError):await registry.execute('evolution_publish',{'revision':r['id']})
    assert not store.skill_catalog()
    seen=[]
    async def approve(job,description):seen.append(description);return True
    registry=CapabilityRegistry(admin=True);e.register(registry,store.get(jid),approve)
    await registry.execute('evolution_publish',{'revision':r['id']})
    assert r['id'] in seen[0]
    await registry.execute('evolution_publish',{'revision':r['id']})
    assert len(seen)==1


def make_updater(workspace,tmp_path):
    store,owner,_,a,_=workspace
    root=tmp_path/'project';(root/'local_ai').mkdir(parents=True)
    (root/'local_ai/calculator.py').write_text('VALUE = "old"\n')
    store.evolution.project=root
    item=stage(store,owner,a,'core','friday',{'local_ai/calculator.py':'VALUE = "new"\n'})
    updater=Updater(root,store.path,tmp_path/'updates')
    return updater,dict(store.db.execute('SELECT * FROM evolution_revisions WHERE id=?',(item['id'],)).fetchone())


def test_updater_tests_fail_preserves_running_code_and_database(workspace,tmp_path,monkeypatch):
    updater,item=make_updater(workspace,tmp_path)
    calls=[]
    monkeypatch.setattr(updater,'prepare',lambda *a:(_ for _ in ()).throw(RuntimeError('tests failed')))
    monkeypatch.setattr(updater,'service',lambda start:calls.append(start))
    updater.apply(item)
    assert not calls
    assert (updater.project/'local_ai/calculator.py').read_text()=='VALUE = "old"\n'
    assert updater.db.execute('SELECT state FROM evolution_revisions WHERE id=?',(item['id'],)).fetchone()[0]=='failed'
    updater.db.close()


@pytest.mark.parametrize('healthy',[True,False])
def test_updater_switch_and_failed_health_restores_previous_code(workspace,tmp_path,monkeypatch,healthy):
    updater,item=make_updater(workspace,tmp_path)
    def prepare(*_):
        (updater.state/item['id']).mkdir();return 'new-build'
    monkeypatch.setattr(updater,'prepare',prepare)
    monkeypatch.setattr(updater,'idle',lambda *a:None)
    calls=[];monkeypatch.setattr(updater,'service',lambda start:calls.append(start))
    monkeypatch.setattr(updater,'await_health',lambda expected:healthy or expected!='new-build')
    updater.apply(item)
    status=updater.db.execute('SELECT state FROM evolution_revisions WHERE id=?',(item['id'],)).fetchone()[0]
    assert status==('succeeded' if healthy else 'rolled_back')
    assert ('new' if healthy else 'old') in (updater.project/'local_ai/calculator.py').read_text()
    assert (updater.state/item['id']/'database-before.sqlite3').is_file()
    assert updater.db.execute('SELECT count(*) FROM accounts').fetchone()[0]==2
    assert updater.db.execute('SELECT drain_until FROM evolution_control').fetchone()[0]==0
    updater.db.close()


def test_recover_interrupted_partial_switch_and_expiring_admission_pause(workspace,tmp_path,monkeypatch):
    updater,item=make_updater(workspace,tmp_path)
    release=updater.state/item['id'];release.mkdir()
    updater.backup(item,json.loads(item['files']))
    (updater.project/'local_ai/calculator.py').write_text('broken new version')
    updater.state_of(item,'applying')
    monkeypatch.setattr(updater,'service',lambda start:None)
    monkeypatch.setattr(updater,'await_health',lambda expected:True)
    updater.recover()
    assert (updater.project/'local_ai/calculator.py').read_text()=='VALUE = "old"\n'
    assert updater.db.execute('SELECT state FROM evolution_revisions WHERE id=?',(item['id'],)).fetchone()[0]=='rolled_back'
    updater.db.close()


def test_exact_core_patch_does_not_mutate_source_and_rejects_ambiguous_match(workspace,tmp_path):
    updater,item=make_updater(workspace,tmp_path)
    store,owner,_,a,_=workspace;e=store.evolution
    draft=e.patch_core(owner['id'],a['id'],[{'path':'local_ai/calculator.py','old':'"old"','new':'"patched"'}],'小范围修复',e.core_base())
    assert 'patched' in draft['files']['local_ai/calculator.py']
    assert 'old' in (updater.project/'local_ai/calculator.py').read_text()
    with pytest.raises(ValueError):e.patch_core(owner['id'],a['id'],[{'path':'local_ai/calculator.py','old':'missing','new':'x'}],'bad',e.core_base())
    updater.db.close()


def test_upgrade_drain_expiration_and_legacy_tasks_do_not_gain_mcp(workspace):
    import time
    store,owner,_,a,_=workspace
    jid=store.enqueue(owner['user_id'],None,'chat','waiting',session_id=a['id'],channel='app')
    with store.db:store.db.execute('UPDATE evolution_control SET drain_until=?',(time.time()+60,))
    assert store.next() is None
    with store.db:store.db.execute('UPDATE evolution_control SET drain_until=?',(time.time()-1,))
    assert store.next()['id']==jid
    assert store.evolution.capture(jid)=={}


def test_skill_interrupted_publication_reconciles_actual_file(workspace):
    store,owner,_,a,_=workspace;e=store.evolution
    item=stage(store,owner,a,'skill','writer',{'SKILL.md':'Writer'})
    with store.db:store.db.execute("UPDATE evolution_revisions SET state='applying' WHERE id=?",(item['id'],))
    target=store.skills_root/'writer/SKILL.md';target.parent.mkdir(parents=True);target.write_text('Writer')
    e.recover()
    assert e.get(owner['id'],a['id'],item['id'])['state']=='published'


def test_upgrade_notifications_only_report_terminal_states(workspace):
    from local_ai.notifications import APNsNotifier
    event={'account_id':'owner','session_id':'a','sequence':1,'kind':'upgrade_updated','payload':json.dumps({'revision':'abc','state':'testing'})}
    assert APNsNotifier._alert(event) is None
    for state in ('succeeded','failed','rolled_back','rollback_failed'):
        event['payload']=json.dumps({'revision':'abc','state':state})
        result=APNsNotifier._alert(event)
        assert result['session_id']=='a'
        assert result['job_id']=='upgrade:abc'
        assert 'files' not in result and 'summary' not in result
