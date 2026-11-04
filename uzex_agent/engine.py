import json
from datetime import date
from decimal import Decimal

from .data import connect, decimal
from .normalization import clean, notes


METRICS = ['count', 'turnover', 'volume', 'price']
GROUPS = ['none', 'year', 'month', 'region', 'category', 'product', 'status', 'seller', 'buyer', 'seller_broker', 'buyer_broker']
FILTERS = ['date_from', 'date_to', 'product', 'region', 'category', 'status', 'form', 'contract_type', 'seller', 'buyer', 'contract', 'seller_broker', 'buyer_broker']
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'action': {'type': 'string', 'enum': ['analytics', 'compare', 'deal', 'contract', 'participant', 'broker', 'unsupported']},
        'metric': {'type': 'string', 'enum': METRICS},
        'group_by': {'type': 'string', 'enum': GROUPS},
        'filters': {'type': 'object', 'additionalProperties': False,
                    'properties': {key: {'type': 'string'} for key in FILTERS}},
        'cancelled': {'type': 'string', 'enum': ['exclude', 'include', 'only']},
        'id': {'type': 'string'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
        'periods': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'date_from': {'type': 'string'}, 'date_to': {'type': 'string'}},
            'required': ['date_from', 'date_to']}},
    }, 'required': ['action'],
}


class Unsupported(ValueError):
    pass


def validate(plan):
    if not isinstance(plan, dict) or set(plan) - set(SCHEMA['properties']):
        raise Unsupported('Неподдерживаемый формат запроса.')
    action = plan.get('action')
    if action not in SCHEMA['properties']['action']['enum'] or action == 'unsupported':
        raise Unsupported('На этот вопрос нельзя ответить доступными операциями по CSV. Прогнозы, объяснения причин и внешние сведения недоступны.')
    if action == 'compare':
        if set(plan) - {'action', 'metric', 'filters', 'cancelled', 'periods'}:
            raise Unsupported('Сравнение поддерживает показатель, общие фильтры и два периода.')
        periods = plan.get('periods')
        if not isinstance(periods, list) or len(periods) != 2:
            raise Unsupported('Укажите два периода для сравнения.')
        shared = plan.get('filters', {})
        if not isinstance(shared, dict) or 'date_from' in shared or 'date_to' in shared:
            raise Unsupported('Даты сравнения указываются только в periods.')
        for period in periods:
            if not isinstance(period, dict) or set(period) != {'date_from', 'date_to'}:
                raise Unsupported('Для каждого периода нужны date_from и date_to.')
            validate({'action': 'analytics', 'metric': plan.get('metric', 'count'), 'cancelled': plan.get('cancelled', 'exclude'), 'filters': {**shared, **period}})
        return {'metric': 'count', 'cancelled': 'exclude', 'filters': {}, **plan}
    if action != 'analytics':
        if set(plan) - {'action', 'id'} or not isinstance(plan.get('id'), str) or not 1 <= len(plan['id']) <= 30:
            raise Unsupported('Укажите только тип карточки и её точный идентификатор.')
        return dict(plan)
    if 'id' in plan or 'periods' in plan:
        raise Unsupported('Для аналитики используйте фильтры.')
    p = {'metric': 'count', 'group_by': 'none', 'filters': {}, 'cancelled': 'exclude', 'limit': 10, **plan}
    if p['metric'] not in METRICS or p['group_by'] not in GROUPS or p['cancelled'] not in ['exclude', 'include', 'only']:
        raise Unsupported('Неизвестная операция.')
    if type(p['limit']) is not int or not 1 <= p['limit'] <= 20:
        raise Unsupported('Лимит должен быть от 1 до 20.')
    f = p['filters']
    if not isinstance(f, dict) or set(f) - set(FILTERS):
        raise Unsupported('Неизвестный фильтр.')
    for key, value in f.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise Unsupported('Некорректное значение фильтра.')
        if key == 'region' and clean(value) in ['ташкент', 'toshkent', 'tashkent', 'тошкент']:
            raise Unsupported('Уточните: г. Ташкент или Ташкентская область (Toshkent shahri / Toshkent viloyati).')
        if key.startswith('date_'):
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise Unsupported('Дата должна иметь формат YYYY-MM-DD.') from None
    if f.get('date_from', '') > f.get('date_to', '9999-12-31'):
        raise Unsupported('Начало периода позже конца.')
    if 'status' in f:
        is_cancelled = f['status'] == 'Аннулирован(Не оплачен)'
        if (is_cancelled and p['cancelled'] == 'exclude') or (not is_cancelled and p['cancelled'] == 'only'):
            raise Unsupported('Фильтры состояния сделки противоречат друг другу.')
    return p


