from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from local_ai.routing import output_contract, guard_media
from local_ai.pi_agent import PiAgent, UserTools
from local_ai.store import Store


@pytest.mark.parametrize('prompt,route', [
    ('帮我生成一段中文欢迎语音，不要视频', 'agent'),
    ('请把欢迎词朗读出来', 'agent'),
    ('生成一段带中文旁白的视频', 'agent'),
    ('生成猫咪的视频', 'agent'),
    ('给我介绍一下你自己', 'agent'),
])
def test_explicit_output_contract(prompt, route):
    assert output_contract({'prompt': prompt})['route'] == route


def test_voice_input_does_not_imply_audio_generation():
    contract = output_contract({'prompt': '帮我画一只橘猫', 'audio': 'input.ogg'})
    assert contract['route'] == 'agent'
    assert contract['input_mode'] == 'voice'
    assert contract['reply_audio'] is True


@pytest.mark.asyncio
async def test_standalone_audio_is_explicit_capability_not_video(tmp_path):
    store = Store(tmp_path/'test.sqlite3')
    jid = store.enqueue(1,1,'chat','请生成一段中文欢迎语音')
    path=tmp_path/'welcome.ogg';path.write_bytes(b'audio')
    speech=SimpleNamespace(ready=lambda:True,synthesize=AsyncMock(return_value=path))
    try:
        tools=UserTools(store,store.get(jid),lambda _:True,tmp_path/'workspaces',speech=speech)
        result=await tools.registry().execute('generate_audio',{'text':'欢迎来到这里。'})
        assert result['generated'] and result['delivery']=='pending'
        assert store.get(jid)['voice_result']==str(path)
        assert store.db.execute("SELECT count(*) FROM jobs WHERE kind='video'").fetchone()[0]==0
    finally:store.db.close()


def test_searching_audio_documentation_is_not_audio_generation():
    assert output_contract({'prompt': '搜索中文配音模型的官方文档'})['route'] == 'agent'
