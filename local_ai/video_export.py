"""Bounded ffmpeg exports; source clips stay intact until atomic publication."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

FFMPEG='/opt/homebrew/bin/ffmpeg'
FFPROBE='/opt/homebrew/bin/ffprobe'


async def command(arguments,timeout=900):
    proc=await asyncio.create_subprocess_exec(*arguments,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        output,_=await asyncio.wait_for(proc.communicate(),timeout)
    except BaseException:
        if proc.returncode is None:proc.kill()
        await proc.wait()
        raise
    if proc.returncode:raise RuntimeError('视频封装失败；已生成的原片仍保留。')
    return output


async def duration(path):
    data=json.loads(await command([FFPROBE,'-v','error','-show_entries','format=duration','-of','json',str(path)],30))
    value=float(data['format']['duration'])
    if value<=0:raise RuntimeError('视频时长校验失败。')
    return value


async def trim_clip(source,seconds,target):
    source,target=Path(source),Path(target)
    if not source.is_file() or source.is_symlink():raise RuntimeError('原片不存在，不能导出。')
    if source.resolve()==target.resolve():raise RuntimeError('导出路径不能覆盖原片。')
    # Duration alone does not identify a clip. A retry can replace a source with
    # a different render of exactly the same length; never reuse its old trim.
    fingerprint={'version':1,'source_sha256':file_digest(source),'seconds':seconds,
                 'profile':'h264-fast-crf18-yuv420p-aac'}
    receipt=target.with_suffix(target.suffix+'.source.json')
    if target.is_file() and not target.is_symlink() and receipt.is_file():
        try:
            cached=json.loads(receipt.read_text())
            if cached=={**fingerprint,'output_sha256':file_digest(target)} and abs(await duration(target)-seconds)<.12:
                return target
        except (OSError,ValueError,RuntimeError):
            pass
    tmp=target.with_name(target.stem+'.partial.mp4')
    receipt_tmp=receipt.with_suffix(receipt.suffix+'.partial')
    try:
        await command([FFMPEG,'-v','error','-nostdin','-y','-i',str(source),'-t',str(seconds),
            '-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
            '-c:a','aac','-movflags','+faststart',str(tmp)])
        if abs(await duration(tmp)-seconds)>.12:raise RuntimeError('视频导出时长与分镜不符。')
        receipt_tmp.write_text(json.dumps({**fingerprint,'output_sha256':file_digest(tmp)},sort_keys=True))
        os.replace(tmp,target)
        os.replace(receipt_tmp,receipt)
        return target
    finally:
        tmp.unlink(missing_ok=True)
        receipt_tmp.unlink(missing_ok=True)


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


async def assemble_project(rows,directory):
    directory=Path(directory)
    paths=[]
    for row in rows:
        target=directory/f"shot-{row['ordinal']+1:04d}.mp4"
        paths.append(await trim_clip(Path(row['result']),row['duration'],target))
    manifest=directory/'concat.txt'
    manifest.write_text(''.join(f"file '{p.name}'\n" for p in paths))
    tmp=directory/'film.partial.mp4';target=directory/'film.mp4'
    try:
        await command([FFMPEG,'-v','error','-nostdin','-y','-f','concat','-safe','1','-i',str(manifest),
            '-c','copy','-movflags','+faststart',str(tmp)])
        expected=sum(row['duration'] for row in rows)
        if abs(await duration(tmp)-expected)>max(.25,len(rows)*.06):raise RuntimeError('合并时长不符，保留分段供检查。')
        os.replace(tmp,target)
        return target
    finally:tmp.unlink(missing_ok=True)
