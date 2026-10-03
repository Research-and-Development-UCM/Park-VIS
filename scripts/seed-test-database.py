"""Install included test data without replacing an existing database."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description='Seed Park-VIS test data while preserving existing runtime data.')
parser.add_argument('--data-dir', type=Path, default=ROOT / '.local-data')
args = parser.parse_args()
destination = args.data_dir.resolve()
database = destination / 'parkinglot.db'
if database.exists():
    parser.error('A database already exists there. Choose an empty data directory.')
source = ROOT / 'fixtures/parkinglot.db'
if not source.exists():
    parser.error('Download repository files with git lfs pull first.')
with source.open('rb') as file:
    if file.read(16) != b'SQLite format 3\x00':
        parser.error('The database is a Git LFS pointer. Run git lfs pull first.')
destination.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, database)
print('Test data installed. Included accounts keep their existing passwords.')
