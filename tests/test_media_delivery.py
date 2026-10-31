"""Media delivery and authenticated previews, without external sends or GPUs."""
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
import wave

import pytest
from PIL import Image
from telegram.error import NetworkError

from local_ai.bot import LocalBot
from local_ai.media import media_type, send_telegram_media
from test_browser_api import browser_api, register


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (24, 16), '#224466').save(buffer, format='PNG')
    return buffer.getvalue()


def mp4(handler=b'vide'):
    # Structurally valid ISO-BMFF metadata plus inert bytes for HTTP Range tests.
    def box(kind, payload):
        return (len(payload) + 8).to_bytes(4, 'big') + kind + payload
    return (box(b'ftyp', b'isom\0\0\0\0isommp42') +
            box(b'moov', box(b'trak', box(b'mdia', box(b'hdlr', b'\0'*8 + handler + b'\0'*12)))) +
            box(b'mdat', bytes(range(256))))


@pytest.mark.parametrize('fmt,mime', [('PNG','image/png'),('JPEG','image/jpeg'),('WEBP','image/webp'),('GIF','image/gif')])
def test_preview_uses_verified_content_not_extension(tmp_path, fmt, mime):
    path=tmp_path/'renamed.html'
    Image.new('RGB',(24,16)).save(path,format=fmt)
    assert media_type(path)==mime
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    assert media_type(path) is None  # A replaced file cannot inherit cached media type.


def test_passive_audio_video_and_malformed_files(tmp_path):
    path=tmp_path/'video.mp4'
    path.write_bytes(mp4())
    assert media_type(path)=='video/mp4'
    path.write_bytes(mp4(b'soun'))
    assert media_type(path)=='audio/mp4'
    path.write_bytes(mp4()[:-1])
    assert media_type(path) is None
    path.write_bytes(b'\0\0\0\x20ftypisom<script>alert(1)</script>')
    assert media_type(path) is None
    path.write_bytes(b'RIFF\xff\xff\xff\xffWAVE<html>')
    assert media_type(path) is None
    with wave.open(str(path),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000)
        audio.writeframes(b'\0'*800)
    assert media_type(path)=='audio/wav'
    path.unlink()
    path.symlink_to(tmp_path/'target')
    assert media_type(path) is None


def channel():
    return SimpleNamespace(**{name:AsyncMock(return_value=SimpleNamespace(message_id=index))
        for index,name in enumerate(('send_photo','send_video','send_document'),1)})


async def test_photo_and_streaming_video_preserve_original_and_real_receipt(tmp_path):
    api=channel();path=tmp_path/'photo.png';path.write_bytes(png())
    original=path.read_bytes()
    result=await send_telegram_media(api,123,'image',path)
    assert result.message_id==1 and path.read_bytes()==original
    api.send_photo.assert_awaited_once();api.send_document.assert_not_awaited()
    path=tmp_path/'movie.mp4';path.write_bytes(mp4())
    result=await send_telegram_media(api,123,'video',path)
    assert result.message_id==2 and api.send_video.call_args.kwargs['supports_streaming'] is True


@pytest.mark.parametrize('reason',['size','dimensions','aspect'])
async def test_telegram_photo_limit_falls_back_explicitly(tmp_path,monkeypatch,reason):
    api=channel();path=tmp_path/'photo.png'
    dimensions={'size':(24,16),'dimensions':(5100,5000),'aspect':(100,2)}[reason]
    Image.new('RGB',dimensions).save(path)
    if reason=='size':
        monkeypatch.setattr('local_ai.media.PHOTO_MAX_BYTES',1)
    original=path.read_bytes()
    result=await send_telegram_media(api,123,'image',path)
    assert result.message_id==3 and path.read_bytes()==original
    api.send_photo.assert_not_awaited()
    assert '超出' in api.send_document.call_args.kwargs['caption']


async def test_photo_network_failure_never_retries_as_document(tmp_path):
    api=channel();api.send_photo.side_effect=NetworkError('response lost')
    path=tmp_path/'photo.png';path.write_bytes(png())
    with pytest.raises(NetworkError):
        await send_telegram_media(api,123,'image',path)
    api.send_document.assert_not_awaited()


async def test_explicit_resend_uses_same_inline_photo_sender(tmp_path):
    bot=LocalBot.__new__(LocalBot);bot.channel=channel()
    path=tmp_path/'image.png';path.write_bytes(png())
    result=await bot.send_artifact(123,'image',path)
    assert result.message_id==1
    bot.channel.send_photo.assert_awaited_once()
    bot.channel.send_document.assert_not_awaited()


