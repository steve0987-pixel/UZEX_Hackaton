"""Independent reconciliation directly from CSV; run from the repository root."""
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uzex_agent.engine import execute


def rows(name):
    with Path('uzex_spot_data', name).open(encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def dec(value):
    return Decimal(value.replace(' ', ''))


contracts = {r['Номер контракта']: r for r in rows('contracts.csv')}
rates = {r['Дата']: dec(r['Курс']) for r in rows('usd_rates.csv')}
totals = defaultdict(lambda: Decimal(0))
counts = defaultdict(int)
statuses = defaultdict(int)
volume = defaultdict(lambda: Decimal(0))
price_amount = defaultdict(lambda: Decimal(0))
price_volume = defaultdict(lambda: Decimal(0))
for file in sorted(Path('uzex_spot_data').glob('deals_*.csv')):
    for r in rows(file.name):
        statuses[r['Состояние сделки']] += 1
        if r['Состояние сделки'] == 'Аннулирован(Не оплачен)':
            continue
        year = r['Дата сделки'][-4:]
        counts[year] += 1
        c = contracts[r['Номер контракта']]
        amount = dec(r['Сумма сделки'])
        currency = 'USD' if c['Валюта'] == 'Доллар США' else 'UZS'
        totals[year] += amount * (rates[r['Дата сделки']] if currency == 'USD' else 1)
        v = dec(r['Количество']) * dec(r['Лот'])
        unit = r['Единица измерения']
        volume[unit] += v
        price_amount[unit, currency] += amount
        price_volume[unit, currency] += v

db = 'runtime/uzex.sqlite3'
for metric, expected in [('count', counts), ('turnover', totals)]:
    result = execute(db, {'action': 'analytics', 'metric': metric, 'group_by': 'year'})
    actual = {r['group'][0]: Decimal(r['value']) for r in result['rows']}
    assert actual == dict(expected), (metric, actual, expected)
    print(metric, json.dumps({k: str(v) for k, v in actual.items()}, ensure_ascii=False))
result = execute(db, {'action': 'analytics', 'metric': 'count', 'group_by': 'status', 'cancelled': 'include'})
assert {r['group'][0]: int(r['value']) for r in result['rows']} == dict(statuses)
assert sum(statuses.values()) == 500000
result = execute(db, {'action': 'analytics', 'metric': 'volume', 'limit': 20})
assert {r['group'][0]: Decimal(r['value']) for r in result['rows']} == dict(volume)
result = execute(db, {'action': 'analytics', 'metric': 'price', 'limit': 20})
assert {tuple(r['group']): Decimal(r['value']) for r in result['rows']} == {k: price_amount[k] / v for k, v in price_volume.items()}
print('PASS: 500,000 rows; annual counts/turnover, statuses, volumes and weighted prices match original CSV.')
