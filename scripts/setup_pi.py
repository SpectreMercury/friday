"""Install locked Pi dependencies using explicit Node and pnpm executables."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--node',required=True,type=Path)
    parser.add_argument('--pnpm',required=True,type=Path)
    args=parser.parse_args()
    node=args.node.resolve(strict=True)
    pnpm=args.pnpm.resolve(strict=True)
    version=subprocess.check_output([str(node),'--version'],text=True).strip()
    parts=tuple(int(p) for p in version.lstrip('v').split('.')[:3])
    if parts<(22,19,0):raise SystemExit('Pi requires Node >=22.19.0')
    env=os.environ.copy();env['PATH']=str(node.parent)+os.pathsep+env.get('PATH','')
    subprocess.run([str(pnpm),'install','--frozen-lockfile','--ignore-scripts'],cwd=ROOT/'pi',env=env,check=True)
    link=ROOT/'vendor/node';link.parent.mkdir(exist_ok=True)
    if link.is_symlink():link.unlink()
    if link.exists():raise SystemExit('vendor/node exists and is not a symlink; leaving it unchanged')
    link.symlink_to(node)
    print('Pi 0.87.1 ready with',version)

if __name__=='__main__':main()
