import os
from huggingface_hub import HfApi

api = HfApi(token=os.getenv('HF_TOKEN'))

print('=== PRODUCTION DATASET ===')
ds_info = api.dataset_info('diatribe00/normattiva-data')
commits = list(api.list_repo_commits(repo_id='diatribe00/normattiva-data', repo_type='dataset'))[:5]
for c in commits:
    cid = c.commit_id[:7]
    print(f'{cid}: {c.title}')

files = {s.rfilename for s in ds_info.siblings}
print()
print('Files:')
print('  laws.db:', 'data/laws.db' in files)
print('  laws_vigente.jsonl:', 'data/processed/laws_vigente.jsonl' in files)
print('  laws_multivigente.jsonl:', 'data/processed/laws_multivigente.jsonl' in files)

print()
print('=== LAB SPACE & DATASET ===')
sp_info = api.space_info('diatribe00/normattiva-lab')
runtime_status = sp_info.runtime.stage if sp_info.runtime else 'unknown'
print(f'Lab space status: {runtime_status}')

ds_info = api.dataset_info('diatribe00/normattiva-lab-data')
files = {s.rfilename for s in ds_info.siblings}
print('Lab files:')
print('  laws.db:', 'data/laws.db' in files)
print('  laws_vigente.jsonl:', 'data/processed/laws_vigente.jsonl' in files)
print('  laws_multivigente.jsonl:', 'data/processed/laws_multivigente.jsonl' in files)
