"""Async child completion settles exactly the requirements in a verified watch."""
import json
import pytest
from local_ai.store import Store


@pytest.fixture
def flow(tmp_path):
    store=Store(tmp_path/'db')
    parent=store.enqueue(1,1,'chat','Produce an artifact and report the result.')
    store.update(parent,status='succeeded',outcome='pending')
    child=store.enqueue(1,1,'image','fixture artifact',parent_job_id=parent)
    path=tmp_path/'artifact.bin';path.write_bytes(b'fixture')
    yield store,parent,child,path
    store.db.close()


def watch(store,parent,children,*,accepted=True,only=True):
    proof={'accepted':accepted,'completion_watch':children,'completion_watch_only':only}
    store.db.execute("INSERT INTO pi_tools(job_id,user_id,name,args,result,created) VALUES (?,1,'final_check','{}',?,1)",
                     (parent,json.dumps(proof)))
    store.db.commit()


def finish(store,child,path,*,receipt=True):
    if receipt:store.record_receipt(child,'media',store.get(child)['chat_id'],100)
    store.update(child,status='succeeded',result=str(path),outcome='completed',delivery_state='delivered')


@pytest.mark.parametrize('proof', ['absent','rejected','other_requirements_missing'])
def test_a_finished_child_cannot_complete_unproved_parent_requirements(flow,proof):
    store,parent,child,path=flow
    if proof!='absent':watch(store,parent,[child],accepted=proof!='rejected',only=proof!='other_requirements_missing')
    finish(store,child,path)
    assert store.get(parent)['outcome']=='pending'


def test_unrelated_pending_child_does_not_change_a_verified_watch(flow):
    store,parent,child,path=flow
    unused=store.enqueue(1,1,'image','not a remaining requirement',parent_job_id=parent)
    watch(store,parent,[child])
    finish(store,child,path)
    assert store.get(parent)['outcome']=='completed'
    assert store.get(unused)['status']=='queued'


@pytest.mark.parametrize('missing', ['file','receipt','symlink'])
def test_terminal_status_without_real_artifact_and_delivery_is_insufficient(flow,missing):
    store,parent,child,path=flow
    watch(store,parent,[child])
    if missing=='symlink':
        link=path.with_name('artifact-link.bin');link.symlink_to(path);path=link
    finish(store,child,path.with_name('nonexistent') if missing=='file' else path,receipt=missing!='receipt')
    assert store.get(parent)['outcome']=='pending'


def test_a_followup_can_watch_the_same_owned_async_result(flow):
    store,parent,child,path=flow
    follow=store.enqueue(1,1,'chat','Report when the previous artifact is ready.')
    store.link_task(store.get(follow),parent)
    store.update(follow,status='succeeded',outcome='pending')
    watch(store,follow,[child])
    finish(store,child,path)
    assert store.get(follow)['outcome']=='completed'
    assert store.get(parent)['outcome']=='pending'


def test_app_artifact_does_not_require_a_telegram_receipt(tmp_path):
    store=Store(tmp_path/'db')
    account=store.account_for_uid(1);session=store.create_session(account['id'],'app')
    parent=store.enqueue(1,None,'chat','Generate artifact',session_id=session['id'],channel='app')
    store.update(parent,status='succeeded',outcome='pending')
    child=store.enqueue(1,None,'image','fixture',parent_job_id=parent)
    watch(store,parent,[child])
    path=tmp_path/'artifact.bin';path.write_bytes(b'fixture')
    finish(store,child,path,receipt=False)
    assert store.get(parent)['outcome']=='completed'
    store.db.close()


def test_foreign_watch_cannot_complete_the_parent(flow):
    store,parent,child,path=flow
    foreign_parent=store.enqueue(2,2,'chat','Other artifact')
    store.update(foreign_parent,status='succeeded',outcome='pending')
    foreign_child=store.enqueue(2,2,'image','other',parent_job_id=foreign_parent)
    watch(store,parent,[child,foreign_child])
    finish(store,foreign_child,path)
    finish(store,child,path)
    assert store.get(parent)['outcome']=='pending'
