"""Verified passive media previews and channel-specific media delivery.

Filename extensions and uploaded Content-Type values are never preview authority.
Original files remain available as downloads; previewing never rewrites them.
"""
from functools import lru_cache
from pathlib import Path
import stat
import warnings
import wave

from PIL import Image


IMAGE_TYPES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}
PHOTO_MAX_BYTES = 10 * 1024 * 1024


def _boxes(stream, start, end, budget):
    """Walk bounded ISO-BMFF boxes without loading video payloads into memory."""
    position = start
    while position < end:
        budget[0] -= 1
        if budget[0] < 0 or end - position < 8:
            raise ValueError('Invalid media container')
        stream.seek(position)
        header = stream.read(8)
        size, kind = int.from_bytes(header[:4], 'big'), header[4:8]
        head_size = 8
        if size == 1:
            size = int.from_bytes(stream.read(8), 'big')
            head_size = 16
        elif size == 0:
            size = end - position
        if size < head_size or position + size > end:
            raise ValueError('Invalid media container')
        yield kind, position + head_size, position + size
        position += size


def _mp4_type(stream, size):
    budget = [2048]
    tracks, has_data, has_type = set(), False, False
    for kind, start, end in _boxes(stream, 0, size, budget):
        if kind == b'ftyp':
            if start != 8 or not 8 <= end - start <= 256:
                return None
            stream.seek(start)
            brands = stream.read(end - start)
            accepted = {b'isom', b'iso2', b'iso5', b'iso6', b'mp41', b'mp42', b'avc1', b'M4A ', b'M4V '}
            has_type = bool(({brands[:4]} | {brands[i:i+4] for i in range(8, len(brands), 4)}) & accepted)
        elif kind == b'mdat':
            has_data = end > start
        elif kind == b'moov':
            for name, a, b in _boxes(stream, start, end, budget):
                if name != b'trak':
                    continue
                for name, c, d in _boxes(stream, a, b, budget):
                    if name != b'mdia':
                        continue
                    for name, e, f in _boxes(stream, c, d, budget):
                        if name == b'hdlr' and f - e >= 12:
                            stream.seek(e + 8)
                            tracks.add(stream.read(4))
    if has_type and has_data:
        if b'vide' in tracks:
            return 'video/mp4'
        if b'soun' in tracks:
            return 'audio/mp4'
    return None


@lru_cache(maxsize=2048)
def _inspect(path, identity):
    # identity includes inode, size and nanosecond change times, so replacing or
    # modifying an upload invalidates the bounded metadata cache.
    try:
        with open(path, 'rb') as stream:
            prefix = stream.read(64)
            stream.seek(0)
            if prefix.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a')) or (prefix[:4] == b'RIFF' and prefix[8:12] == b'WEBP'):
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(stream) as image:
                        result = IMAGE_TYPES.get(image.format)
                        image.verify()
                        return result
            if prefix[4:8] == b'ftyp':
                return _mp4_type(stream, identity[1])
            if prefix[:4] == b'RIFF' and prefix[8:12] == b'WAVE':
                with wave.open(stream, 'rb') as audio:
                    if audio.getnframes() and 1 <= audio.getnchannels() <= 8 and audio.getframerate() > 0:
                        return 'audio/wav'
            if prefix[:6] == b'OggS\x00\x02' and len(prefix) >= 27:
                # The BOS page must introduce an audio codec, not arbitrary
                # Ogg content (e.g. a video track or a renamed HTML upload).
                stream.seek(27)
                lacing = stream.read(prefix[26])
                payload = stream.read(sum(lacing))
                if len(payload) == sum(lacing) and (payload.startswith(b'OpusHead') or payload.startswith(b'\x01vorbis')):
                    return 'audio/ogg'
    except (OSError, ValueError, SyntaxError, EOFError, wave.Error, Image.DecompressionBombWarning, Image.DecompressionBombError):
        pass
    return None


def media_type(path):
    """Return an allowlisted content type only for a verified regular file."""
    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not info.st_size:
            return None
        return _inspect(str(path), (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    except OSError:
        return None


async def send_telegram_media(channel, chat_id, kind, path):
    """Send viewable media by default, with a deterministic photo-limit fallback.

    Network failures propagate to durable delivery handling. Never retry a
    possibly accepted photo as a document and risk a duplicate message.
    """
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise FileNotFoundError('Generated artifact missing')
    caption = None
    if kind == 'image':
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
        if path.stat().st_size > PHOTO_MAX_BYTES or width + height > 10000 or max(width, height) > 20 * min(width, height):
            caption = '图片超出 Telegram 照片尺寸或大小限制，已保留原图并以文件发送。'
    with path.open('rb') as media:
        if kind == 'video':
            return await channel.send_video(chat_id, media, supports_streaming=True, read_timeout=180, write_timeout=180)
        if kind == 'image' and caption is None:
            return await channel.send_photo(chat_id, media, read_timeout=180, write_timeout=180)
        kwargs = {'caption': caption} if caption else {}
        return await channel.send_document(chat_id, media, **kwargs, read_timeout=180, write_timeout=180)


@lru_cache(maxsize=64)
def _thumbnail(path, identity):
    """Bounded in-memory preview cache; never modifies the original artifact."""
    import io
    from PIL import ImageOps
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as source:
            source.seek(0)
            preview = ImageOps.exif_transpose(source)
            preview.thumbnail((768, 768), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            if 'A' in preview.getbands() or 'transparency' in preview.info:
                # The native chat surface is black. Composite only the disposable
                # preview; keep the original alpha channel intact for Quick Look.
                rgba = preview.convert('RGBA')
                preview = Image.new('RGB', rgba.size, (0, 0, 0))
                preview.paste(rgba, mask=rgba.getchannel('A'))
            preview.convert('RGB').save(output, format='JPEG', quality=82, optimize=True)
            mime = 'image/jpeg'
    return output.getvalue(), mime


def image_thumbnail(path):
    path = Path(path)
    if not (media_type(path) or '').startswith('image/'):
        raise ValueError('此文件不支持图片预览。')
    info = path.stat()
    return _thumbnail(str(path), (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