def execute(path, plan):
    p = validate(plan)
    if p['action'] == 'compare':
        return compare(path, p)
    con = connect(path)
    try:
        meta = json.loads(con.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        base = {'plan': p, 'snapshot': {k: v for k, v in meta.items() if k != 'manifest'}}
        if p['action'] != 'analytics':
            if p['action'] == 'deal':
                sql, args = 'SELECT * FROM deals WHERE id=?', [p['id']]
            else:
                sql, args = 'SELECT payload,file,row FROM entities WHERE kind=? AND id=?', [p['action'], p['id']]
            row = con.execute(sql, args).fetchone()
            record = json.loads(row['payload']) if row else None
            sources = [{'file': row['file'], 'row': row['row'], **meta['manifest'][row['file']]}] if row else []
            if row and p['action'] == 'deal':
                record['Валюта суммы и стартовой цены (из контракта)'] = row['currency']
                record['Объём (количество × лот)'] = row['volume'] + ' ' + row['unit']
                c = con.execute("SELECT file,row FROM entities WHERE kind='contract' AND id=?", (row['contract'],)).fetchone()
                sources.append({'file': c['file'], 'row': c['row'], **meta['manifest'][c['file']]})
            return {**base, 'record': record, 'sources': sources}
        f = p['filters']
        if f.get('date_from', meta['start']) < meta['start'] or f.get('date_to', meta['end']) > meta['end'] or f.get('date_from', meta['start']) > meta['end'] or f.get('date_to', meta['end']) < meta['start']:
            raise Unsupported(f"Запрошенный период выходит за покрытие выгрузки: {meta['start']} — {meta['end']}. Укажите период внутри него.")
        clauses, args = [], []
        for key, value in f.items():
            if key == 'date_from':
                clauses.append('date >= ?')
            elif key == 'date_to':
                clauses.append('date <= ?')
            elif key == 'product':
                clauses.append("instr(product_key(product),product_key(?)) > 0")
            elif key == 'region':
                clauses.append('region_key(region) = region_key(?)')
            else:
                clauses.append(f'fold({key}) = fold(?)')
            args.append(value)
        if p['cancelled'] != 'include':
            clauses.append('status ' + ('!=' if p['cancelled'] == 'exclude' else '=') + ' ?')
            args.append('Аннулирован(Не оплачен)')
        where = ' AND '.join(clauses) or '1=1'
        group = p['group_by']
        columns = [] if group == 'none' else [{'year': 'substr(date,1,4)', 'month': 'substr(date,1,7)', 'region': 'region_key(region)', 'product': 'product_key(product)'}.get(group, group)]
        if p['metric'] in ['volume', 'price']:
            columns.append('unit')
        if p['metric'] == 'price':
            columns.append('currency')
        selects = [f'{col} AS g{i}' for i, col in enumerate(columns)]
        selects += ['count(*) AS n', 'decimal_sum(amount_uzs) AS turnover', 'decimal_sum(volume) AS volume', 'decimal_sum(amount) AS amount']
        sql = 'SELECT ' + ','.join(selects) + ' FROM deals WHERE ' + where
        if columns:
            sql += ' GROUP BY ' + ','.join(columns)
        rows = []
        for row in con.execute(sql, args):
            if not row['n']:
                continue
            metric = p['metric']
            value = Decimal(row['n']) if metric == 'count' else decimal(row[metric]) if metric != 'price' else decimal(row['amount']) / decimal(row['volume'])
            rows.append({'group': [row[f'g{i}'] for i in range(len(columns))], 'value': str(value), 'deals': row['n']})
        # Different units/currencies are never ranked against each other.
        if p['metric'] in ['price', 'volume']:
            rows.sort(key=lambda r: tuple(r['group']))
        elif group in ['year', 'month']:
            rows.sort(key=lambda r: r['group'])
        else:
            rows.sort(key=lambda r: (-decimal(r['value']), tuple(r['group'])))
        count = con.execute('SELECT count(*) FROM deals WHERE ' + where, args).fetchone()[0]
        files = [r[0] for r in con.execute('SELECT DISTINCT file FROM deals WHERE ' + where, args)]
        if files:
            files += ['contracts.csv']
            if p['metric'] == 'turnover':
                files += ['usd_rates.csv']
        sample = [dict(r) for r in con.execute('SELECT id,file,row FROM deals WHERE ' + where + ' ORDER BY date,id LIMIT 3', args)]
        if group in ['seller', 'buyer', 'seller_broker', 'buyer_broker'] and rows:
            kind = 'broker' if group.endswith('_broker') else 'participant'
            names = {r['id']: json.loads(r['payload'])['Наименование'] for r in con.execute('SELECT id,payload FROM entities WHERE kind=?', (kind,))}
            for row in rows:
                row['name'] = names.get(row['group'][0], '')
            files.append('brokers.csv' if kind == 'broker' else 'participants.csv')
        return {**base, 'rows': rows[:p['limit']], 'matched': count, 'groups': len(rows), 'sample': sample,
                'sql': sql, 'parameters': args,
                'sources': [{'file': name, **meta['manifest'][name]} for name in sorted(files)]}
    finally:
        con.close()


def number(value):
    return f'{decimal(value):,.2f}'.replace(',', ' ').removesuffix('.00')


def compare(path, p):
    results = [execute(path, {'action': 'analytics', 'metric': p['metric'], 'cancelled': p['cancelled'],
                             'limit': 20, 'filters': {**p['filters'], **period}}) for period in p['periods']]
    maps = [{tuple(row['group']): row['value'] for row in r['rows']} for r in results]
    keys = sorted(set(maps[0]) | set(maps[1])) or ([()] if p['metric'] in ['count', 'turnover'] else [])
    rows = []
    for key in keys:
        values = [m.get(key, '0' if p['metric'] in ['count', 'turnover'] else None) for m in maps]
        delta = str(decimal(values[1]) - decimal(values[0])) if all(v is not None for v in values) else None
        percent = str(decimal(delta) / decimal(values[0]) * 100) if delta is not None and decimal(values[0]) != 0 else None
        rows.append({'group': list(key), 'before': values[0], 'after': values[1], 'delta': delta, 'percent': percent})
    days = [(date.fromisoformat(period['date_to']) - date.fromisoformat(period['date_from'])).days + 1 for period in p['periods']]
    sources = {s['file']: s for r in results for s in r['sources']}
    return {'plan': p, 'snapshot': results[0]['snapshot'], 'comparison': rows, 'period_results': results,
            'period_days': days, 'sources': list(sources.values())}


def describe(plan):
    p = validate(plan)
    if p['action'] == 'compare':
        base = describe({'action': 'analytics', 'metric': p['metric'], 'cancelled': p['cancelled'], 'filters': p['filters']})
        return 'Сравнение двух периодов\n' + base + '\n' + ' → '.join(x['date_from'] + ' — ' + x['date_to'] for x in p['periods'])
    if p['action'] != 'analytics':
        kind = {'deal': 'Сделка', 'contract': 'Контракт', 'participant': 'Участник', 'broker': 'Брокер'}[p['action']]
        return f"{kind}: {p['id']}"
    metric = {'count': 'Число сделок', 'turnover': 'Сумма сделок в UZS', 'volume': 'Объём по единицам', 'price': 'Средневзвешенная цена по единицам и валютам'}[p['metric']]
    cancel = {'exclude': 'исключены', 'include': 'включены', 'only': 'только аннулированные'}[p['cancelled']]
    group = {'none': 'общий итог', 'year': 'по годам', 'month': 'по месяцам', 'region': 'по регионам',
             'category': 'по категориям', 'product': 'по товарам', 'status': 'по состояниям',
             'seller': 'по продавцам', 'buyer': 'по покупателям', 'seller_broker': 'по брокерам продавца',
             'buyer_broker': 'по брокерам покупателя'}[p['group_by']]
    labels = dict(zip(FILTERS, ['с даты', 'по дату', 'товар содержит', 'регион', 'категория', 'состояние',
                              'форма контракта', 'тип контракта', 'ИНН продавца', 'ИНН покупателя',
                              'контракт', 'брокер продавца', 'брокер покупателя']))
    filters = '; '.join(f'{labels[k]}: {v}' for k, v in p['filters'].items()) or 'весь период выгрузки'
    return f"{metric}; группировка: {group}; аннулированные: {cancel}.\nФильтры: {filters}."


def render(result):
    lines = [describe(result['plan'])]
    if 'comparison' in result:
        for row in result['comparison']:
            show = lambda v: 'нет данных' if v is None else number(v)
            percent = 'не определено (нулевая база или нет данных)' if row['percent'] is None else show(row['percent']) + '%'
            lines.append(f"{' / '.join(row['group']) or 'Итого'}: {show(row['before'])} → {show(row['after'])}; разница: {show(row['delta'])}; изменение: {percent}")
        lines.append(f"Длительность периодов: {result['period_days'][0]} и {result['period_days'][1]} дней; суммы не приведены к одинаковому числу дней.")
        lines.append('Цена — средневзвешенная, отдельно по валютам и единицам. Изменение состава товаров может влиять на сравнение; уточняйте товар и атрибуты.')
    elif 'record' in result:
        lines += [f'{k}: {v or "—"}' for k, v in result['record'].items()] if result['record'] else ['Запись не найдена.']
    else:
        if not result['rows']:
            lines.append('По указанным фильтрам сделок не найдено.')
        for row in result['rows']:
            label = ' / '.join(row['group']) or 'Итого'
            if row.get('name'):
                label = row['name'] + ' (' + label + ')'
            suffix = ' UZS' if result['plan']['metric'] == 'turnover' else ''
            lines.append(f"{label}: {number(row['value'])}{suffix} (сделок: {row['deals']})")
        lines.append(f"Найдено сделок: {result['matched']}; показано групп: {len(result['rows'])} из {result['groups']}.")
        if result['plan']['metric'] == 'price':
            lines.append('Цена = сумма сделок / сумма (количество × лот). Разные товары могут входить в одну группу; уточните товар и атрибуты.')
        if result['plan']['metric'] == 'turnover':
            lines.append('USD пересчитаны по курсу на дату сделки. Это сумма зарегистрированных сделок, не поступившая оплата.')
        if result['plan']['metric'] == 'volume':
            lines.append('Объём = количество × лот; это договорный, не фактически поставленный объём.')
        if result['plan']['metric'] in ['price', 'volume']:
            lines.append('Группы отсортированы по названию; разные единицы и валюты не сравниваются.')
        if result['sample']:
            lines.append('Примеры записей: ' + '; '.join(f"{s['file']}, строка {s['row']}, сделка {s['id']}" for s in result['sample']))
    lines.append('Источники: ' + (', '.join(s['file'] + (f", строка {s['row']}" if 'row' in s else '') for s in result['sources']) or 'совпадений нет'))
    lines.append(notes())
    m = result['snapshot']
    lines.append(f"Вымышленные данные. Покрытие: {m['start']} — {m['end']}. Состояния на {m['end']}; не текущие торги.")
    return '\n'.join(lines)
