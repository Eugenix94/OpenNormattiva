import urllib.request, json
from pathlib import Path

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
HEADERS = {"User-Agent": "NormattivaVOOM/1.0 (inspect)", "Accept": "*/*"}
url = f"{BASE_API}/api/v1/collections/collection-predefinite"
req = urllib.request.Request(url, headers=HEADERS)
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.load(r)

print(f"Total items: {len(data)}\n")
for i, item in enumerate(data[:12]):
    print('ITEM', i)
    print('keys:', list(item.keys()))
    # print a few key values
    for k in ['nome', 'formatoCollezione', 'titolo', 'id', 'nomeCollezione']:
        if k in item:
            print(f"  {k}: {str(item[k])[:120]}")
    print('\n')
print('Done')
