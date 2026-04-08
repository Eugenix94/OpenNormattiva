# DEPLOYMENT GUIDE - Normattiva Research Platform

**Status**: ✅ Production Code Ready  
**Action**: Execute deployment script with your credentials  
**Estimated Time**: 5-10 minutes to live

---

## What You Need (2 Items)

### 1. GitHub Repository URL
**Where to get it:**
- Go to https://github.com/new
- Repository name: `normattiva-research` (or your choice)
- Public repo (checked)
- NO README, NO LICENSE, NO .gitignore (we have them)
- Click "Create repository"
- Copy the URL: `https://github.com/YOUR_USER/normattiva-research`

### 2. HuggingFace Write Token
**Where to get it:**
- Go to https://huggingface.co/settings/tokens
- Click "New token"
- Name: "Normattiva Deployment"
- Type: "Write"
- Click "Generate"
- Copy the token: `hf_xxxxxxxxxxxxxxxx`

---

## Windows Deployment (PowerShell)

### Option A: Using PowerShell Script (Recommended)

```powershell
# 1. Set your credentials
$GitHubURL = "https://github.com/YOUR_USER/normattiva-research"
$HFToken = "hf_xxxxxxxxxxxxx"

# 2. Run deployment script from the OpenNormattiva folder
cd "C:\Users\Dell\Documents\VSC Projects\OpenNormattiva"
.\deploy.ps1 -GitHubRepo $GitHubURL -HFToken $HFToken
```

**What it does automatically:**
- ✅ Configures Git remote
- ✅ Pushes code to GitHub
- ✅ Creates HF Dataset repo
- ✅ Creates HF Space repo
- ✅ Syncs Streamlit app
- ✅ Displays final URLs

### Option B: Manual Step-by-Step

```powershell
cd "C:\Users\Dell\Documents\VSC Projects\OpenNormattiva"

# Step 1: Add GitHub remote
git remote add origin https://github.com/YOUR_USER/normattiva-research
git branch -M master

# Step 2: Push code
git push -u origin master

# Step 3: Create HF repos (requires Python + huggingface_hub)
$env:HF_TOKEN = "hf_xxxxxxxxxxxxx"
python -c "
from huggingface_hub import HfApi, create_repo
api = HfApi()
user = api.whoami(token='$env:HF_TOKEN')['name']
create_repo(f'{user}/normattiva-data-raw', repo_type='dataset', exist_ok=True, token='$env:HF_TOKEN')
create_repo(f'{user}/normattiva-search', repo_type='space', space_sdk='streamlit', exist_ok=True, token='$env:HF_TOKEN')
print(f'✓ Repos created for {user}')
"
```

---

## Linux / macOS Deployment (Bash)

```bash
# 1. Set your credentials
export GITHUB_REPO="https://github.com/YOUR_USER/normattiva-research"
export HF_TOKEN="hf_xxxxxxxxxxxxx"

# 2. Run deployment script
cd ~/Documents/OpenNormattiva
chmod +x deploy.sh
./deploy.sh "$GITHUB_REPO" "$HF_TOKEN"
```

---

## Manual Cloud Deployment (No CLI)

If you prefer not to run local scripts, you can deploy via web:

### Via GitHub Web Interface
1. Go to https://github.com/new
2. Name: `normattiva-research`
3. Create empty repo
4. In VS Code, open integrated terminal:
   ```powershell
   git remote add origin https://github.com/YOUR_USER/normattiva-research
   git push -u origin master
   ```

### Via HuggingFace Web Interface
1. Go to https://huggingface.co/new-dataset
   - Name: `normattiva-data-raw`
   - Create ✓
2. Go to https://huggingface.co/new-space
   - Name: `normattiva-search`
   - SDK: Streamlit
   - Create ✓
