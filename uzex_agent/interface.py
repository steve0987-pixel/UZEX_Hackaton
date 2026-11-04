"""Telegram navigation and conversation state; all analytics use the existing engine."""
import json
import logging
import os
import re
import secrets
import time
from contextlib import closing

from .data import connect
from .engine import Unsupported, describe, execute, render
from .planner import HELP, parse
from .errors import TelegramError
from urllib.parse import urlparse


MENU = {
    'keyboard': [[{'text': '📊 Обзор'}, {'text': '📈 Аналитика'}],
                 [{'text': '🔎 Поиск'}, {'text': '💬 Задать вопрос'}],
                 [{'text': '📚 Источники'}, {'text': '❓ Помощь'}]],
    'resize_keyboard': True, 'is_persistent': True,
    'input_field_placeholder': 'Выберите раздел или напишите вопрос',
}
COMMANDS = [{'command': command, 'description': description} for command, description in [
    ('start', 'Открыть главное меню'), ('menu', 'Главное меню'),
    ('analytics', 'Отчёты и выбор периода'), ('search', 'Найти сделку, контракт или компанию'),
    ('sources', 'Источники и покрытие данных'), ('help', 'Примеры вопросов'),
    ('cancel', 'Отменить текущий запрос')]]
REPORTS = {'turnover': '💰 Сумма сделок', 'count': '🧾 Количество сделок',
           'monthly': '📈 Динамика по месяцам', 'sellers': '🏆 Топ-10 продавцов',
           'status': '📋 Состояния сделок'}
KINDS = {'deal': 'Сделка', 'contract': 'Контракт', 'participant': 'Участник', 'broker': 'Брокер'}


def main_menu(private=True):
    url = os.getenv('MINIAPP_URL', '').strip()
    parsed = urlparse(url)
    if private and parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password:
        return {**MENU, 'keyboard': [[{'text': '✨ Открыть веб-меню', 'web_app': {'url': url}}]] + MENU['keyboard']}
    return MENU


def buttons(rows):
    return {'inline_keyboard': [[{'text': label, 'callback_data': data} for label, data in row] for row in rows]}


