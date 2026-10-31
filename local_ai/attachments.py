"""Owned input attachments: preserve content before choosing an operation."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
import zipfile
from xml.etree import ElementTree

from PIL import Image

from .engine import ROOT, RUNTIME
from .tool_errors import ToolFailure

MAX_FILE_BYTES = 20 * 1024 * 1024
_build_lock = asyncio.Lock()


def classify(path, filename):
    path = Path(path)
    with path.open('rb') as stream:
        magic = stream.read(16)
    if magic.startswith(b'%PDF-'):
        return 'pdf', '.pdf'
    try:
        with Image.open(path) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError('图片超过2500万像素，请缩小后发送。')
            image.verify()
            return 'image', '.' + {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif', 'TIFF': 'tiff'}.get(image.format, 'img')
    except (Image.UnidentifiedImageError, OSError):
        pass
    extension = Path(filename or '').suffix.lower()
    if magic.startswith(b'PK') and extension in ('.docx', '.xlsx'):
        return extension[1:], extension
    if extension in ('.txt', '.md', '.csv', '.tsv', '.json', '.log', '.yaml', '.yml'):
        return 'text', extension
    return 'binary', extension[:12] or '.bin'


def office_text(path, kind):
    """Bound zip expansion and return document text; never run macros or formulas."""
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 2000 or sum(i.file_size for i in members) > 40_000_000:
            raise ToolFailure('document_too_large', '文档解压体积过大。')
        def xml(name):
            return ElementTree.fromstring(archive.read(name))
        if kind == 'docx':
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            return '\n'.join(''.join(p.itertext()) for p in xml('word/document.xml').findall('.//w:p', ns))
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(n.itertext()) for n in xml('xl/sharedStrings.xml').findall('s:si', ns)]
        sections = []
        for sheet in sorted(n for n in archive.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')):
            lines = [sheet]
            for row in xml(sheet).findall('.//s:row', ns):
                cells = []
                for cell in row.findall('s:c', ns):
                    value = cell.findtext('s:v', default='', namespaces=ns)
                    if cell.get('t') == 's' and value.isdigit() and int(value) < len(strings):
                        value = strings[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        value = ''.join(cell.find('s:is', ns).itertext())
                    formula = cell.findtext('s:f', namespaces=ns)
                    cells.append(f"{cell.get('r', '?')}={value}" + (f' [formula={formula}; cached value, not recalculated]' if formula else ''))
                lines.append('\t'.join(cells))
            sections.append('\n'.join(lines))
        return '\n\n'.join(sections)


async def native_extract(path, runtime):
    if sys.platform != 'darwin' or not shutil.which('swiftc'):
        raise ToolFailure('parser_unavailable', '本地图片/PDF提取需要 macOS 的 Swift、Vision 和 PDFKit。')
    source = ROOT/'scripts/attachment_reader.swift'
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    binary = runtime/'bin'/f'attachment-reader-{fingerprint}'
    async with _build_lock:
        if not binary.exists():
            binary.parent.mkdir(parents=True, exist_ok=True)
            temporary = binary.with_suffix('.building')
            proc = await asyncio.create_subprocess_exec(shutil.which('swiftc'), str(source), '-o', str(temporary),
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                _, errors = await asyncio.wait_for(proc.communicate(), 90)
                if proc.returncode:
                    raise ToolFailure('parser_build_failed', '本地附件解析器编译失败。')
                temporary.replace(binary)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                temporary.unlink(missing_ok=True)
    proc = await asyncio.create_subprocess_exec(str(binary), str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 90)
        if proc.returncode:
            raise ToolFailure('attachment_unreadable', '附件无法读取，可能损坏或加密。')
        return json.loads(stdout)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class Attachments:
    def __init__(self, store, job, runtime=RUNTIME):
        self.store, self.job, self.runtime = store, job, Path(runtime)

    @staticmethod
    def init_schema(db):
        db.execute('''CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, user_id INTEGER NOT NULL, chat_id INTEGER,
            name TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
            digest TEXT NOT NULL, created REAL NOT NULL, extraction TEXT)''')
        db.commit()

    def add(self, source, filename):
        source = Path(source)
        size = source.stat().st_size
        if not 0 < size <= MAX_FILE_BYTES:
            raise ValueError('附件须为1字节到20MB。')
        kind, suffix = classify(source, filename)
        aid = uuid.uuid4().hex
        destination = self.runtime/'attachments'/self.job['session_id']/(aid+suffix)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        self.store.db.execute('INSERT INTO attachments(id,job_id,user_id,chat_id,name,kind,path,size,digest,created,extraction,session_id) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)',
            (aid, self.job['id'], self.job['user_id'], self.job['chat_id'], Path(filename or 'attachment').name[:250],
             kind, str(destination), size, digest, time.time(),self.job['session_id']))
        self.store.db.commit()
        return aid

    def owned(self, aid):
        row = self.store.db.execute('SELECT * FROM attachments WHERE id=? AND session_id=?',
                                  (aid, self.job['session_id'])).fetchone()
        if not row:
            raise PermissionError('附件不属于当前用户和会话。')
        return dict(row)

    def list(self, *, current=False):
        query = 'SELECT id,name,kind,size,job_id FROM attachments WHERE session_id=?'
        args = [self.job['session_id']]
        if current:
            query += ' AND job_id=?'
            args.append(self.job['id'])
        query += ' ORDER BY created DESC LIMIT 50'
        return [dict(row) for row in self.store.db.execute(query, args)]

    async def read(self, aid, offset=0, limit=12000):
        item = self.owned(aid)
        if type(offset) is not int or offset < 0:
            raise ValueError('offset 必须为非负整数。')
        if item['extraction']:
            extracted = json.loads(item['extraction'])
        else:
            path = Path(item['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['digest']:
                raise ToolFailure('attachment_changed', '附件内容已发生变化，不能使用旧记录。')
            kind = item['kind']
            if kind == 'text':
                extracted = {'text': path.read_text(encoding='utf-8-sig'), 'method': 'text', 'truncated': False}
            elif kind in ('docx', 'xlsx'):
                extracted = {'text': await asyncio.to_thread(office_text, path, kind), 'method': kind, 'truncated': False}
            elif kind in ('image', 'pdf'):
                native = await native_extract(path, self.runtime)
                pages = native.get('pages', [native])
                text = '\n\n'.join(f"[page {p.get('page', 1)}]\n" + (p.get('text') or '\n'.join(line['text'] for line in p.get('lines', []))) for p in pages)
                extracted = {'text': text, 'method': 'pdf_text_or_ocr' if kind == 'pdf' else 'ocr',
                             'page_count': native.get('page_count', 1),
                             'truncated': native.get('truncated', False),
                             'limitations': '仅提取文字，OCR可能有误；不能据此声称识别了所有图片内容、图表或布局。'}
            else:
                raise ToolFailure('unsupported_format', '该附件类型尚无解析器；原文件已保存，不会转换成其他任务。')
            self.store.db.execute('UPDATE attachments SET extraction=? WHERE id=?', (json.dumps(extracted, ensure_ascii=False), aid))
            self.store.db.commit()
        text = extracted['text']
        return {**extracted, 'text': text[offset:offset+limit], 'attachment_id': aid, 'name': item['name'],
                'offset': offset, 'total_characters': len(text), 'next_offset': offset+limit if offset+limit < len(text) else None,
                'truncated': extracted.get('truncated', False) or offset+limit < len(text), 'untrusted_content': True}
