"""Local-owner account administration. No credentials are passed to a model."""
import argparse
import fcntl
import os
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_ai.store import Store
from local_ai.mobile_auth import MobileAuth
from local_ai.passkeys import PasskeyAuth


def main():
    parser=argparse.ArgumentParser(description='Local account, mobile pairing, and private passkey administration')
    parser.add_argument('--database',type=Path,default=ROOT/'runtime/bot.sqlite3')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('accounts')
    create=commands.add_parser('create-account');create.add_argument('name');create.add_argument('--admin',action='store_true')
    invite=commands.add_parser('invite')
    target=invite.add_mutually_exclusive_group(required=True)
    target.add_argument('--account');target.add_argument('--telegram-user',type=int)
    revoke=commands.add_parser('revoke-device');revoke.add_argument('account');revoke.add_argument('device')
    for name in ('passkey-invite','passkeys'):
        command=commands.add_parser(name)
        target=command.add_mutually_exclusive_group(required=True)
        target.add_argument('--account');target.add_argument('--telegram-user',type=int)
    revoke_key=commands.add_parser('revoke-passkey')
    revoke_key.add_argument('account');revoke_key.add_argument('credential')
    args=parser.parse_args()
    os.umask(0o077)
    args.database.parent.mkdir(parents=True,exist_ok=True)
    # A read-only schema probe prevents accidentally migrating under an old worker.
    if args.database.exists():
        with sqlite3.connect(f'file:{args.database}?mode=ro',uri=True) as probe:
            migrated=probe.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
        if not migrated:
            with (args.database.parent/'service.lock').open('a') as lock:
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SystemExit('首次迁移前请先停止旧服务；迁移会自动备份数据库。')
                store=Store(args.database)
        else:
            store=Store(args.database)
    else:
        store=Store(args.database)
    try:
        auth=MobileAuth(store)
        if args.command=='accounts':
            for row in store.db.execute('SELECT id,display_name,is_admin,enabled FROM accounts ORDER BY display_name'):
                print(dict(row))
        elif args.command=='create-account':
            account=store.create_account(args.name,is_admin=args.admin)
            print('Account:',account['id'])
        elif args.command=='invite':
            account=store.account(args.account) if args.account else store.account_for_uid(args.telegram_user)
            print('Account:',account['id'])
            print('One-use invitation (expires in 10 minutes):',auth.invite(account['id']))
        elif args.command=='revoke-device':
            auth.revoke(args.account,args.device)
            print('Device revoked.')
        elif args.command in ('passkey-invite','passkeys'):
            passkeys=PasskeyAuth(store)
            account=store.account(args.account) if args.account else store.account_for_uid(args.telegram_user)
            print('Account:',account['id'])
            if args.command=='passkey-invite':
                print('One-use passkey registration code (expires in 10 minutes):',passkeys.invite(account['id']))
                print('Enter this code only on your configured HTTPS Harness page. Never send it in a URL.')
            else:
                for credential in passkeys.list_credentials(account['id']):
                    print(credential)
        elif args.command=='revoke-passkey':
            PasskeyAuth(store).revoke(args.account,args.credential)
            print('Passkey revoked. Its associated browser sessions can no longer authenticate.')
    finally:
        store.db.close()


if __name__=='__main__':
    main()