class Interface:
    def __init__(self, api, db):
        self.api, self.db = api, db
        with closing(connect(db)) as con:
            self.snapshot = json.loads(con.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        self.pending = {}
        self.lookup = {}

    def send(self, chat, text, markup=None):
        self.api.send(chat, text, reply_markup=main_menu(private=chat > 0) if markup is None else markup)

    def clear(self, owner):
        self.pending = {k: v for k, v in self.pending.items() if v[0] != owner}
        self.lookup.pop(owner, None)

    def home(self, chat):
        m = self.snapshot
        count = f"{m['count']:,}".replace(',', ' ')
        self.send(chat, 'UZEX · AI-аналитик\n\n'
                  'Сделки, товары и участники — ответы с источниками.\n\n'
                  f"🧾 {count} сделок\n🗓 {m['start']} — {m['end']}\n\n"
                  'Выберите отчёт в «Аналитике», найдите запись по номеру или задайте вопрос своими словами.\n\n'
                  'Вымышленные данные для хакатона. Состояния сделок на конец выгрузки.')

    def analytics(self, chat):
        self.send(chat, '📈 Аналитика\n\nВыберите отчёт, затем период. Суммы — в UZS с пересчётом USD по курсу на дату сделки.\n\n'
                  'Аннулированные исключены из сумм и рейтингов. Отчёт по состояниям включает все сделки.',
                  buttons([[(label, 'report:' + key)] for key, label in REPORTS.items()]))

    def period(self, chat, report):
        if report not in REPORTS:
            raise Unsupported('Этот отчёт недоступен. Откройте «Аналитика».')
        m = self.snapshot
        years = list(range(int(m['start'][:4]), int(m['end'][:4]) + 1))
        choices = []
        for year in years:
            partial = m['start'] > f'{year}-01-01' or m['end'] < f'{year}-12-31'
            choices.append((str(year) + (' · часть года' if partial else ''), f'run:{report}:{year}'))
        rows = [choices[i:i + 2] for i in range(0, len(choices), 2)]
        # Monthly reports stay within a year so all twelve months fit the result limit.
        if report != 'monthly':
            rows.append([('Весь период', f'run:{report}:all')])
        rows.append([('← К отчётам', 'nav:analytics')])
        self.send(chat, REPORTS[report] + '\n\nВыберите период. Неполные годы ограничены датами выгрузки.', buttons(rows))

    def report_plan(self, report, period):
        if report not in REPORTS or (period == 'all' and report == 'monthly'):
            raise Unsupported('Недоступный отчёт или период.')
        start, end = self.snapshot['start'], self.snapshot['end']
        if period != 'all':
            if not re.fullmatch(r'20\d{2}', period) or not int(start[:4]) <= int(period) <= int(end[:4]):
                raise Unsupported('Выберите год из меню.')
            start, end = max(start, period + '-01-01'), min(end, period + '-12-31')
        return {'action': 'analytics', 'metric': 'count' if report in ['count', 'status'] else 'turnover',
                'group_by': {'monthly': 'month', 'sellers': 'seller', 'status': 'status'}.get(report, 'none'),
                'limit': 12 if report == 'monthly' else 10,
                'cancelled': 'include' if report == 'status' else 'exclude',
                'filters': {'date_from': start, 'date_to': end}}

    def result(self, chat, plan):
        self.send(chat, '📊 Результат\n\n' + render(execute(self.db, plan)))

    def handle(self, message, sender, callback=None):
        chat = message['chat']['id']
        owner = (sender, chat)
        now = time.monotonic()
        self.pending = {k: v for k, v in self.pending.items() if now - v[2] < 600}
        self.lookup = {k: v for k, v in self.lookup.items() if now - v[1] < 600}
        if not callback and 'web_app_data' in message:
            self.handle_webapp(message, sender)
            return
        if callback:
            # Acknowledging a click only clears Telegram's loading indicator.
            # A delayed/expired acknowledgement must not block the actual action.
            try:
                self.api.call('answerCallbackQuery', callback_query_id=callback['id'])
            except TelegramError as error:
                logging.warning('Callback acknowledgement failed; continuing: %s', error)
            data = callback.get('data', '')
            if data == 'nav:analytics':
                self.clear(owner)
                self.analytics(chat)
            elif data.startswith('report:'):
                self.clear(owner)
                self.period(chat, data[7:])
            elif data.startswith('run:'):
                parts = data.split(':')
                if len(parts) != 3:
                    raise Unsupported('Некорректная кнопка. Откройте меню заново.')
                self.clear(owner)
                self.result(chat, self.report_plan(parts[1], parts[2]))
            elif data.startswith('lookup:'):
                kind = data[7:]
                if kind not in KINDS:
                    raise Unsupported('Неизвестный тип записи.')
                self.clear(owner)
                if len(self.lookup) >= 1000:
                    self.lookup.pop(next(iter(self.lookup)))
                self.lookup[owner] = (kind, now)
                examples = {'deal': 'номер сделки, например 100001', 'contract': 'номер контракта, например 100001',
                            'participant': 'ИНН компании из 9 цифр', 'broker': 'код брокера, например BK001'}
                self.send(chat, '🔎 ' + KINDS[kind] + '\n\nПришлите ' + examples[kind] + '.\nДля отмены: /cancel.')
            elif data.startswith(('confirm:', 'cancel:')):
                action, key = data.split(':', 1)
                item = self.pending.get(key)
                if item is None or item[0] != owner:
                    self.send(chat, 'Этот запрос устарел или принадлежит другому пользователю. Задайте вопрос заново.')
                    return
                self.pending.pop(key)
                if action == 'cancel':
                    self.send(chat, 'Запрос отменён. Выберите раздел или задайте другой вопрос.')
                else:
                    self.result(chat, item[1])
            else:
                self.send(chat, 'Кнопка устарела. Откройте /menu.')
            return
        text = message.get('text', '').strip()
        command = text.split(maxsplit=1)[0].split('@')[0] if text else ''
        if command in ['/start', '/menu', '/cancel'] or text == '📊 Обзор':
            self.clear(owner)
            self.home(chat)
        elif command == '/analytics' or text == '📈 Аналитика':
            self.clear(owner)
            self.analytics(chat)
        elif command == '/search' or text == '🔎 Поиск':
            self.clear(owner)
            self.send(chat, '🔎 Поиск записи\n\nЧто хотите найти?', buttons([
                [('🧾 Сделку', 'lookup:deal'), ('📄 Контракт', 'lookup:contract')],
                [('🏢 Участника', 'lookup:participant'), ('🤝 Брокера', 'lookup:broker')]]))
        elif text == '💬 Задать вопрос':
            self.clear(owner)
            self.send(chat, '💬 Спросите о данных\n\nНапример:\n'
                      '• Какова сумма сделок по цементу за 2025 год?\n'
                      '• Какова средневзвешенная цена бензина за 2025 год?\n'
                      '• Сколько экспортных сделок за 2024 год?\n\n'
                      'Напишите вопрос сообщением. Затем проверьте фильтры и нажмите «Рассчитать». '
                      'Указывайте все условия в одном сообщении.')
        elif command == '/sources' or text == '📚 Источники':
            self.clear(owner)
            m = self.snapshot
            lines = ['📚 Источники', f"\nПокрытие: {m['start']} — {m['end']}\n"]
            lines += [f"• {name}: {item['rows']:,} записей".replace(',', ' ') for name, item in m['manifest'].items()]
            lines.append('\nОтветы основаны на этой выгрузке. Вымышленные данные; обновлений в реальном времени нет.')
            self.send(chat, '\n'.join(lines))
        elif command == '/help' or text == '❓ Помощь':
            self.clear(owner)
            self.send(chat, '❓ Как пользоваться\n\n' + HELP)
        elif text:
            if owner in self.lookup:
                kind, _ = self.lookup[owner]
                pattern = r'\d{9}' if kind == 'participant' else r'BK\d{3}' if kind == 'broker' else r'\d{1,20}'
                if re.fullmatch(pattern, text.upper()):
                    self.lookup.pop(owner)
                    self.result(chat, {'action': kind, 'id': text.upper()})
                    return
                self.send(chat, 'Ожидаю идентификатор выбранной записи. Для обычного вопроса нажмите «💬 Задать вопрос» или /cancel.')
                return
            self.clear(owner)
            plan, confirm = parse(text)
            if confirm:
                if len(self.pending) >= 1000:
                    self.pending.pop(next(iter(self.pending)))
                key = secrets.token_hex(12)
                self.pending[key] = (owner, plan, now)
                self.send(chat, '🔍 Проверьте запрос\n\n' + describe(plan) + '\n\nЕсли условия верны, запустите расчёт.',
                          buttons([[('✅ Рассчитать', 'confirm:' + key), ('✖ Отмена', 'cancel:' + key)]]))
            else:
                self.result(chat, plan)
        else:
            self.send(chat, 'Пока поддерживаются текстовые вопросы. Напишите вопрос или выберите раздел.')

    def handle_webapp(self, message, sender):
        """Treat service-message payloads as untrusted; never accept computed values."""
        try:
            raw = message['web_app_data']['data']
            if not isinstance(raw, str) or len(raw.encode('utf-8')) > 4096:
                raise ValueError()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            kind = data.get('type')
            chat = message['chat']['id']
            if kind == 'report' and set(data) == {'type', 'report', 'period'}:
                if not isinstance(data['report'], str) or not isinstance(data['period'], str):
                    raise ValueError()
                plan = self.report_plan(data['report'], data['period'])
                self.clear((sender, chat))
                self.result(chat, plan)
            elif kind == 'lookup' and set(data) == {'type', 'kind', 'id'}:
                if not isinstance(data['kind'], str) or data['kind'] not in KINDS or not isinstance(data['id'], str):
                    raise ValueError()
                pattern = r'\d{9}' if data['kind'] == 'participant' else r'BK\d{3}' if data['kind'] == 'broker' else r'\d{1,20}'
                if not re.fullmatch(pattern, data['id']):
                    raise ValueError()
                self.clear((sender, chat))
                self.result(chat, {'action': data['kind'], 'id': data['id']})
            elif kind == 'question' and set(data) == {'type', 'text'}:
                if not isinstance(data['text'], str) or not data['text'].strip() or len(data['text']) > 1000:
                    raise ValueError()
                self.handle({'chat': message['chat'], 'text': data['text']}, sender)
            else:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as error:
            if isinstance(error, Unsupported):
                raise
            raise Unsupported('Некорректный запрос веб-меню. Откройте приложение заново.') from None
