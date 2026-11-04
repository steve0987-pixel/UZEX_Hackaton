import json
import sqlite3
import tempfile
import time
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from uzex_agent.bot import Telegram
from uzex_agent.engine import Unsupported
from uzex_agent.interface import Interface, main_menu
from uzex_agent.errors import TelegramError


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = Path(self.temp.name) / 'db.sqlite3'
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE metadata(key TEXT, value TEXT)')
        con.execute('INSERT INTO metadata VALUES(?,?)', ('snapshot', json.dumps({
            'start': '2022-01-03', 'end': '2026-10-30', 'count': 500000, 'manifest': {}})))
        con.commit()
        con.close()
        self.api = MagicMock()
        self.ui = Interface(self.api, db)
        self.message = {'chat': {'id': 1}, 'text': ''}

    def callback(self, data, sender=7):
        self.ui.handle(self.message, sender, {'id': 'callback-id', 'data': data})

    def test_partial_years_use_available_coverage(self):
        self.assertEqual(self.ui.report_plan('turnover', '2022')['filters'],
                         {'date_from': '2022-01-03', 'date_to': '2022-12-31'})
        self.assertEqual(self.ui.report_plan('turnover', '2026')['filters'],
                         {'date_from': '2026-01-01', 'date_to': '2026-10-30'})
        with self.assertRaises(Unsupported):
            self.ui.report_plan('turnover', '2027')
        with self.assertRaises(Unsupported):
            self.ui.report_plan('monthly', 'all')

    def test_navigation_does_not_call_model(self):
        with patch('uzex_agent.interface.parse') as parse:
            self.ui.handle({**self.message, 'text': '/start'}, 7)
            self.ui.handle({**self.message, 'text': '📈 Аналитика'}, 7)
            self.callback('report:turnover')
        parse.assert_not_called()

    def test_expired_acknowledgement_still_opens_turnover_period_picker(self):
        self.api.call.side_effect = TelegramError('answerCallbackQuery', 400, 'callback_expired')
        self.callback('report:turnover')
        self.api.send.assert_called_once()
        markup = self.api.send.call_args.kwargs['reply_markup']
        data = [button['callback_data'] for row in markup['inline_keyboard'] for button in row]
        self.assertIn('run:turnover:2025', data)

    def test_failed_acknowledgement_does_not_bypass_confirmation_ownership(self):
        self.api.call.side_effect = TelegramError('answerCallbackQuery', 400, 'callback_expired')
        plan = self.ui.report_plan('count', '2025')
        self.ui.pending['test'] = ((7, 1), plan, time.monotonic())
        with patch.object(self.ui, 'result') as result:
            self.callback('confirm:test', sender=8)
            result.assert_not_called()

    def test_confirmation_is_owned_and_single_use(self):
        plan = self.ui.report_plan('count', '2025')
        self.ui.pending['test'] = ((7, 1), plan, time.monotonic())
        with patch.object(self.ui, 'result') as result:
            self.callback('confirm:test', sender=8)
            result.assert_not_called()
            self.assertIn('test', self.ui.pending)
            self.callback('confirm:test')
            self.callback('confirm:test')
            result.assert_called_once_with(1, plan)

    def test_cancel_and_expiration_never_execute(self):
        plan = self.ui.report_plan('count', '2025')
        self.ui.pending['cancel'] = ((7, 1), plan, time.monotonic())
        self.ui.pending['expired'] = ((7, 1), plan, time.monotonic() - 601)
        with patch.object(self.ui, 'result') as result:
            self.callback('cancel:cancel')
            self.callback('confirm:expired')
            result.assert_not_called()
        self.assertFalse(self.ui.pending)

    def test_lookup_does_not_guess_an_invalid_identifier(self):
        self.callback('lookup:participant')
        with patch.object(self.ui, 'result') as result, patch('uzex_agent.interface.parse') as parse:
            self.ui.handle({**self.message, 'text': 'some company'}, 7)
            result.assert_not_called()
            parse.assert_not_called()
            self.ui.handle({**self.message, 'text': '915688255'}, 7)
            result.assert_called_once_with(1, {'action': 'participant', 'id': '915688255'})

    def test_new_question_invalidates_previous_plan(self):
        plan = self.ui.report_plan('count', '2025')
        self.ui.pending['old'] = ((7, 1), plan, time.monotonic())
        with patch('uzex_agent.interface.parse', return_value=(plan, True)):
            self.ui.handle({**self.message, 'text': 'Новый вопрос'}, 7)
        self.assertNotIn('old', self.ui.pending)
        self.assertEqual(len(self.ui.pending), 1)

    def test_message_chunks_only_attach_actions_to_final_chunk(self):
        telegram = Telegram('test-token')
        markup = {'inline_keyboard': [[{'text': 'Run', 'callback_data': 'run'}]]}
        with patch.object(telegram, 'call') as call:
            telegram.send(1, 'a' * 2000, reply_markup=markup)
        self.assertNotIn('reply_markup', call.call_args_list[0].kwargs)
        self.assertEqual(call.call_args_list[1].kwargs['reply_markup'], markup)

    def test_webapp_report_recomputes_from_local_data(self):
        message = {**self.message, 'web_app_data': {'data': json.dumps({'type': 'report', 'report': 'turnover', 'period': '2026'})}}
        with patch.object(self.ui, 'result') as result:
            self.ui.handle(message, 7)
        result.assert_called_once_with(1, self.ui.report_plan('turnover', '2026'))

    def test_webapp_rejects_injected_results_and_invalid_actions(self):
        payloads = [{'type': 'report', 'report': 'turnover', 'period': '2025', 'value': '999'},
                    {'type': 'sql', 'query': 'SELECT * FROM deals'},
                    {'type': 'lookup', 'kind': 'deal', 'id': "1 OR 1=1"},
                    {'type': 'question', 'text': 'a' * 1001}, []]
        for data in payloads:
            with self.subTest(data=data), patch.object(self.ui, 'result') as result:
                with self.assertRaises(Unsupported):
                    self.ui.handle({**self.message, 'web_app_data': {'data': json.dumps(data)}}, 7)
                result.assert_not_called()

    def test_webapp_free_question_still_requires_confirmation(self):
        plan = self.ui.report_plan('count', '2025')
        with patch('uzex_agent.interface.parse', return_value=(plan, True)), patch.object(self.ui, 'result') as result:
            self.ui.handle({**self.message, 'web_app_data': {'data': json.dumps({'type': 'question', 'text': 'Посчитай сделки'})}}, 7)
            result.assert_not_called()
        self.assertEqual(len(self.ui.pending), 1)

    def test_webapp_button_only_uses_https_and_private_chat(self):
        with patch.dict(os.environ, {'MINIAPP_URL': 'https://example.com'}):
            self.assertIn('web_app', main_menu()['keyboard'][0][0])
            self.assertNotIn('web_app', main_menu(private=False)['keyboard'][0][0])
        with patch.dict(os.environ, {'MINIAPP_URL': 'http://localhost:8080'}):
            self.assertNotIn('web_app', main_menu()['keyboard'][0][0])


if __name__ == '__main__':
    unittest.main()
