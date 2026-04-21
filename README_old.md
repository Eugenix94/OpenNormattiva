# Normattiva Research Platform

**Automated Italian legal code explorer with full jurisprudence visualization**

Extract, parse, visualize: Normattiva law data → JSONL pipeline → Streamlit research interface

---

## 🚀 Enhanced Pipeline (NEW - April 2026)

The platform now includes **enhanced incremental updates** for production deployment:

```
✅ Update-only downloads (50-200 MB instead of 1.65 GB)
✅ Zero-downtime staging/promotion pattern  
✅ Automatic backups before every update
✅ Rollback capability in < 1 second
✅ Smart delta detection (only new/changed laws)
```

**📚 Quick References:**
- **Just show me commands:** [ENHANCED_PIPELINE_QUICKSTART.md](ENHANCED_PIPELINE_QUICKSTART.md) (5 min)
- **How it works:** [ENHANCED_PIPELINE_ARCHITECTURE.md](ENHANCED_PIPELINE_ARCHITECTURE.md) (15 min)
- **Set up production:** [ENHANCED_PIPELINE_DEPLOYMENT.md](ENHANCED_PIPELINE_DEPLOYMENT.md) (20 min)
- **Complete overview:** [ENHANCED_SOLUTION_SUMMARY.md](ENHANCED_SOLUTION_SUMMARY.md) (2 min)

**Choose your pipeline:**
- **Static** (`static_pipeline.py`): Safe, checks all laws each time (use for learning)
- **Enhanced** (`enhanced_pipeline.py`): Fast, incremental, production-grade (use for live data)

---

## 🎯 What This Does

```
Normattiva API (AKN ZIP)
    ↓ download_normattiva.py
Raw ZIP files (AKN XML)
    ↓ parse_akn.py
JSONL (structured laws)
    ↓ pipeline.py
Indexes + Metrics
    ↓ push to HF Dataset
Streamlit UI (HF Space)
    ↓
Research platform
```

**Features**:
- 📊 Dashboard: Dataset statistics, type/year distribution
- 📋 Browse: Filter laws by type/year, paginated view
- 🔍 Search: Full-text search across ~300K laws
- 🔗 Citations: Network analysis of law references
- 📜 Amendment tracking: Complex laws by article count
- ⚡ Real-time updates: Nightly via GitHub Actions

---

## 📋 Project Structure

```
.
├── pipeline.py                    # Main orchestrator (download → parse → index)
├── parse_akn.py                   # AKN XML → JSON converter
├── normattiva_api_client.py       # API wrapper for Normattiva
├── requirements.txt               # Python dependencies
├── .github/
│   └── workflows/
│       └── nightly-update.yml     # GitHub Actions workflow (daily 2 AM UTC)
├── data/
│   ├── raw/                       # Downloaded ZIP files (AKN XML)
│   ├── processed/                 # JSONL output
│   └── indexes/                   # Citations & metrics JSON
└── space/
    └── app.py                     # Streamlit app (HF Space)
```

---

## 🚀 Quick Start

### 1. Setup

```bash
git clone https://github.com/YOUR_USER/normattiva-research
cd normattiva-research

python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Run Locally (Test)

```bash
# Download a couple collections, parse, index
python pipeline.py --variants vigente --collections Cost DPR

# Check output
ls -la data/processed/
ls -la data/indexes/

# Run Streamlit
streamlit run space/app.py
```

Open http://localhost:8501

### 3. Deploy to GitHub + HF

Push this repo to GitHub, create HF Dataset + Space (linked to GitHub repo), and workflow runs automatically every 24 hours.

---

## 💾 Data Formats & Variants (Choose What You Need)

### Available Collections (23 Total - 286K+ Laws)

```
22 VIGENTE COLLECTIONS (Current/Active Laws)
├─ All support: AKN (recommended) + XML formats
├─ All support: O (Original) + V (Vigente) + M (Multivigente) variants
├─ Total: 162,391 acts
└─ Recommendation: Use V (vigente = current law) ✅

