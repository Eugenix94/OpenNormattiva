#!/usr/bin/env python3
import requests

RUNS_URL = (
    "https://api.github.com/repos/Eugenix94/OpenNormattiva/"
    "actions/workflows/nightly-update.yml/runs?per_page=10"
)
HF_DS_URL = "https://huggingface.co/api/datasets/diatribe00/normattiva-data"


def safe_get(url: str, timeout: int = 10):
    """Best-effort GET that never raises to caller."""
    try:
        return requests.get(url, timeout=timeout)
    except KeyboardInterrupt:
        print(f"Request interrupted: {url}")
        return None
    except Exception as e:
        print(f"Request error for {url}: {e}")
        return None


print("=== GITHUB ACTIONS WORKFLOW RUNS ===\n")
r = safe_get(RUNS_URL, timeout=10)
if r and r.status_code == 200:
    data = r.json()
    runs = data.get("workflow_runs", [])

    for i, run in enumerate(runs[:3]):
        print(f"\n[{i}] {run.get('created_at')}")
        print(f"    Status: {run.get('status')}")
        print(f"    Conclusion: {run.get('conclusion')}")
        print(f"    Logs URL: {run.get('logs_url')}")
        print(f"    Run ID: {run.get('id')}")

        # Best-effort fetch logs (public endpoint can be slow/unavailable)
        logs_url = run.get("logs_url")
        if logs_url:
            logs_r = safe_get(logs_url, timeout=10)
            if logs_r and logs_r.status_code == 200:
                logs_text = logs_r.text
                lines = logs_text.split("\n")
                print("    Last 30 lines of logs:")
                for line in lines[-30:]:
                    l = line.lower()
                    if line.strip() and (
                        "error" in l
                        or "failed" in l
                        or "upload" in l
                        or "complete" in l
                    ):
                        print(f"      > {line[:120]}")
else:
    code = r.status_code if r else "N/A"
    print(f"Failed to fetch workflow runs (status={code})")

print("\n\n=== HF DATASET FILES ===")
r = safe_get(HF_DS_URL, timeout=8)
if r and r.status_code == 200:
    ds = r.json()
    files = ds.get("siblings", [])
    for f in sorted(files, key=lambda x: x.get("rfilename", "")):
        fname = f.get("rfilename", "?")
        fsize = f.get("size", 0)
        if fsize > 1e9:
            print(f"  {fname}: {fsize/1e9:.2f} GB")
        elif fsize > 1e6:
            print(f"  {fname}: {fsize/1e6:.2f} MB")
        else:
            print(f"  {fname}: {fsize} bytes")
else:
    code = r.status_code if r else "N/A"
    print(f"HF dataset metadata unavailable (status={code})")
