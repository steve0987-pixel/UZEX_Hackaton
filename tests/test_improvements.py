import io
import json
import unittest
import zipfile
from unittest.mock import patch
from decimal import Decimal
from xml.etree import ElementTree

from uzex_agent.engine import Unsupported, compare, validate
from uzex_agent.exports import export_csv, export_xlsx
from uzex_agent.normalization import product, region


class ImprovementsTests(unittest.TestCase):
    def test_product_aliases_preserve_grades_and_sizes(self):
        self.assertEqual(product('ПОРТЛАНДЦЕМЕНТ Марка М-400'), product('sement Марка М 400'))
        self.assertEqual(product('Рис Сорт 1-сорта'), product('Рис Сорт первый сорт'))
        self.assertNotEqual(product('Цемент М400'), product('Цемент М500'))
        self.assertNotEqual(product('Хлопок сырец'), product('Хлопковое масло'))

    def test_city_and_region_stay_separate(self):
        self.assertEqual(region('Toshkent viloyati'), region('Ташкентская область'))
        self.assertNotEqual(region('Toshkent shahri'), region('Toshkent viloyati'))
        self.assertEqual(region('Farg‘ona viloyati'), region('Ферганская область'))
        with self.assertRaises(Unsupported):
            validate({'action': 'analytics', 'filters': {'region': 'Toshkent'}})

    def test_comparison_validation(self):
        with self.assertRaises(Unsupported):
            validate({'action': 'compare', 'periods': [{'date_from': '2025-01-01', 'date_to': '2025-01-31'}]})
        with self.assertRaises(Unsupported):
            validate({'action': 'analytics', 'periods': []})

    def test_comparison_exact_difference_zero_base_and_missing_price(self):
        base = {'snapshot': {}, 'sources': []}
        plan = {'action': 'compare', 'metric': 'price', 'filters': {}, 'cancelled': 'exclude',
                'periods': [{'date_from': '2025-01-01', 'date_to': '2025-01-31'}, {'date_from': '2025-02-01', 'date_to': '2025-02-28'}]}
        a = {**base, 'rows': [{'group': ['тонна', 'UZS'], 'value': '0'}, {'group': ['тонна', 'USD'], 'value': '10.1'}]}
        b = {**base, 'rows': [{'group': ['тонна', 'UZS'], 'value': '100'}, {'group': ['тонна', 'USD'], 'value': '12.12'}, {'group': ['литр', 'UZS'], 'value': '5'}]}
        with patch('uzex_agent.engine.execute', side_effect=[a, b]):
            result = compare('db', plan)
        rows = {tuple(r['group']): r for r in result['comparison']}
        self.assertEqual(Decimal(rows['тонна', 'USD']['percent']), Decimal(20))
        self.assertIsNone(rows['тонна', 'UZS']['percent'])
        self.assertIsNone(rows['литр', 'UZS']['before'])
        self.assertEqual(result['period_days'], [31, 28])

    def test_exports_preserve_precision_and_do_not_create_formulas(self):
        result = {'plan': {'action': 'analytics'}, 'snapshot': {'start': '2025-01-01', 'end': '2025-12-31'},
                  'rows': [{'group': ['=HYPERLINK("bad")'], 'name': 'Тест', 'value': '52060836360057.1205', 'deals': 1}], 'groups': 1, 'sources': []}
        csv = export_csv(result).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK", csv)
        with zipfile.ZipFile(io.BytesIO(export_xlsx(result))) as archive:
            for name in archive.namelist():
                ElementTree.fromstring(archive.read(name))
            sheet = archive.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('52060836360057.1205', sheet)
            self.assertNotIn('<f>', sheet)


if __name__ == '__main__':
    unittest.main()
