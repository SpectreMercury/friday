"""Seed an independent Friday skill library without overwriting local edits."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(destination):
    destination = Path(destination).expanduser()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    installed = []
    for source in sorted((ROOT / 'assistant-skills').glob('*/SKILL.md')):
        folder = destination / source.parent.name
        if folder.is_symlink():
            raise ValueError('Skill folders must not be symbolic links')
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / 'SKILL.md'
        try:
            # Exclusive creation means reinstalling never destroys an iteration.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'w') as output:
            output.write(source.read_text())
        installed.append(source.parent.name)
    return installed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default=os.environ.get('FRIDAY_SKILLS_DIR') or str(Path.home() / '.friday/skills'))
    args = parser.parse_args()
    print('Library:', Path(args.directory).expanduser())
    print('Installed missing skills:', ', '.join(install(args.directory)) or 'none (existing files retained)')
