import argparse
import json
import logging
import os
from pathlib import Path

from .data import build
from .engine import execute, render
from .planner import parse


def load_env():
    path = Path('.env')
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def main():
    load_env()
    parser = argparse.ArgumentParser(description='UZEX grounded analytics Telegram bot')
    parser.add_argument('--db', default=os.getenv('UZEX_DB', 'runtime/uzex.sqlite3'))
    commands = parser.add_subparsers(dest='command', required=True)
    ingest = commands.add_parser('ingest')
    ingest.add_argument('--source', default='uzex_spot_data')
    ask = commands.add_parser('ask')
    ask.add_argument('question')
    ask.add_argument('--json', action='store_true', help='Include exact values, SQL, file hashes and evidence')
    ask.add_argument('--confirm', action='store_true', help='Execute a model-generated plan after reviewing it')
    commands.add_parser('bot')
    commands.add_parser('sources')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        if args.command == 'ingest':
            meta = build(args.source, args.db)
            print(f"Ready: {meta['count']} deals, {meta['start']} — {meta['end']}")
        elif args.command == 'ask':
            plan, confirm = parse(args.question)
            if confirm and not args.confirm:
                print('Проверьте план. Для точного выполнения передайте этот JSON через /query:')
                print(json.dumps(plan, ensure_ascii=False, indent=2))
            else:
                result = execute(args.db, plan)
                print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
        elif args.command == 'sources':
            from .bot import source_info
            print(source_info(args.db))
        else:
            from .bot import run
            run(args.db)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
