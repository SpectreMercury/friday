"""Install Friday's separate per-user update supervisor; never install generated code here."""
import hashlib
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LABEL='local.friday.updater'


def main():
    home=Path.home()/'.friday/updater';home.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=home/'worker.py';source=(ROOT/'scripts/friday_updater.py').read_bytes()
    # Updating this supervisor is a manual maintenance operation, never a core proposal.
    if target.exists() and target.read_bytes()!=source:
        raise SystemExit('升级器已存在且版本不同；先结束待处理升级，再人工更新独立升级器。')
    target.write_bytes(source);target.chmod(0o600)
    (home/'worker.sha256').write_text(hashlib.sha256(source).hexdigest()+'\n')
    database=ROOT/'runtime/bot.sqlite3'
    state=ROOT/'runtime/upgrades';state.mkdir(parents=True,exist_ok=True,mode=0o700)
    plist=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    config={'Label':LABEL,'ProgramArguments':[str(Path(sys.executable).resolve()),str(target),'--project',str(ROOT),'--database',str(database),'--state',str(state)],
        'WorkingDirectory':str(home),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,
        'StandardOutPath':str(home/'supervisor.log'),'StandardErrorPath':str(home/'supervisor.log'),
        'EnvironmentVariables':{'PATH':'/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
    with plist.open('wb') as out:plistlib.dump(config,out)
    plist.chmod(0o600)
    domain=f'gui/{os.getuid()}'
    existing=subprocess.run(['/bin/launchctl','print',domain+'/'+LABEL],capture_output=True)
    if existing.returncode:
        subprocess.run(['/bin/launchctl','bootstrap',domain,str(plist)],check=True)
    print('Independent supervisor installed:',target)


if __name__=='__main__':main()
