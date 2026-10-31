import asyncio
import json
from pathlib import Path

import pytest

from local_ai.engine import Engine, build_workflow
from local_ai.store import Store


def test_access_revocation_overrides_config_and_keeps_admin(tmp_path):
    s = Store(tmp_path / "test.db")
    assert not s.allowed(3, {2}, 1)
    assert s.allowed(2, {2}, 1)
    s.set_access(2, False)
    assert not s.allowed(2, {2}, 1)
    s.set_access(3, True)
    assert s.allowed(3, {2}, 1)
    s.set_access(1, False)
    assert s.allowed(1, set(), 1)


def test_queue_history_and_restart_isolation(tmp_path):
    path = tmp_path / "test.db"
    s = Store(path)
    first = s.enqueue(11, 11, "chat", "secret question")
    second = s.enqueue(22, 22, "image", "a cat")
    assert s.next()["id"] == first
    s.remember(11, "secret question", "private answer")
    assert "private answer" in str(s.messages(11, "next"))
    assert "private answer" not in str(s.messages(22, "next"))
    s.reset(22)
    assert "private answer" in str(s.messages(11, "next"))
    s.update(first, status="running")
    s.db.close()
    restarted = Store(path)
    # Readers must not mutate a live owner's jobs merely by opening the DB.
    assert restarted.get(first)["status"] == "running"
    assert restarted.recover_interrupted() == [first]
    assert restarted.get(first)['restart_count'] == 1
    assert restarted.next()["id"] == first
    assert len(restarted.pending_delivery()) == 0


def test_queue_cap_and_update_deduplication(tmp_path):
    s = Store(tmp_path / "test.db")
    assert not s.seen(123)
    assert s.seen(123)
    for _ in range(3):
        s.enqueue(1, 1, "chat", "hello")
    with pytest.raises(ValueError):
        s.enqueue(1, 1, "chat", "hello")
    s.enqueue(2, 2, "chat", "another user")


def test_video_first_frame_and_hd_do_not_mutate_default():
    a = build_workflow("video", "a cat", "test1", image="test.png", hd=True, seed=12)
    assert a["text"]["inputs"]["first_frame"] == ["input_image", 0]
    assert a["text"]["inputs"]["width"] == 480
    b = build_workflow("video", "a dog", "test2", seed=13)
    assert "first_frame" not in b["text"]["inputs"]
    assert b["text"]["inputs"]["width"] == 352
    assert b["noise"]["inputs"]["noise_seed"] == 13


@pytest.mark.asyncio
async def test_stop_releases_owned_process(tmp_path):
    engine = Engine()
    engine.process = await asyncio.create_subprocess_exec("/bin/sleep", "60", start_new_session=True)
    process = engine.process
    engine.kind = "video"
    await engine.stop()
    assert process.returncode is not None
    assert engine.process is None
    assert engine.kind is None
