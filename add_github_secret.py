#!/usr/bin/env python3
"""Add HF_TOKEN secret to GitHub repo via API."""
import base64
import json
import os
import sys
import urllib.request

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
REPO = "Eugenix94/OpenNormattiva"

if not GITHUB_TOKEN:
    print("No GITHUB_TOKEN — set it in environment to add the secret automatically.")
    print("OR go to: https://github.com/Eugenix94/OpenNormattiva/settings/secrets/actions/new")
    print("  Name: HF_TOKEN")
    print(f"  Value: {HF_TOKEN[:8]}... (use your full HF token)")
    sys.exit(0)  # Not an error — just manual instructions

# Fetch the repo's public key for encrypting the secret
req = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/actions/secrets/public-key",
    headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    },
)
with urllib.request.urlopen(req) as resp:
    pub_key_data = json.loads(resp.read())

key_id = pub_key_data["key_id"]
public_key = pub_key_data["key"]

# Encrypt the secret using libsodium (requires PyNaCl)
try:
    from nacl import encoding, public

    pk = public.PublicKey(public_key.encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk)
    encrypted = sealed.encrypt(HF_TOKEN.encode())
    encrypted_b64 = base64.b64encode(encrypted).decode()
except ImportError:
    print("PyNaCl not installed. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyNaCl", "-q"])
    from nacl import encoding, public
    pk = public.PublicKey(public_key.encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk)
    encrypted = sealed.encrypt(HF_TOKEN.encode())
    encrypted_b64 = base64.b64encode(encrypted).decode()

# PUT the secret
body = json.dumps({"encrypted_value": encrypted_b64, "key_id": key_id}).encode()
req = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/actions/secrets/HF_TOKEN",
    data=body,
    method="PUT",
    headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(req) as resp:
    status = resp.status
    print(f"Secret set: HTTP {status} (201=created, 204=updated)")
