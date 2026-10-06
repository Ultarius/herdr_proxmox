#!/usr/bin/python3
"""Root-only operator management for the dashboard.

Run on the container as root:
  herdr-operator add damien --role admin
  herdr-operator list
  herdr-operator rotate damien
  herdr-operator remove damien

The generated token is printed once. Only its SHA-256 is stored, in a file the
gateway can read but not write.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys

PATH = Path('/etc/herdr/operators.json')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}')
ROLES = ('admin', 'operator')


def digest(token):
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def load():
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get('operators') if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def save(entries):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps({'operators': entries}, indent=2))
    os.chmod(temporary, 0o640)
    try:
        import grp
        os.chown(temporary, 0, grp.getgrnam('herdr').gr_gid)
    except (ImportError, KeyError):
        pass
    temporary.replace(PATH)


def check_name(name):
    if not NAME.fullmatch(name):
        raise SystemExit('Operator names are 1-40 characters: letters, digits, dot, dash or underscore.')
    return name


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root.')
    parser = argparse.ArgumentParser(prog='herdr-operator')
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('add')
    add.add_argument('name')
    add.add_argument('--role', choices=ROLES, default='operator')
    for name in ('list', 'remove', 'rotate'):
        command = commands.add_parser(name)
        if name != 'list':
            command.add_argument('name')
    args = parser.parse_args()
    entries = load()
    if args.command == 'list':
        for entry in entries:
            print(f"{entry.get('name')}\t{entry.get('role')}")
        return
    check_name(args.name)
    matches = [entry for entry in entries if entry.get('name') == args.name]
    if args.command == 'add':
        if matches:
            raise SystemExit('That operator already exists; use rotate.')
        token = secrets.token_urlsafe(32)
        entries.append(dict(name=args.name, role=args.role, token_sha256=digest(token)))
        save(entries)
        print(token)
        return
    if not matches:
        raise SystemExit('No such operator.')
    if args.command == 'remove':
        save([entry for entry in entries if entry.get('name') != args.name])
        print(f'Removed {args.name}.')
        return
    token = secrets.token_urlsafe(32)
    matches[0]['token_sha256'] = digest(token)
    save(entries)
    print(token)


if __name__ == '__main__':
    main()
