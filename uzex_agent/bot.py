import json
import logging
import os
import time
import urllib.request
import urllib.error
from contextlib import closing
from pathlib import Path

from .data import connect
from .engine import Unsupported
from .interface import COMMANDS, MENU, Interface
from .errors import TelegramError


def telegram_reason(description):
    description = str(description).casefold()
    # Only fixed categories reach logs; API response text might contain user data.
    for fragment, reason in [('query is too old', 'callback_expired'),
                             ('query id is invalid', 'callback_expired'),
                             ('message is too long', 'message_too_long'),
                             ('button_data_invalid', 'invalid_button_data'),
                             ('reply markup', 'invalid_keyboard'),
                             ('too many requests', 'rate_limited'),
                             ('terminated by other getupdates', 'duplicate_polling'),
                             ('webhook is active', 'webhook_active'),
                             ('bot was blocked', 'bot_blocked'),
                             ('chat not found', 'chat_not_found')]:
        if fragment in description:
            return reason
    return 'api_rejected_request'


class Telegram:
    def __init__(self, token):
        self.base = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, **payload):
        request = urllib.request.Request(self.base + method, data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                reason = telegram_reason(json.load(error).get('description', ''))
            except (ValueError, TypeError, AttributeError):
                reason = 'api_rejected_request'
            finally:
                error.close()
            raise TelegramError(method, error.code, reason) from None
        except (OSError, ValueError) as error:
            raise TelegramError(method, reason='network_or_response_error') from None
        if not result.get('ok'):
            raise TelegramError(method, result.get('error_code'), telegram_reason(result.get('description', '')))
        return result['result']

    def send(self, chat, text, **kwargs):
        for start in range(0, len(text), 1800):
            options = dict(kwargs)
            if start + 1800 < len(text):
                options.pop('reply_markup', None)
            self.call('sendMessage', chat_id=chat, text=text[start:start + 1800], **options)


def source_info(db):
    with closing(connect(db)) as con:
        m = json.loads(con.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
    return (f"Локальная выгрузка: {m['count']} сделок, {m['start']} — {m['end']}.\n"
            'Вымышленные данные. Состояния сделок на конец выгрузки.\n' +
            '\n'.join(f"{name}: {item['rows']} записей; SHA256 {item['sha256']}" for name, item in m['manifest'].items()))


def run(db):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise ValueError('Задайте TELEGRAM_BOT_TOKEN в .env (токен от @BotFather).')
    if not Path(db).is_file():
        raise ValueError('Сначала выполните python3 -m uzex_agent ingest')
    allowed = {int(v.strip()) for v in os.getenv('TELEGRAM_ALLOWED_USERS', '').split(',') if v.strip()}
    api = Telegram(token)
    me = api.call('getMe')
    try:
        api.call('setMyCommands', commands=COMMANDS)
        api.call('setChatMenuButton', menu_button={'type': 'commands'})
    except RuntimeError:
        logging.warning('Could not register Telegram command menu; /start is available')
    interface = Interface(api, db)
    logging.info('Bot @%s ready', me['username'])
    state = Path(db).parent / f"telegram-offset-{me['id']}.txt"
    offset = int(state.read_text()) if state.exists() else 0
    while True:
        try:
            updates = api.call('getUpdates', offset=offset, timeout=25, allowed_updates=['message', 'callback_query'])
            for update in updates:
                callback = update.get('callback_query')
                message = callback.get('message', {}) if callback else update.get('message', {})
                sender = (callback or message).get('from', {}).get('id')
                chat = message.get('chat', {}).get('id')
                if chat is not None and (not allowed or sender in allowed):
                    try:
                        interface.handle(message, sender, callback)
                    except Unsupported as error:
                        api.send(chat, 'Не удалось выполнить запрос\n\n' + str(error), reply_markup=MENU)
                    except TelegramError as error:
                        logging.error('%s', error)
                        api.send(chat, 'Telegram не смог доставить ответ. Откройте /menu и повторите запрос.', reply_markup=MENU)
                    except Exception as error:
                        # Do not log exception strings: HTTP errors may contain a bot token.
                        logging.error('Request failed: %s', type(error).__name__)
                        api.send(chat, 'Не удалось выполнить запрос. Повторите позже; неподтверждённый результат не выдан.')
                offset = update['update_id'] + 1
                temp = state.with_suffix('.tmp')
                temp.write_text(str(offset))
                temp.replace(state)
        except (OSError, RuntimeError, ValueError) as error:
            logging.warning('Telegram polling failed: %s; retrying', error if isinstance(error, TelegramError) else type(error).__name__)
            time.sleep(3)
