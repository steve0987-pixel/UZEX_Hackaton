import io
import json
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from uzex_agent.engine import Unsupported
from uzex_agent.planner import parse, provider_config


@patch.dict(os.environ, {'AI_PROVIDER': 'auto', 'GROQ_API_KEY': 'test-secret',
                         'GROQ_MODEL': '', 'OLLAMA_MODEL': 'local-test'})
class GroqTests(unittest.TestCase):
    def response(self, content, finish='stop'):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'finish_reason': finish, 'message': {'content': content}}]}).encode()
        return response

    def test_auto_chooses_groq(self):
        self.assertEqual(provider_config(), ('groq', 'openai/gpt-oss-120b'))

    def test_groq_request_and_confirmation(self):
        response = self.response('{"action":"analytics","metric":"turnover","filters":{"product":"цемент"}}')
        with patch('urllib.request.urlopen', return_value=response) as call:
            plan, confirm = parse('Какая сумма сделок по цементу?')
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.groq.com/openai/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret')
        payload = json.loads(request.data)
        self.assertEqual(payload['response_format']['type'], 'json_schema')
        self.assertEqual(payload['messages'][-1]['content'], 'Какая сумма сделок по цементу?')
        self.assertEqual(plan['metric'], 'turnover')
        self.assertEqual(plan['filters']['product'], 'цемент')
        self.assertTrue(confirm)

    def test_schema_and_truncated_responses_rejected(self):
        for content, finish in [('{"action":"sql","sql":"DELETE FROM deals"}', 'stop'),
                                ('{"action":"analytics"}', 'length'),
                                ('not-json', 'stop')]:
            with self.subTest(content=content, finish=finish):
                with patch('urllib.request.urlopen', return_value=self.response(content, finish)):
                    with self.assertRaises(Unsupported):
                        parse('Проанализируй данные')

    def test_http_errors_are_sanitized(self):
        for code in (400, 401, 403, 429, 500):
            error = urllib.error.HTTPError('https://test-secret.example', code,
                                           'test-secret', {}, io.BytesIO(b'test-secret'))
            with self.subTest(code=code), patch('urllib.request.urlopen', side_effect=error):
                with self.assertRaises(Unsupported) as result:
                    parse('Проанализируй данные')
                self.assertIn(str(code), str(result.exception))
                self.assertNotIn('test-secret', str(result.exception))

    def test_templates_do_not_call_groq(self):
        with patch('urllib.request.urlopen') as call:
            _, confirm = parse('Оборот за 2025 год')
        call.assert_not_called()
        self.assertFalse(confirm)

    def test_explicit_ollama_overrides_groq(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'ollama'}):
            self.assertEqual(provider_config(), ('ollama', 'local-test'))

    def test_missing_groq_key(self):
        with patch.dict(os.environ, {'AI_PROVIDER': 'groq', 'GROQ_API_KEY': ''}):
            with self.assertRaises(Unsupported):
                provider_config()


if __name__ == '__main__':
    unittest.main()
