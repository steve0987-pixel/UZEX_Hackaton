import csv
import json
import io
import urllib.error
import os
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from uzex_agent.data import build, connect
from uzex_agent.engine import Unsupported, execute, render, validate
from uzex_agent.planner import parse
from uzex_agent.bot import Telegram


def write(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.db = cls.root / 'test.sqlite3'
        write(cls.root / 'contracts.csv', [
            {'Номер контракта': key, 'Валюта': currency, 'Единица измерения': unit,
             'Лот': lot, 'Категория товара': 'Стройматериалы'}
            for key, currency, unit, lot in [('1', 'Узбекский сум', 'тонна', '2'),
                                           ('2', 'Доллар США', 'тонна', '1'),
                                           ('3', 'Узбекский сум', 'литр', '1')]])
        write(cls.root / 'participants.csv', [{'ИНН': '900000001', 'Наименование': 'Тест'}])
        write(cls.root / 'brokers.csv', [{'Код брокера': 'BK001', 'Наименование': 'Брокер'}])
        write(cls.root / 'usd_rates.csv', [{'Дата': '01.01.2025', 'Курс': '100.01'}, {'Дата': '31.12.2025', 'Курс': '200'}])
        rows = []
        specs = [('1', '1', '5', '2', '100.1', 'тонна', 'Исполнен'),
                 ('2', '1', '1', '2', '40.2', 'тонна', 'Исполнен'),
                 ('3', '2', '3', '1', '12', 'тонна', 'Исполнен'),
                 ('4', '3', '5', '1', '50', 'литр', 'Исполнен'),
                 ('5', '1', '1', '2', '999', 'тонна', 'Аннулирован(Не оплачен)')]
        for id_, contract, quantity, lot, amount, unit, status in specs:
            rows.append({'Номер сделки': id_, 'Номер контракта': contract,
                         'Дата сделки': '31.12.2025' if id_ == '5' else '01.01.2025',
                         'Количество': quantity, 'Лот': lot, 'Сумма сделки': amount,
                         'Единица измерения': unit, 'Состояние сделки': status,
                         'Наименование товара': 'ЦЕМЕНТ' if id_ != '4' else 'Бензин',
                         'Регион': 'Ташкент', 'Форма контракта': 'Спот',
                         'Тип контракта': 'Экспортный' if contract == '2' else 'Внутренний',
                         'ИНН продавца': '900000001', 'ИНН покупателя': '900000001',
                         'Брокер продавца': 'BK001', 'Брокер покупателя': 'BK001'})
        write(cls.root / 'deals_2025.csv', rows)
        build(cls.root, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def query(self, **kwargs):
        return execute(self.db, {'action': 'analytics', **kwargs})

    def test_decimal_turnover_and_daily_currency(self):
        result = self.query(metric='turnover')
        self.assertEqual(result['rows'][0]['value'], '1390.42')
        self.assertEqual(result['matched'], 4)
        self.assertIn('usd_rates.csv', [s['file'] for s in result['sources']])

    def test_cancelled_explicit(self):
        self.assertEqual(self.query(cancelled='include')['matched'], 5)
        self.assertEqual(self.query(cancelled='only')['matched'], 1)
        with self.assertRaises(Unsupported):
            self.query(filters={'status': 'Аннулирован(Не оплачен)'})

    def test_weighted_price_and_separate_currencies(self):
        rows = self.query(metric='price', filters={'product': 'цемент'})['rows']
        values = {tuple(row['group']): Decimal(row['value']) for row in rows}
        self.assertEqual(values[('тонна', 'UZS')], Decimal('140.3') / Decimal(12))
        self.assertEqual(values[('тонна', 'USD')], Decimal(4))

    def test_volume_uses_lots_and_separates_units(self):
        rows = self.query(metric='volume')['rows']
        self.assertEqual({tuple(r['group']): r['value'] for r in rows}, {('тонна',): '15', ('литр',): '5'})

    def test_sources_and_lookup(self):
        result = execute(self.db, {'action': 'deal', 'id': '1'})
        self.assertEqual(result['record']['Сумма сделки'], '100.1')
        self.assertEqual(result['sources'][0]['row'], 2)
        self.assertEqual(len(result['sources'][0]['sha256']), 64)
        self.assertEqual(result['record']['Валюта суммы и стартовой цены (из контракта)'], 'UZS')
        self.assertEqual(result['sources'][1]['file'], 'contracts.csv')
        self.assertIn('Вымышленные', render(result))
        self.assertIsNone(execute(self.db, {'action': 'deal', 'id': '999'})['record'])

    def test_sql_injection_is_literal(self):
        result = self.query(filters={'product': "' OR 1=1 --"})
        self.assertEqual(result['matched'], 0)
        self.assertEqual(self.query()['matched'], 4)
        self.assertEqual(self.query(filters={'product': '%'})['matched'], 0)

    def test_seller_names_are_sourced(self):
        result = self.query(metric='turnover', group_by='seller')
        self.assertEqual(result['rows'][0]['name'], 'Тест')
        self.assertIn('participants.csv', [s['file'] for s in result['sources']])

    def test_dates_reject_incomplete_coverage(self):
        with self.assertRaises(Unsupported):
            self.query(filters={'date_from': '2024-01-01'})
        with self.assertRaises(Unsupported):
            self.query(filters={'date_to': '2026-01-01'})
        with self.assertRaises(Unsupported):
            self.query(filters={'date_from': '2025-02-30'})

    def test_unknown_operations_and_filters_rejected(self):
        for plan in [{'action': 'sql', 'sql': 'DROP TABLE deals'},
                     {'action': 'analytics', 'filters': {'delivered': 'yes'}},
                     {'action': 'analytics', 'limit': True},
                     {'action': 'analytics', 'metric': 'prediction'},
                     {'action': 'deal', 'id': '1', 'filters': {}}]:
            with self.subTest(plan=plan), self.assertRaises(Unsupported):
                validate(plan)

    @patch.dict(os.environ, {'OLLAMA_MODEL': '', 'GROQ_API_KEY': '', 'AI_PROVIDER': 'auto'})
    def test_offline_parser_does_not_drop_extra_conditions(self):
        plan, confirm = parse('Сколько сделок за 2025 год?')
        self.assertEqual(plan['metric'], 'count')
        self.assertFalse(confirm)
        for question in ['Сколько сделок за 2025 год в Ташкенте?', 'Оборот за 2025 год кроме экспорта', 'Какой будет цена завтра?']:
            with self.subTest(question=question), self.assertRaises(Unsupported):
                parse(question)

    @patch.dict(os.environ, {'OLLAMA_MODEL': 'test', 'AI_PROVIDER': 'ollama'})
    def test_model_plan_requires_confirmation(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'message': {'content': '{"action":"analytics","metric":"count"}'}}).encode()
        with patch('urllib.request.urlopen', return_value=response):
            plan, confirm = parse('Посчитай все сделки без аннулированных')
        self.assertTrue(confirm)
        self.assertEqual(plan['metric'], 'count')

    def test_database_is_read_only(self):
        import sqlite3
        with closing(connect(self.db)) as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM deals')

    def test_telegram_errors_do_not_expose_token(self):
        with patch('urllib.request.urlopen', side_effect=OSError('url with secret-token')):
            with self.assertRaises(RuntimeError) as context:
                Telegram('secret-token').call('getMe')
        self.assertNotIn('secret-token', str(context.exception))

    def test_telegram_http_error_keeps_safe_reason(self):
        error = urllib.error.HTTPError('https://secret-token.example', 400, 'secret-token', {},
                                       io.BytesIO(json.dumps({'description': 'Bad Request: query is too old and response timeout expired or query ID is invalid'}).encode()))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as context:
                Telegram('secret-token').call('answerCallbackQuery', callback_query_id='example')
        self.assertIn('callback_expired', str(context.exception))
        self.assertNotIn('secret-token', str(context.exception))


if __name__ == '__main__':
    unittest.main()
