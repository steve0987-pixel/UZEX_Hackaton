"""Export computed public demo aggregates; never exports secrets or raw company records."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uzex_agent.engine import execute
from uzex_agent.interface import Interface, REPORTS


def export():
    ui = Interface(Mock(), 'runtime/uzex.sqlite3')
    snapshot = ui.snapshot
    years = [str(y) for y in range(int(snapshot['start'][:4]), int(snapshot['end'][:4]) + 1)]
    data = {'snapshot': snapshot, 'years': years, 'reports': {}}
    for year in years:
        data['reports'][year] = {}
        for report in REPORTS:
            result = execute(ui.db, ui.report_plan(report, year))
            data['reports'][year][report] = {key: result[key] for key in ['plan', 'rows', 'matched', 'groups', 'sources']}
        print('Exported', year, flush=True)
    target = Path('miniapp/dist/data.json')
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print('Exported verified aggregates:', target)


if __name__ == '__main__':
    export()