@pytest.mark.parametrize('kind',['file','artifact'])
async def test_preview_ranges_ownership_and_revoked_cookie(browser_api,kind):
    api=browser_api;login=await register(api);foreign=await register(api,api.friend)
    first=api.store.create_session(api.owner['id'],'A')
    second=api.store.create_session(api.owner['id'],'B')
    prefix=f"/v1/sessions/{first['id']}"
    if kind=='file':
        path=api.store.workspace_file(api.owner['id'],first['id'],'movie.mp4');path.write_bytes(mp4())
        url=prefix+'/files/movie.mp4'
        data=await (await api.client.get(prefix+'/files',headers=login.headers)).json()
        assert data['items'][0]['preview_url']==url+'?preview=1'
        assert data['items'][0]['media_type']=='video/mp4'
    else:
        parent=api.store.enqueue(api.owner['user_id'],None,'chat','Make a video',session_id=first['id'],channel='app')
        child=api.store.enqueue(api.owner['user_id'],None,'video','internal production prompt',parent_job_id=parent)
        path=api.store.path.parent/'movie.mp4';path.write_bytes(mp4())
        api.store.update(child,status='succeeded',result=str(path))
        url=prefix+f'/tasks/{child}/artifact'
        data=await (await api.client.get(prefix+'/messages',headers=login.headers)).json()
        media=next(row for row in data['items'] if row['id']==child)
        assert media['parent_job_id']==parent and media['artifact_media_type']=='video/mp4'
        assert media['artifact_preview_url']==url+'?preview=1' and media['result'] is None
    download=await api.client.get(url,headers=login.headers)
    assert download.content_type=='application/octet-stream' and download.headers['Content-Disposition']=='attachment'
    response=await api.client.get(url+'?preview=1',headers={**login.headers,'Range':'bytes=10-29'})
    assert response.status==206 and await response.read()==path.read_bytes()[10:30]
    assert response.content_type=='video/mp4' and response.headers['Content-Disposition']=='inline'
    assert response.headers['Content-Range']==f'bytes 10-29/{path.stat().st_size}'
    assert response.headers['Cache-Control']=='no-store' and response.headers['X-Content-Type-Options']=='nosniff'
    assert "sandbox" in response.headers['Content-Security-Policy']
    assert response.headers['Cross-Origin-Resource-Policy']=='same-origin'
    assert (await api.client.get(url+'?preview=1')).status==401
    assert (await api.client.get(url+'?preview=1',headers=foreign.headers)).status==403
    wrong=url.replace(first['id'],second['id'])+'?preview=1'
    assert (await api.client.get(wrong,headers=login.headers)).status in (403,404)
    api.auth.revoke_token(login.token)
    assert (await api.client.get(url+'?preview=1',headers=login.headers)).status==401


@pytest.mark.parametrize('name,content',[('fake.png',b'<html><script>alert(1)</script></html>'),('active.svg',b'<svg onload="alert(1)"/>'),('script.js',b'alert(1)')])
async def test_active_content_never_gets_inline_preview(browser_api,name,content):
    api=browser_api;login=await register(api)
    current=api.store.create_session(api.owner['id'],'A')
    path=api.store.workspace_file(api.owner['id'],current['id'],name);path.write_bytes(content)
    url=f"/v1/sessions/{current['id']}/files/{name}"
    response=await api.client.get(url+'?preview=1',headers=login.headers)
    assert response.status==415
    assert response.headers['Cache-Control']=='no-store' and response.headers['X-Content-Type-Options']=='nosniff'
    response=await api.client.get(url,headers=login.headers)
    assert response.content_type=='application/octet-stream' and response.headers['Content-Disposition']=='attachment'


async def test_verified_image_served_with_exact_mime_and_private_metadata(browser_api):
    api=browser_api;login=await register(api)
    current=api.store.create_session(api.owner['id'],'A')
    path=api.store.workspace_file(api.owner['id'],current['id'],'图 一.png');path.write_bytes(png())
    prefix=f"/v1/sessions/{current['id']}"
    data=await (await api.client.get(prefix+'/files',headers=login.headers)).json()
    item=data['items'][0]
    assert item['preview_url'].endswith('/%E5%9B%BE%20%E4%B8%80.png?preview=1')
    response=await api.client.get(item['preview_url'],headers=login.headers)
    assert response.content_type=='image/png' and await response.read()==png()
    assert (await api.client.get('/pwa/media.js')).status==401


def test_thumbnail_composites_preview_preserves_original_and_invalidates_replaced_file(tmp_path):
    from local_ai.media import image_thumbnail
    path = tmp_path / 'transparent.png'
    Image.new('RGBA', (1024, 512), (255, 0, 0, 64)).save(path)
    original = path.read_bytes()
    data, mime = image_thumbnail(path)
    assert mime == 'image/jpeg'
    with Image.open(io.BytesIO(data)) as preview:
        assert preview.size == (768, 384)
        assert abs(preview.getpixel((0, 0))[0] - 64) <= 2
    assert path.read_bytes() == original
    Image.new('RGBA', (1024, 512), (0, 255, 0, 255)).save(path)
    changed, mime = image_thumbnail(path)
    assert mime == 'image/jpeg' and changed != data
    path.write_text('not an image')
    with pytest.raises(ValueError):
        image_thumbnail(path)
