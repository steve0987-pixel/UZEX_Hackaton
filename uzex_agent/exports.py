"""Dependency-free CSV/XLSX reports, with provenance and formula-injection protection."""
import csv
import io
import json
import re
import zipfile
from xml.sax.saxutils import escape


def table(result):
    if 'comparison' in result:
        rows = [['Группа', 'Период A', 'Период B', 'Разница B−A', 'Изменение, %']]
        rows += [[' / '.join(r['group']) or 'Итого', *[r[k] if r[k] is not None else 'Не определено' for k in ['before', 'after', 'delta', 'percent']]] for r in result['comparison']]
    elif 'record' in result:
        rows = [['Поле', 'Значение'], *list((result['record'] or {}).items())]
    else:
        rows = [['Группа', 'Наименование', 'Значение', 'Число сделок']]
        rows += [[' / '.join(r['group']) or 'Итого', r.get('name', ''), r['value'], r['deals']] for r in result['rows']]
    return rows


def metadata(result):
    return [['Параметр', 'Значение'], ['План и фильтры', json.dumps(result['plan'], ensure_ascii=False)],
            ['Покрытие', result['snapshot']['start'] + ' — ' + result['snapshot']['end']],
            ['Тип данных', 'Вымышленные, учебные'],
            ['Методика', 'Объем = количество × лот; цена = сумма / объем, отдельно по валютам и единицам; USD пересчитывается в UZS на дату сделки.'],
            ['Ограничение', 'Экспорт содержит показанные группы; полный результат может содержать больше групп.'],
            ['Всего групп', result.get('groups', len(result.get('comparison', [])))],
            *[['Источник', json.dumps(s, ensure_ascii=False)] for s in result['sources']]]


def safe_csv(value):
    value = str(value)
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        if not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
            return "'" + value
    return value


def export_csv(result):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerows([[safe_csv(cell) for cell in row] for row in table(result) + [[]] + metadata(result)])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def export_xlsx(result):
    def sheet(rows):
        chunks = ['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        for n, row in enumerate(rows, 1):
            chunks.append(f'<row r="{n}">')
            for cell in row:
                # All values are text: exact monetary precision and INN leading zeros survive Excel.
                value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(cell))[:32767]
                chunks.append('<c t="inlineStr"><is><t xml:space="preserve">' + escape(value) + '</t></is></c>')
            chunks.append('</row>')
        return ''.join(chunks) + '</sheetData></worksheet>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Отчет" sheetId="1" r:id="rId1"/><sheet name="Источники" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', sheet(table(result)))
        z.writestr('xl/worksheets/sheet2.xml', sheet(metadata(result)))
    return output.getvalue()
