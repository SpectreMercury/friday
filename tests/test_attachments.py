import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from PIL import Image, ImageDraw, ImageFont
import pytest

from local_ai.attachments import Attachments
from local_ai.store import Store


@pytest.fixture
def inputs(tmp_path):
    store = Store(tmp_path/'db')
    jid = store.enqueue(1, 1, 'chat', '分析附件')
    manager = Attachments(store, store.get(jid), tmp_path)
    yield store, manager
    store.db.close()


@pytest.mark.asyncio
async def test_text_pagination_and_owner_boundary(inputs, tmp_path):
    store, manager = inputs
    path = tmp_path/'source.txt'
    path.write_text('first\n' + 'data'*4000 + '\nlast', encoding='utf-8')
    aid = manager.add(path, '../../user-name.txt')
    first = await manager.read(aid)
    assert first['truncated'] and first['next_offset'] == 12000
    second = await manager.read(aid, first['next_offset'])
    assert first['text'] + second['text'] == path.read_text()
    assert second['next_offset'] is None
    assert manager.list()[0]['name'] == 'user-name.txt'
    other = store.enqueue(2, 2, 'chat', '读取附件')
    with pytest.raises(PermissionError):
        await Attachments(store, store.get(other), tmp_path).read(aid)


@pytest.mark.asyncio
async def test_office_documents_are_data_and_formulas_not_executed(inputs, tmp_path):
    _, manager = inputs
    doc = tmp_path/'document.docx'
    with zipfile.ZipFile(doc, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>项目说明</w:t></w:r></w:p><w:p><w:r><w:t>预算 2400</w:t></w:r></w:p></w:body></w:document>')
    result = await manager.read(manager.add(doc, '说明.docx'))
    assert result['text'] == '项目说明\n预算 2400' and result['untrusted_content']
    sheet = tmp_path/'book.xlsx'
    with zipfile.ZipFile(sheet, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1"><v>12</v></c><c r="B1"><f>A1*2</f><v>24</v></c></row></sheetData></worksheet>')
    result = await manager.read(manager.add(sheet, '数据.xlsx'))
    assert 'A1=12' in result['text'] and 'B1=24' in result['text']
    assert 'cached value, not recalculated' in result['text']


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='native macOS parser')
async def test_real_local_image_ocr(inputs, tmp_path):
    _, manager = inputs
    image = Image.new('RGB', (1000, 200), 'white')
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 64)
    ImageDraw.Draw(image).text((40, 50), 'Receipt total 128.50', fill='black', font=font)
    source = tmp_path/'receipt.png'
    image.save(source)
    result = await manager.read(manager.add(source, 'receipt.png'))
    assert '128.50' in result['text'] and result['method'] == 'ocr'
    assert result['limitations'] and result['untrusted_content']


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='native macOS parser')
async def test_real_pdf_text_extraction(inputs, tmp_path):
    _, manager = inputs
    content = b'BT /F1 18 Tf 40 700 Td (Invoice total 219.75) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    data, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    source = tmp_path/'invoice.pdf'
    source.write_bytes(data)
    result = await manager.read(manager.add(source, 'invoice.pdf'))
    assert '219.75' in result['text'] and result['page_count'] == 1 and not result['truncated']


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,caption,expected', [
    ('question.png', '图中写了什么', 'chat'),
    ('notes.txt', '总结文档', 'chat'),
    ('question.png', '', 'chat'),
    ('question.png', '/video 让画面动起来', 'video'),
])
async def test_channel_routes_only_explicit_video_commands(tmp_path, monkeypatch, filename, caption, expected):
    import local_ai.bot as module
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_ADMIN_USER_ID', '1')
    monkeypatch.setattr(module, 'RUNTIME', tmp_path)
    monkeypatch.setattr(module, 'COMFY', tmp_path/'comfy')
    monkeypatch.setattr(module, 'model_status', lambda _: [])
    bot = module.LocalBot()
    source = tmp_path/filename
    if filename.endswith('.png'):
        Image.new('RGB', (20, 20)).save(source)
    else:
        source.write_text('document content')
    async def download(target):
        shutil.copyfile(source, target)
    media = SimpleNamespace(file_size=source.stat().st_size, file_name=filename,
                            get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=download)))
    message = SimpleNamespace(photo=None, document=media, caption=caption, text=None,
                              reply_text=AsyncMock(), set_reaction=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=1, type='private'),
                             effective_message=message, update_id=101,
                             get_bot=lambda: SimpleNamespace(send_chat_action=AsyncMock()))
    try:
        await bot.message(update, None)
        job = bot.store.next()
        assert job['kind'] == expected
        if expected == 'chat':
            assert job['image'] is None
            attachments = Attachments(bot.store, job, tmp_path).list(current=True)
            assert len(attachments) == 1 and attachments[0]['name'] == filename
        else:
            assert (tmp_path/'comfy/input'/job['image']).is_file()
            assert not Attachments(bot.store, job, tmp_path).list(current=True)
    finally:
        bot.store.db.close()
