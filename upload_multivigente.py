import os
from pathlib import Path
from huggingface_hub import HfApi

HF_TOKEN = os.environ.get('HF_TOKEN')
api = HfApi(token=HF_TOKEN)

MV = Path('data') / 'multivigente.db'
if not MV.exists():
    print(f'Multivigente DB not found: {MV.resolve()}')
    raise SystemExit(1)
size_mb = MV.stat().st_size / 1e6
print(f'Found multivigente.db: {size_mb:.1f} MB')

repo_id = 'diatribe00/normattiva-lab-data'
print(f'Uploading to {repo_id}/data/multivigente.db ...')
url = api.upload_file(
    path_or_fileobj=str(MV.resolve()),
    path_in_repo='data/multivigente.db',
    repo_id=repo_id,
    repo_type='dataset',
    commit_message=f'Add multivigente.db ({size_mb:.0f} MB) — VOOM multivigente upload',
)
print('Uploaded:', url)