3. In Space settings: Link to GitHub repo (copy `space/app.py` content to Space's `app.py`)

---

## What Happens After Deployment

### Immediate (Minutes 0-5)
- ✅ Code appears in GitHub repo
- ✅ GitHub Actions enabled
- ✅ HF Dataset repo created (empty, waiting for data)
- ✅ HF Space repo created with Streamlit app

### First Nightly Run (Tomorrow 2 AM UTC)
- GitHub Actions workflow triggers automatically
- Downloads latest Vigente laws (6 collections, ~2-3 min)
- Parses AKN XML → JSONL (~5-8 min)
- Builds citation index (~3-5 min)
- Generates metrics (~2-3 min)
- Uploads to HF Dataset (~5 min)
- HF Space auto-refreshes with new data

### After First Run (Morning)
- ✅ 300,000+ Italian laws in HF Dataset
- ✅ Full-text searchable via Streamlit UI
- ✅ Citation network indexed and visualized
- ✅ Metrics dashboard showing law statistics
- ✅ Browse interface with filtering

### Daily Thereafter
- 🔄 Automatic nightly updates at 2 AM UTC
- 🔄 New laws added to dataset
- 🔄 Updated citation indices
- 🔄 Fresh metrics and statistics

---

## Verification Checklist

After running deployment, verify everything works:

### GitHub
- [ ] Repository visible at `https://github.com/YOUR_USER/normattiva-research`
- [ ] Files: `parse_akn.py`, `pipeline.py`, `space/app.py` present
- [ ] Actions tab shows "Nightly" workflow
- [ ] Workflow enabled and running tomorrow

### HuggingFace Dataset
- [ ] Dataset repo at `https://huggingface.co/datasets/YOUR_USER/normattiva-data-raw`
- [ ] Initially empty (will fill after first nightly run)
- [ ] Settings show GitHub connection (optional)

### HuggingFace Space
- [ ] Space at `https://huggingface.co/spaces/YOUR_USER/normattiva-search`
- [ ] Streamlit app loads (may show warning about missing data initially)
- [ ] All 5 pages visible in sidebar:
  - Dashboard
  - Browse
  - Search
  - Citations
  - Amendments

### After First Run (Check Tomorrow Morning)
- [ ] GitHub Actions → "Nightly" workflow completed ✓
- [ ] HF Dataset shows `laws_vigente.jsonl` files
- [ ] HF Space loads full dashboard with metrics
- [ ] Can search for laws
- [ ] Citation network displays

---

## Troubleshooting

### "Git remote already exists"
```powershell
git remote set-url origin https://github.com/YOUR_USER/normattiva-research
git push -u origin master
```

### "HF_TOKEN invalid"
- Verify token from https://huggingface.co/settings/tokens
- Ensure it's a "Write" token (not "Read")
- Try regenerating if unsure

### "GitHub Actions not running"
- Check: Settings → Actions → General
- Enable: "Allow all actions and reusable workflows"
- Trigger manually: Actions tab → Nightly → Run workflow

### "Streamlit shows 'No data available'"
- Normal on first day (waiting for pipeline run)
- Check tomorrow after 2 AM UTC
- Or manually trigger: GitHub Actions → Nightly → Run workflow

### "Space not syncing from GitHub"
- Manual fix: Go to HF Space settings
- Copy content of `space/app.py` from GitHub
- Paste into Space's `app.py`
- Save and restart

---

## Architecture After Deployment

```
GitHub (Source + CI/CD)
├── parse_akn.py
├── pipeline.py
├── space/app.py
├── requirements.txt
└── .github/workflows/nightly-update.yml
    ↓ (Daily 2 AM UTC)
    
HF Dataset (Data Storage)
├── laws_vigente.jsonl (300K laws)
├── laws_citations.json (citation index)
└── laws_metrics.json (statistics)
    ↓ (Auto-synced)
    
HF Space (Research UI)
├── Dashboard (metrics)
├── Browse (filtering)
├── Search (full-text)
├── Citations (network)
└── Amendments (complexity)
```

---

## Support

**Need help?**

- **GitHub push issues**: Ensure username/email configured
  ```powershell
  git config user.name "Your Name"
  git config user.email "your@email.com"
  ```

- **HF token issues**: Create new token at https://huggingface.co/settings/tokens

- **Space not updating**: Check GitHub Actions logs for errors

- **Want to change schedule**: Edit `.github/workflows/nightly-update.yml`
  - Line `cron: '0 2 * * *'` = daily 2 AM UTC
  - Change to `cron: '0 2 * * 1'` for Mondays only
  - Push change, workflow updates automatically

---

## Timeline Summary

| Action | Time | Result |
|--------|------|--------|
| Run deploy script | 5 min | Code in GitHub, HF repos created |
| First nightly run | Tomorrow 2 AM UTC + 20 min | Data in HF Dataset |
| HF Space refresh | Auto | UI displays 300K laws |
| **TOTAL TO LIVE** | **~24 hours** | **Production Ready** |

---

## Next Steps

1. **Get your URLs ready:**
   - GitHub: Create repo, copy URL
   - HuggingFace: Create token

2. **Run deployment** (choose one):
   - Option A: `.\deploy.ps1 -GitHubRepo "..." -HFToken "..."`
   - Option B: Manual steps above
   - Option C: Manual via web interfaces

3. **Verify** (check boxes above)

4. **Wait for tomorrow's run** (or trigger manually)

5. **Share your Space** with colleagues:
   - https://huggingface.co/spaces/YOUR_USER/normattiva-search

---

**Everything is ready. The system is fully automated from here.**

Your research platform will run daily with zero manual intervention. 🚀