1 ABROGATE COLLECTION (Abrogated/Repealed Laws)
├─ Supports: AKN (recommended) + XML formats
├─ Format only: O (Original, no V/M exists)
├─ Total: 124,036 historical acts
└─ Recommendation: Keep separate, load with V ✅
```

### Format Decision: AKN vs XML

| Feature | AKN | XML |
|---------|-----|-----|
| **Parser support** | ✅ Yes | ✅ Yes |
| **Size** | ~30% smaller | ~30% larger |
| **Standard** | EU (Akoma Ntoso) | Generic |
| **Recommendation** | ✅ Use this | Fallback only |

**Decision:** Use **AKN format** (all 23 collections support it) ✅

See [DATA_FORMAT_ANALYSIS.md](DATA_FORMAT_ANALYSIS.md) for complete analysis.

### Variant Decision: V vs O vs M

| Variant | Name | What It Is | Size | Use Case |
|---------|------|-----------|------|----------|
| **V** 🔴 | Vigente | Current law in force | 1.2 GB | ✅ Use NOW |
| **O** 🟠 | Originale | Original text (historical) | 1.3 GB | ⏳ Abrogate only |
| **M** 🟡 | Multivigente | All versions + timelines | 2.8 GB | ⏹️ Skip (unless requested) |

**Decision:**
- ✅ **Phase 1 (NOW):** Use V (vigente) = 1.2 GB, fast, sufficient for most use cases
- ⏳ **Phase 2 (if needed):** Add O (abrogate) = separate collection, 1.3 GB
- ⏹️ **Phase 3 (future):** Add M (multivigente) only if users request amendment history feature

**Why skip M?**
- Adds 2.0 GB storage (15% of collection size)
- Adds 3× query latency (slower searches)
- Most operations need current law (V), not historical versions
- Can add M later if customer requests "amendment timeline" feature

See [MULTIVIGENTE_ANALYSIS.md](MULTIVIGENTE_ANALYSIS.md) for detailed comparison.

---

## 🔄 Update Strategy (Enhanced Pipeline)

### For Production (Recommended)

Use the **Enhanced Pipeline** with **semi-automated updates**:

```bash
# Sunday 2 AM UTC: Automated
python enhanced_pipeline.py --mode incremental  # 5-10 min
python enhanced_pipeline.py --mode verify       # 2 min

