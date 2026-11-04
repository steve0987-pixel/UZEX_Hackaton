import csv
import hashlib
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from .normalization import product, region


def decimal(value):
    result = Decimal(str(value).replace(' ', '').replace('\xa0', ''))
    if not result.is_finite():
        raise ValueError('Non-finite number')
    return result


def date(value):
    return datetime.strptime(value, '%d.%m.%Y').date().isoformat()


def records(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        yield from enumerate(csv.DictReader(stream), start=2)


def build(source, target):
    """Validate and atomically replace the database; never mutate the CSV files."""
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.building')
    if temporary.exists():
        temporary.unlink()
    con = sqlite3.connect(temporary)
    try:
        con.executescript('''
        CREATE TABLE entities(kind TEXT, id TEXT, payload TEXT, file TEXT, row INTEGER,
                              PRIMARY KEY(kind,id));
        CREATE TABLE deals(
          id TEXT PRIMARY KEY, date TEXT, product TEXT, category TEXT, region TEXT,
          status TEXT, form TEXT, contract_type TEXT, seller TEXT, buyer TEXT,
          seller_broker TEXT, buyer_broker TEXT, contract TEXT, currency TEXT,
          unit TEXT, volume TEXT, amount TEXT, amount_uzs TEXT,
          file TEXT, row INTEGER, payload TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
        ''')
        contracts, rates = {}, {}
        files = [source / name for name in ('contracts.csv', 'participants.csv', 'brokers.csv', 'usd_rates.csv')]
        files += sorted(source.glob('deals_*.csv'))
        if len(files) == 4:
            raise ValueError('No deals_*.csv files')
        manifest = {}
        for path in files:
            with path.open('rb') as stream:
                manifest[path.name] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest(), 'rows': 0}
        for kind, filename, key in [('contract', 'contracts.csv', 'Номер контракта'),
                                    ('participant', 'participants.csv', 'ИНН'),
                                    ('broker', 'brokers.csv', 'Код брокера')]:
            for row, record in records(source / filename):
                con.execute('INSERT INTO entities VALUES(?,?,?,?,?)',
                            (kind, record[key], json.dumps(record, ensure_ascii=False), filename, row))
                if kind == 'contract':
                    contracts[record[key]] = record
                manifest[filename]['rows'] += 1
        participants = {row[0] for row in con.execute("SELECT id FROM entities WHERE kind='participant'")}
        brokers = {row[0] for row in con.execute("SELECT id FROM entities WHERE kind='broker'")}
        for _, record in records(source / 'usd_rates.csv'):
            day = date(record['Дата'])
            if day in rates:
                raise ValueError('Duplicate exchange rate')
            rates[day] = decimal(record['Курс'])
            if rates[day] <= 0:
                raise ValueError('Invalid exchange rate')
            manifest['usd_rates.csv']['rows'] += 1
        for path in files[4:]:
            for row, record in records(path):
                c = contracts[record['Номер контракта']]
                day = date(record['Дата сделки'])
                currency = {'Узбекский сум': 'UZS', 'Доллар США': 'USD'}[c['Валюта']]
                volume = decimal(record['Количество']) * decimal(record['Лот'])
                amount = decimal(record['Сумма сделки'])
                if volume <= 0 or amount < 0:
                    raise ValueError(f'Invalid quantity/amount: {path.name}:{row}')
                if record['Единица измерения'] != c['Единица измерения'] or decimal(record['Лот']) != decimal(c['Лот']):
                    raise ValueError(f'Contract unit mismatch: {path.name}:{row}')
                for key in ('ИНН продавца', 'ИНН покупателя'):
                    if record[key] not in participants:
                        raise ValueError(f'Unknown participant: {path.name}:{row}')
                for key in ('Брокер продавца', 'Брокер покупателя'):
                    if record[key] not in brokers:
                        raise ValueError(f'Unknown broker: {path.name}:{row}')
                uzs = amount * rates[day] if currency == 'USD' else amount
                values = (record['Номер сделки'], day, record['Наименование товара'], c['Категория товара'],
                          record['Регион'], record['Состояние сделки'], record['Форма контракта'],
                          record['Тип контракта'], record['ИНН продавца'], record['ИНН покупателя'],
                          record['Брокер продавца'], record['Брокер покупателя'], record['Номер контракта'],
                          currency, record['Единица измерения'], str(volume), str(amount), str(uzs),
                          path.name, row, json.dumps(record, ensure_ascii=False))
                con.execute('INSERT INTO deals VALUES(' + ','.join('?' for _ in values) + ')', values)
                manifest[path.name]['rows'] += 1
            print(f"Imported {path.name}: {manifest[path.name]['rows']}", flush=True)
        con.executescript('''
        CREATE INDEX deals_date ON deals(date);
        CREATE INDEX deals_seller ON deals(seller);
        CREATE INDEX deals_buyer ON deals(buyer);
        CREATE INDEX deals_contract ON deals(contract);
        ''')
        start, end, count = con.execute('SELECT min(date),max(date),count(*) FROM deals').fetchone()
        meta = {'start': start, 'end': end, 'count': count, 'synthetic': True, 'manifest': manifest}
        con.execute('INSERT INTO metadata VALUES(?,?)', ('snapshot', json.dumps(meta, ensure_ascii=False)))
        con.commit()
        con.close()
        temporary.replace(target)
        return meta
    except Exception:
        con.close()
        temporary.unlink(missing_ok=True)
        raise


class DecimalSum:
    def __init__(self):
        self.total = Decimal(0)

    def step(self, value):
        self.total += decimal(value)

    def finalize(self):
        return str(self.total)


def connect(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.create_function('fold', 1, lambda s: s.casefold())
    con.create_function('product_key', 1, product, deterministic=True)
    con.create_function('region_key', 1, region, deterministic=True)
    con.create_aggregate('decimal_sum', 1, DecimalSum)
    con.execute('PRAGMA query_only=ON')
    return con
