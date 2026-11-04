"""Check configured APIs without sending Telegram messages or printing keys."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uzex_agent.__main__ import load_env
from uzex_agent.bot import Telegram
from uzex_agent.engine import Unsupported
from uzex_agent.planner import parse


load_env()
failed = False
token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
if token:
    try:
        bot = Telegram(token)
        identity = bot.call('getMe')
        print('Telegram: OK @' + identity['username'], flush=True)
        webhook = bot.call('getWebhookInfo')
        print('Telegram webhook: ' + ('configured; polling requires removing it' if webhook.get('url') else 'not configured; polling available'), flush=True)
    except RuntimeError as error:
        failed = True
        print(str(error), flush=True)
else:
    failed = True
    print('Telegram: TELEGRAM_BOT_TOKEN missing', flush=True)
try:
    plan, confirm = parse('Какова сумма сделок по цементу за 2025 год?')
    expected = {'date_from': '2025-01-01', 'date_to': '2025-12-31', 'product': 'цемент'}
    if plan['action'] != 'analytics' or plan['metric'] != 'turnover' or plan['filters'] != expected or not confirm:
        raise Unsupported('Unexpected plan for smoke-test question')
    print('AI: OK; test question parsed correctly and requires confirmation', flush=True)
except Unsupported as error:
    failed = True
    print(str(error), flush=True)
sys.exit(1 if failed else 0)
