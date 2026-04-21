# Deployment Guide

The Space at https://huggingface.co/spaces/diatribe00/normattiva-search currently shows "no data" because:

1. **The updated app.py hasn't been deployed yet** - The new app with the notifications and update log features hasn't been pushed to the Space
2. **The pre-built database (774 MB) needs to be included** - It ships inside the Docker image, not downloaded at runtime

## Quick Deploy (3 steps)

### 1. Set Your HF Token

First, you need a HuggingFace write token. Get one from: https://huggingface.co/settings/tokens

Then set it as an environment variable:

**Windows PowerShell:**
```powershell
$env:HF_TOKEN = "hf_xxxxxxxxxxxxx"
```

**Windows Command Prompt:**
```cmd
set HF_TOKEN=hf_xxxxxxxxxxxxx
```

**Linux/Mac:**
```bash
export HF_TOKEN=hf_xxxxxxxxxxxxx
```

### 2. Deploy

```bash
python deploy_now.py --token hf_xxxxxxxxxxxxx
```

Or if you set `HF_TOKEN` env var:

```bash
python deploy_now.py
```

The deployment will:
- Copy the 774 MB pre-built database into the Docker image
- Deploy the updated app.py with Notifications + Update Log pages
- Upload the dataset files to HuggingFace
- Restart the Space

**This will take 3-5 minutes.**

### 3. Wait for the Space to Restart

The Space should restart automatically after deployment. Refresh the page at:  
https://huggingface.co/spaces/diatribe00/normattiva-search

You should see **157,121 laws** loading on the Dashboard.

---

## What Changed

### New Features
- **Notifications page** - Polls the Normattiva API for new laws (read-only)
- **Update Log page** - Tracks when you manually update the dataset
- **Better error messages** - Shows exactly where it's looking for the DB
- **Static architecture** - DB ships with the Space, no automatic pipeline

### Files Modified
- `space/app.py` - Complete rewrite with new pages and better path handling
- `deploy_hf.py` - Updated to ship the DB inside the Docker image
- `.github/workflows/check-changes.yml` - New notification-only workflow (no data changes)

### Database Tables
The `update_log` table tracks:
- When updates happened
- What action was taken (initial_load, full_rebuild, etc.)
- How many laws before/after
- Which collections were affected
- Any user notes

---

## Troubleshooting

If the Space still shows "no data" after deployment:

1. **Check the Space logs**: Go to https://huggingface.co/spaces/diatribe00/normattiva-search/logs
   
2. **Verify the DB was copied**: The logs should show:
   ```
   Including DB: 774.0 MB
   ```
   
3. **If it wasn't copied**: The `data/laws.db` file (774 MB) needs to be present locally before deployment
   ```bash
   ls -lh data/laws.db
   ```
   
4. **Redeploy**: Make sure to set HF_TOKEN and run `deploy_now.py` again

---

## Manual Deployment

If `deploy_now.py` doesn't work, you can run the deployment script directly:

```bash
python deploy_hf.py --token hf_xxxxxxxxxxxxx
```

This will show more detailed output about what's being deployed.
