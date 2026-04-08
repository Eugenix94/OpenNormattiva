"""Trigger first GitHub Actions workflow run."""
import requests
import subprocess

def get_github_token():
    proc = subprocess.Popen(
        ['git', 'credential', 'fill'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stdout, _ = proc.communicate(input="protocol=https\nhost=github.com\n\n")
    for line in stdout.strip().split("\n"):
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return None

token = get_github_token()
headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

# List workflows
resp = requests.get("https://api.github.com/repos/Eugenix94/OpenNormattiva/actions/workflows", headers=headers)
workflows = resp.json()

for wf in workflows.get("workflows", []):
    name = wf["name"]
    wf_id = wf["id"]
    state = wf["state"]
    print(f"Workflow: {name} (ID: {wf_id}, state: {state})")

# Trigger the Nightly workflow
for wf in workflows.get("workflows", []):
    if "Nightly" in wf["name"]:
        print(f"\nTriggering: {wf['name']}...")
        resp = requests.post(
            f"https://api.github.com/repos/Eugenix94/OpenNormattiva/actions/workflows/{wf['id']}/dispatches",
            headers=headers,
            json={"ref": "master"}
        )
        if resp.status_code == 204:
            print("Workflow triggered successfully!")
            print("Check progress: https://github.com/Eugenix94/OpenNormattiva/actions")
        else:
            print(f"Trigger response: {resp.status_code} {resp.text[:300]}")
        break
