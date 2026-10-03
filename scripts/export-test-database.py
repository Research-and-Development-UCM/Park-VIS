"""Export test data, keeping accounts but excluding API and service secrets."""
from pathlib import Path
import json
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures'
SAFE_TABLES = {'users', 'cameras', 'spaces', 'camera_groups', 'camera_group_memberships',
               'camera_scans', 'occupancies', 'occupancy_hourly', 'occupancy_events'}
SAFE_SETTINGS = {'inference_interval', 'confidence_threshold', 'retention_images_hours',
                 'retention_events_days', 'retention_raw_data_days', 'retention_hourly_data_days',
                 'inference_device', 'max_inference_resolution', 'hysteresis_occupied_threshold',
                 'hysteresis_free_threshold', 'optimization_interval_days',
                 'max_snapshot_resolution', 'quality_snapshots', 'quality_crops'}

def export():
    FIXTURES.mkdir(exist_ok=True)
    live_path = ROOT / '.local-data/parkinglot.db'
    output = FIXTURES / 'parkinglot.db'
    temporary = FIXTURES / 'parkinglot-export.tmp'
    if temporary.exists():
        raise RuntimeError('An unfinished export exists; inspect it before retrying.')
    original = sqlite3.connect(live_path.as_uri() + '?mode=ro', uri=True)
    snapshot = sqlite3.connect(':memory:')
    original.backup(snapshot)
    original.close()
    snapshot.row_factory = sqlite3.Row
    copied = sqlite3.connect(temporary)
    copied.execute('PRAGMA foreign_keys=OFF')
    schema = snapshot.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END").fetchall()
    for definition in schema:
        copied.execute(definition['sql'])
    for definition in schema:
        table = definition['name']
        if definition['type'] != 'table' or table not in SAFE_TABLES | {'settings'}:
            continue
        for source_row in snapshot.execute(f'SELECT * FROM "{table}"'):
            row = dict(source_row)
            if table == 'settings':
                if row['key'] not in SAFE_SETTINGS and not row['key'].startswith('parking_display_layout_v1'):
                    continue
                if row['key'].startswith('parking_display_layout_v1'):
                    row['value'] = json.dumps(json.loads(row['value']))
            if table == 'cameras':
                local = Path(row['local_path']) if row.get('local_path') else None
                if local and not local.is_absolute():
                    local = ROOT / local
                row.update(snapshot_url=None, stream_url=None, stream_user=None, stream_password=None, is_enabled=False, local_path=None)
                if local and local.is_file() and local.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
                    relative = Path('fixtures/media') / f"camera-{row['id']}{local.suffix.lower()}"
                    (ROOT / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local, ROOT / relative)
                    row.update(source_type='test', local_path=relative.as_posix(), is_enabled=True)
            if table == 'camera_scans':
                row['has_image'] = False
            if table == 'occupancy_events':
                row['has_crop'] = False
            columns = ','.join('"' + column + '"' for column in row)
            placeholders = ','.join('?' for _ in row)
            copied.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', tuple(row.values()))
    copied.commit()
    assert copied.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not copied.execute('PRAGMA foreign_key_check').fetchall()
    assert copied.execute('SELECT COUNT(*) FROM api_keys').fetchone()[0] == 0
    assert copied.execute('SELECT COUNT(*) FROM alert_channels').fetchone()[0] == 0
    assert copied.execute('SELECT COUNT(*) FROM cameras WHERE stream_user IS NOT NULL OR stream_password IS NOT NULL OR stream_url IS NOT NULL OR snapshot_url IS NOT NULL').fetchone()[0] == 0
    settings = {row[0] for row in copied.execute('SELECT key FROM settings')}
    assert all(key in SAFE_SETTINGS or key.startswith('parking_display_layout_v1') for key in settings)
    for table in SAFE_TABLES:
        assert copied.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == snapshot.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    assert [tuple(r) for r in copied.execute('SELECT id,username,password_hash,is_admin,permissions FROM users ORDER BY id')] == [tuple(r) for r in snapshot.execute('SELECT id,username,password_hash,is_admin,permissions FROM users ORDER BY id')]
    copied.close()
    snapshot.close()
    temporary.replace(output)
    print('Test database exported with accounts, lots, spaces, layouts, and history. API and service secrets excluded.')

if __name__ == '__main__':
    export()