# Human review (GitHub PR), then:
python enhanced_pipeline.py --mode promote      # < 1 sec (zero downtime)
```

**Benefits:**
- ✅ 5-10 min updates (vs 2-4 hours full downloads)
- ✅ 50-200 MB bandwidth (vs 1.65 GB)
- ✅ Zero downtime (staging pattern)
- ✅ Automatic backups
- ✅ Manual verification gate

See [ENHANCED_PIPELINE_DEPLOYMENT.md](ENHANCED_PIPELINE_DEPLOYMENT.md) for full setup.

### For Learning/Testing

Use the **Static Pipeline** (checks all laws each time, safe for development):

```bash
python static_pipeline.py --mode full   # Download everything
python static_pipeline.py --mode sync   # Check what changed
```

---

## 📊 Data Pipeline

### Step 1: Download
**Script**: `download_normattiva.py` (via `normattiva_api_client.py`)
- Downloads pre-packaged collections from Normattiva API
- Format: AKN (XML)
- Output: `data/raw/*.zip`

**Collections** (23 total, including ~300K laws):
- `Cost` (Constitution)  
- `DPR` (Presidential Decrees)
- `Leggi` (Laws)
- ... and 20 more

**Variants**:
- `O` (Originale): Historic original
- `V` (Vigente): Current law in force (~0.8 GB)
- `M` (Multivigente): All versions with history (~2.8 GB, optional)

### Step 2: Parse
**Script**: `parse_akn.py`
- Converts AKN XML → structured JSON
- Extracts: URN, title, type, date, full text, articles, citations
- One law = one JSON line (JSONL format)
- Output: `data/processed/laws_*.jsonl`

**Why JSONL?**
- Queryable without loading entire file
- Git-friendly (line-by-line changes)
- Streamlit-friendly (lazy load)
- ~50% smaller than AKN XML

### Step 3: Index
**Script**: `pipeline.py`
- Citation extraction: finds references between laws
- Building index: `data/indexes/laws_citations.json`
- Metrics generation: `data/indexes/laws_metrics.json`

### Step 4: Upload (Optional)
- Push to HF Dataset repo via GitHub Actions
- HF Space auto-syncs and rebuilds UI

---

## 🏗️ Architecture

### GitHub Repo (This)
- Code + data processing
- Runs pipeline nightly via GitHub Actions
- Pushes outputs to HF Dataset

### HF Dataset
- Public storage (~5.6 GB JSONL)
- Queryable with `datasets` library
- API access for research

### HF Space
- Streamlit UI (synced from `space/app.py`)
- Visualizes laws, citations, metrics
- Free CPU tier (sufficient for 300K laws)

---

## 🔧 Configuration

### GitHub Actions Secrets
Set in repo Settings → Secrets:
- `HF_TOKEN` (optional): Auto-upload to HF Dataset

### Environment Variables
```bash
# .env (for local testing)
HF_TOKEN=hf_xxxxx
```

### Time Config
Edit `.github/workflows/nightly-update.yml`:
```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # Daily 2 AM UTC
```

---

## 📈 Outputs

### JSONL Format (laws_vigente.jsonl)
```jsonl
{"urn": "urn:nir:stato:legge:2021-01-01;123", "title": "Legge di bilancio", "type": "Legge", "date": "2021-01-01", "year": "2021", "text": "...", "article_count": 45, "articles": [...], "citations": ["L.227/2023"], "text_length": 123456, "parsed_at": "2026-04-08T..."}
```

### Citation Index (laws_citations.json)
```json
{
  "generated": "2026-04-08T...",
  "total_laws_with_citations": 15342,
  "total_citations": 234567,
  "citations": {
    "urn:nir:...": {
      "law": "Codice Civile",
      "citations": ["L.123/2021", "DL.45/2020"],
      "count": 892
    }
  }
}
```

### Metrics (laws_metrics.json)
```json
{
  "total_laws": 286422,
  "by_type": {"Legge": 125000, "DPR": 85000, ...},
  "by_year": {"1861": 12, "1900": 234, ...},
  "text_stats": {"total_chars": 2e9, "avg_chars": 7234},
  "article_stats": {"total": 2134567, "avg": 7.5}
}
```

---

## 🎮 Local Testing

```bash
# Parse one collection
python parse_akn.py --input data/raw/Cost_vigente.zip --output data/processed/laws_vigente.jsonl

# Full pipeline (small test)
python pipeline.py --variants vigente --collections Cost

# Check Streamlit
streamlit run space/app.py
```

---

## 📈 Performance

| Operation | Time | Data Size |
|-----------|------|-----------|
| Download Vigente (6 collections) | 2-3 min | 1.3 GB |
| Parse to JSONL | 5-8 min | 0.6 GB |
| Build citation index | 3-5 min | 150 MB |
| Generate metrics | 2-3 min | 2 MB |
| **Total Pipeline** | **~15-20 min** | **~0.8 GB** |

---

## 🚨 Troubleshooting

### No data after running pipeline
```bash
# Check what was parsed
ls -la data/processed/
head data/processed/laws_vigente.jsonl
```

### Streamlit can't find data
```bash
# Ensure you're running from repo root
pwd  # should be .../OpenNormattiva
streamlit run space/app.py
```

### GitHub Actions fails
1. Check workflow logs: repo → Actions tab
2. Common issues:
   - Missing `requirements.txt` dependencies
   - Python version mismatch
   - API unavailable (retry manually)

### Citation index empty
- Ensure JSONL files were generated
- Check parse_akn.py extracted text properly
- Try with full `vigente` download

---

## 🔑 Key Files Explained

| File | Purpose | Key Functions |
|------|---------|---|
| `pipeline.py` | Orchestrator | `run_pipeline()` coordinates download→parse→index |
| `parse_akn.py` | XML parser | `AKNParser.parse_zip_file()` converts AKN→JSON |
| `normattiva_api_client.py` | API client | `NormattivaAPI.get_collection()` downloads ZIPs |
| `space/app.py` | UI | Streamlit pages: dashboard, browse, search, citations |

---

## 📞 API Reference

### parse_akn.py
```python
from parse_akn import AKNParser

parser = AKNParser()
laws = parser.parse_zip_file(Path('data/raw/Cost_vigente.zip'))
parser.to_jsonl(laws, Path('laws.jsonl'))
```

### normattiva_api_client.py
```python
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI()
collections = api.get_collection_catalogue()  # All available
data, etag, ct = api.get_collection('Cost', variant='V', format='AKN')
```

### pipeline.py
```python
from pipeline import NormattivaPipeline

pipeline = NormattivaPipeline(data_dir=Path('data'))
laws = pipeline.run_pipeline(variants=['vigente'], collections=['Cost', 'DPR'])
```

---

## 📅 Update Schedule

**Daily (2 AM UTC)**:
- Download latest Vigente
- Parse to JSONL
- Build citation index
- Generate metrics
- Push to repo (committed in git history)

**Manual override**:
```bash
git push
# Triggers GitHub Actions immediately
```

---

## 🤝 Contributing

1. Fork this repo
2. Make changes
3. Test locally: `python pipeline.py`
4. Push → GitHub Actions runs
5. Data syncs to HF Dataset

---

## 📚 References

- [Normattiva API Docs](https://dati.normattiva.it/)
- [Akoma Ntoso Standard](http://www.akomantoso.org/)
- [HuggingFace Datasets](https://huggingface.co/docs/datasets/)
- [Streamlit Docs](https://docs.streamlit.io/)

---

## 📄 License

Code: Apache 2.0  
Data: Public domain (Italian laws, Normattiva)

---

**Status**: ✅ Production Ready  
**Last Updated**: 2026-04-08  
**Next Auto-Update**: Tomorrow 2 AM UTC
[DRY RUN] Would download Decreti Legislativi (AKN ORIGINALE)
…
```

### 3. First Live Download

```bash
python download_normattiva.py
```

Outputs:
- `akn/originale/DPR.zip`, `akn/originale/Decreti_Legislativi.zip`, … (23 files)
- `manifests/versions_manifest.jsonl` — metadata for each file
- `metadata/update_log.json` — run summary
- `metadata/last_update.txt` — ISO timestamp

### 4. Check Current Law (Real-time)

```python
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI()

# Get current law in force
data, meta = api.get_vigente("DPR")  # Latest with amendments
print(f"Law is current (V variant), ETag: {meta['etag']}")

# Get historical versions
data, meta = api.get_multivigenza("Codici")  # All versions
print(f"Full history, size: ~{len(data)/1e6:.0f} MB")

# Get official text
data, meta = api.get_originale("DPR")  # Original as published
print(f"Official original text, from local mirror")
```

### 5. Check Mirror Freshness

```bash
python reconcile_mirror.py
```

Output:
```
========================================================================
MIRROR RECONCILIATION REPORT
Summary:
  Total collections : 23
  Up-to-date         : 23
  Stale (changed)    : 0
```

---

## Architecture

### Why Hybrid (Mirror + API)?

```
Normattiva Live API (https://api.normattiva.it/t/normattiva.api/bff-opendata/v1)
    │
    ├─── O (ORIGINALE)
    │    └─→ Mirror locally (1.36 GB)
    │        ✓ Stable (rarely changes)
    │        ✓ Offline-capable
    │        ✓ Reproducible
    │
    ├─── V (VIGENTE — Current Law in Force)
    │    └─→ Query API only (not mirrored)
    │        ✓ Always fresh (amendments apply in real-time)
    │        ✓ Small (~0.8 GB, but changes frequently)
    │        ✓ Critical for compliance
    │
    └─── M (MULTIVIGENZA — All Historical Versions)
         └─→ Query API on-demand (not mirrored)
              ✓ Large (2.84 GB for all 23 collections)
              ✓ Rarely needed (compliance audits, historical research)
              ✓ Server sometimes times out (HTTP 500)
              ✓ Can cache locally if demand justifies

Benefits of Hybrid:
✓ Lower storage (1.36 GB vs 5 GB for "mirror everything")
✓ Faster nightly sync (~25 min vs ~60 min)
✓ Better authority (VIGENTE always comes from API)
✓ Flexible (users choose their data source based on use case)
✓ Lower maintenance (no mirroring flaky large files)
```

---

## Variants Explained

| Feature | ORIGINALE (O) | VIGENTE (V) | MULTIVIGENZA (M) |
|---------|---------------|------------|------------------|
| **What is it?** | Official text as published | Current law in force | All versions + history |
| **Size** | 1.36 GB | 0.81 GB | 2.84 GB |
| **Changes** | Monthly (minor fixes) | Daily (amendments) | Constant (new versions) |
| **Use case** | Reference, research | Compliance, lawyer work | Audits, evolution tracking |
| **Where** | 📦 Local mirror | 🔗 API | 🔗 API (or cache) |
| **Freshness** | 24h (nightly) | Real-time | Real-time |
| **Reliability** | ✅ High | ✅ High | ⚠️ Medium (timeout issues) |

---

## Command Reference

### Mirror

```bash
# Download all ORIGINALE collections
python download_normattiva.py

# Download single collection (dry run)
python download_normattiva.py --collection DPR --dry-run

# Download single collection (for real)
python download_normattiva.py --collection DPR
```

### Check Freshness

```bash
# Report what's stale
python reconcile_mirror.py

# Auto re-download stale
python reconcile_mirror.py --sync
```

### Query API

```python
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI()

# Get current law (VIGENTE—always fresh)
data_v, meta_v = api.get_vigente("DPR", format="AKN")
print(f"Current law ETag: {meta_v['etag']}")

# Get all versions (MULTIVIGENZA—historical)
try:
    data_m, meta_m = api.get_multivigenza("Codici", format="AKN")
    print(f"Full history: {len(data_m)/1e6:.0f} MB")
except Exception as e:
    print(f"Not available (server issue): {e}")

# Get official text (ORIGINALE—stable reference)
data_o, meta_o = api.get_originale("DPR", format="AKN")
print(f"Official text ETag: {meta_o['etag']}")

# Check if fresh without downloading
etag = api.check_collection_etag("DPR", variant="V", format="AKN")
print(f"Current V variant ETag: {etag}")
```

---

## Use Case Guide

Choose your source based on what you need:

### "What's the law RIGHT NOW?" (Compliance)
→ Use **VIGENTE (V)** from API
```python
data, meta = api.get_vigente("DL")
# This is what a judge will enforce today
```

### "What was the original text?" (Research/Citation)
→ Use **ORIGINALE (O)** from local mirror
```python
# Download all locals first
python download_normattiva.py

# Then reference: akn/originale/DL.zip
# Or fetch from API:
data, meta = api.get_originale("DL")
```

### "Show me all changes over 10 years" (Audit)
→ Use **MULTIVIGENZA (M)** from API
```python
data, meta = api.get_multivigenza("Codici")
# This contains every version + amendment dates
```

### "Bulk download all acts for analysis" (Data Science)
→ Use local mirror (ORIGINALE)
```python
# From HuggingFace:
from datasets import load_dataset
ds = load_dataset("YOUR_USERNAME/normattiva-raw")
# All ZIPs in akn/originale/ subfolder
```

---

## Architecture

## File Structures

### Local Mirror Layout

```
akn/
  originale/
    Codici.zip                    (acts in AKN ORIGINALE format + metadata XML)
    Decreti_Legislativi.zip
    …
manifests/
  versions_manifest.jsonl         (one JSON line per file: act_id, variant, dates, hashes, ETag)
metadata/
  update_log.json                 (per-run: timestamp, file list, sha256, success/fail counts)
  last_update.txt                 (ISO timestamp for cache validation)
  reconciliation_last.json        (staleness report from last reconcile)
```

### Manifest Schema (JSONL)

One line per collection:
```json
{
  "collection_name": "Codici",
  "variant": "O",
  "acts_count": 9,
  "file_path": "akn/originale/Codici.zip",
  "sha256": "abc123def456…",
  "etag": "\"7f1b2c3d\"",
  "downloaded_at": "2025-02-13T10:15:00Z",
  "api_built_at": "2025-02-01T00:00:00Z",
  "acts_date_min": "1865-01-01",
  "acts_date_max": "2025-02-10"
}
```

### Update Log Schema (JSON)

```json
{
  "timestamp": "2025-02-13T10:15:00Z",
  "total_attempts": 23,
  "successful_downloads": 23,
  "failed_downloads": 0,
  "total_size_mb": 1355.6,
  "download_duration_seconds": 623,
  "files": [
    {
      "collection": "Codici",
      "variant": "O",
      "success": true,
      "size_mb": 14.2,
      "sha256": "abc123…",
      "etag": "\"7f1b2c3d\""
    }
  ]
}
```

## Command Reference

### Download Module

```bash
# Download all ORIGINALE collections
python download_normattiva.py

# Download single collection (dry run)
python download_normattiva.py --collection DPR --dry-run

# Download single collection (commit to mirror)
python download_normattiva.py --collection DPR

# Verbose output
python download_normattiva.py --verbose
```

### API Wrapper

```python
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI(retries=2)

# Get all collections and variants
catalogue = api.get_collection_catalogue()
for c in catalogue:
    print(f"{c['nomeCollezione']}: {c['numeroAtti']} acts ({c['formatoCollezione']})")

# Download a specific collection
data, metadata = api.get_collection(
    nome="DPR",
    variant="O",
    format="AKN"
)
with open("DPR.zip", "wb") as f:
    f.write(data)
print(f"Downloaded: {metadata['content_type']}, ETag: {metadata['etag']}")

# Check if changed without downloading
etag = api.check_collection_etag(
    nome="DPR",
    variant="O",
    format="AKN"
)

# List available export formats
formats = api.get_extensions()
print(f"Available formats: {formats}")
```

### Reconciliation Module

```bash
# Just report (no downloads)
python reconcile_mirror.py

# Report + auto-download stale collections
python reconcile_mirror.py --sync

# Outputs: metadata/reconciliation_last.json
```

## Initial Deployment Steps

### Step 1: Set Up GitHub Repository

```bash
git init
git add .
git commit -m "Initial: download, API wrapper, reconciliation, workflow"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/normattiva-mirror.git
git push -u origin main
```

### Step 2: Create HuggingFace Dataset

1. Go to https://huggingface.co/new-dataset
2. Create repo: `normattiva-raw` (public, MIT license)
3. Note the dataset URL: `https://huggingface.co/datasets/YOUR_USERNAME/normattiva-raw`

### Step 3: Configure GitHub Secrets

1. Go to GitHub repo → Settings → Secrets and variables → Actions
2. Create `HF_TOKEN`:
   - Generate token at https://huggingface.co/settings/tokens (read+write)
   - Add as repo secret named `HF_TOKEN`

### Step 4: Enable GitHub Actions

1. Go to Actions tab → Enable workflows
2. The `.github/workflows/sync-normattiva.yml` workflow will activate automatically

### Step 5: First Dry Run

```bash
python download_normattiva.py --dry-run
```

Verify output shows all 23 collections listed (no actual download).

### Step 6: First Live Run

```bash
python download_normattiva.py
```

This populates `akn/originale/`, `manifests/versions_manifest.jsonl`, and `metadata/update_log.json`.

### Step 7: Commit Manifest

```bash
git add manifests/versions_manifest.jsonl metadata/
git commit -m "Initial mirror: 23 ORIGINALE collections, 1.35 GB"
git push
```

### Step 8: Verify Workflow

Check GitHub Actions tab — next run at 02:00 UTC will auto-sync and upload to HF.

## Daily Workflow

### For Mirror Operators

**Check freshness (manual, anytime):**
```bash
python reconcile_mirror.py
```
Output shows which collections have changed since last download.

**Force re-download of stale collections:**
```bash
python reconcile_mirror.py --sync
# or manually:
python download_normattiva.py --collection "Codici"
```

**Automated (nightly via GitHub Actions):**
- Workflow runs 02:00 UTC daily
- Runs `reconcile_mirror.py --sync` (downloads only stale items)
- Uploads new ZIPs to HF dataset
- Commits updated manifest back to repo

### For Data Users

**Option 1: Access via HF (recommended for bulk use)**
```python
from datasets import load_dataset

ds = load_dataset("diatribe00/normattiva-raw", split="train")
# Access: ds["akn/originale/DPR.zip"], etc.
```

**Option 2: Query live API (for real-time / specific formats)**
```python
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI()
# Get PDF version of a law
pdf_data, meta = api.get_collection("DPR", variant="O", format="PDF")
```

**Option 3: Download from GitHub releases** (Phase 1: TBD, after stabilization)

## Troubleshooting

### "ModuleNotFoundError: No module named 'normattiva_api_client'"

Ensure both `download_normattiva.py` and `normattiva_api_client.py` are in the same directory.

### "HTTP 500 on Multivigente variant"

Known issue: Normattiva server occasionally times out on large M-variant collections. Phase 1 uses ORIGINALE only (stable). Phase 2 will add M/V with refined retry logic.

### "ETag mismatch but files appear identical"

Run reconcile with `--sync` to re-download. ETags indicate server modification timestamp; if changed, content may differ.

### "HF upload fails in workflow"

Check:
1. `HF_TOKEN` secret is set in repo
2. Token has write access (not read-only)
3. HF dataset exists and is public
4. GitHub Actions has permission to call HF API

### "Manifest JSONL corrupted after interrupted download"

Manually restore from last good commit:
```bash
git checkout manifests/versions_manifest.jsonl
```

Then re-run `download_normattiva.py` for affected collections.

## Performance Metrics

**Current (AKN ORIGINALE, Phase 1):**
- Total size: 1.355 GB (23 collections, 286,422 acts)
- Download time: ~10 min @ 19.3 Mbps avg
- Upload to HF: ~15 min
- Total nightly runtime: ~25 min
- Manifest overhead: ~5 MB
- Update log overhead: ~200 KB per run

**Future (with Phase 2 M/V variants, estimated):**
- Size: ~5.0 GB total (O + M + V for all collections)
- Nightly sync: only re-downloads changed collections (typically 2–5)
- Estimated: ~45 min for full refresh, ~10 min for typical incremental

## License

- **Code**: Apache 2.0
- **Data (mirror)**: CC BY 4.0 (Normattiva OpenData license, effective April 1, 2026)
- **Normattiva**: https://www.normattiva.it/static/pdf/LICENSE_CC-BY_4.0.pdf

## Support & Contributions

- Issues: GitHub Issues
- Questions: GitHub Discussions
- Contributing: Pull requests welcome
- Data accuracy: Normattiva source is authoritative; report discrepancies to Normattiva team

## Roadmap

- **Phase 1 (NOW)** ✅: AKN ORIGINALE mirrored locally, nightly sync, HF dataset
- **Phase 1.5 (THIS MONTH)** 🔄: Extend API client with vigente/multivigenza methods, realtime monitoring ← **YOU ARE HERE**
- **Phase 2 (2–4 WEEKS)**: Add optional caching for VIGENTE (V variant), document real-time compliance queries
- **Phase 3 (1–2 MONTHS)**: Evaluate MULTIVIGENZA mirror (decide based on: user demand + server stability + storage capacity)
- **Phase 4 (TBD)**: All 8 export formats + GitHub Releases + torrent distribution

**Key Decision**: For Phase 3, only mirror MULTIVIGENZA if:
- Server stability improves (zero HTTP 500s for 3+ weeks), AND
- User demand data shows 20%+ of queries request M variant, AND
- HF storage/bandwidth justifies the cost

---

## License

- **Code**: Apache 2.0
- **Data (mirror)**: CC BY 4.0 (Normattiva OpenData license, in effect since April 1, 2026)
- **Normattiva**: https://www.normattiva.it/static/pdf/LICENSE_CC-BY_4.0.pdf

## Support & Contributions

- Issues: GitHub Issues
- Questions: GitHub Discussions
- Contributing: Pull requests welcome
- Data accuracy: Normattiva source is authoritative; report discrepancies to Normattiva team

## Next Steps

1. Read [ARCHITECTURE.md](ARCHITECTURE.md) for detailed analysis of O/M/V variants
2. Review updated `normattiva_api_client.py` methods: `get_vigente()`, `get_multivigenza()`, `get_originale()`
3. Implement Phase 1.5: vigente state monitoring and real-time compliance queries
4. Gather user demand data to inform Phase 3 decision on multivigenza mirroring

---

**Last updated:** April 3, 2026  
**Mirror operator:** [Your name/org]  
**Contact:** [email/issues]
**Architecture summary:** Hybrid (mirror ORIGINALE locally, query API for VIGENTE/MULTIVIGENZA)
