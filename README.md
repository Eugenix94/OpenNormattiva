# OpenNormattiva — Italian Law Research Platform

Search, browse, and analyse **157,000+** Italian laws with full-text search (FTS5/BM25), citation graphs, PageRank importance, and legal domain classification.

**Live**: [HuggingFace Space](https://huggingface.co/spaces/diatribe00/normattiva-search) · **Dataset**: [HuggingFace Dataset](https://huggingface.co/datasets/diatribe00/normattiva-data)

---

## Architecture: Fully Static

The platform uses a **static architecture** — the pre-built SQLite database ships *inside* the Space. No automated pipeline modifies live data.

```
┌──────────────────────────────────────────────────┐
│  LOCAL (your machine)                            │
│                                                  │
│  download_normattiva.py  →  raw ZIPs             │
│  parse_akn.py            →  JSONL                │
│  production_build.py     →  data/laws.db (764 MB)│
│                                                  │
│  deploy_hf.py  ──────────────────────────────┐   │
└──────────────────────────────────────────────│───┘
                                               ▼
┌──────────────────────────────────────────────────┐
│  HF Space (Docker / Streamlit)                   │
│                                                  │
│  data/laws.db (ships with Space — always online)  │
│  space/app.py (read-only, no writes)             │
│                                                  │
│  API polling (read-only, detects new laws)        │
│  Notifications page → review changes             │
│  Update Log → track manual updates               │
└──────────────────────────────────────────────────┘
```

**Key principles:**
- DB is **pre-built locally**, deployed with the Space — no runtime downloads
- API monitoring is **read-only** — detects new/changed collections via ETags
- Updates are **manual** — you decide what enters the dataset
- **Update Log** tracks every change with before/after counts

---

## Quick Start

### 1. Setup

```bash
git clone https://github.com/YOUR_USER/OpenNormattiva
cd OpenNormattiva
python -m venv .venv
.\.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. Download & Build (first time)

```bash
# Download all vigente collections (~1.2 GB)
python download_normattiva.py

# Build the database with enrichment (citations, PageRank, domains)
python production_build.py --enrich
```

### 3. Run Locally

```bash
streamlit run space/app.py
# Open http://localhost:8501
```

### 4. Deploy to HuggingFace

```bash
python deploy_hf.py --token hf_xxx
# Deploys Space (with DB inside) + uploads Dataset
```

---

## Features

| Page | Description |
|------|-------------|
| **Dashboard** | Stats, charts, top laws by PageRank |
| **Search** | FTS5 full-text with BM25 ranking |
| **Browse** | Paginated, filterable list of all laws |
| **Law Detail** | Full text, metadata, citation graph |
| **Citations** | Network explorer, cross-domain analysis |
| **Domains** | 12 legal domain clusters |
| **Notifications** | API change detection (read-only) |
| **Update Log** | Manual update history with notes |
| **Export** | CSV, JSON, JSONL downloads |

---

## Updating the Dataset

When the Notifications page (or GitHub Actions) detects new laws:

```bash
# 1. Download updated collections
python download_normattiva.py --collections "CollectionName"

# 2. Rebuild the database
python production_build.py --enrich

# 3. Record the update in the Update Log (via UI or directly)

# 4. Redeploy
python deploy_hf.py --token hf_xxx
```

GitHub Actions runs a daily check (`.github/workflows/check-changes.yml`) — it polls the API and reports changes as artifacts but does **not** modify any data.

---

## Project Structure

```
├── space/app.py                  # Streamlit UI (static, read-only)
├── deploy_hf.py                  # Deploy Space + Dataset to HuggingFace
├── production_build.py           # One-shot DB build (full or enrich-only)
├── download_normattiva.py        # Download collections from Normattiva API
├── parse_akn.py                  # AKN XML → structured JSON
├── normattiva_api_client.py      # API wrapper (ETag support)
├── law_monitor.py                # ETag-based change detection
├── resolve_citations.py          # Citation URN resolver
├── search_accounting.py          # CLI search helper
├── core/
│   ├── db.py                     # LawDatabase (SQLite + FTS5 + PageRank)
│   ├── changelog.py              # Changelog tracker
│   └── legislature.py            # Legislature metadata
├── data/
│   ├── laws.db                   # Pre-built SQLite database (764 MB)
│   ├── processed/                # JSONL output
│   ├── raw/                      # Downloaded ZIP files
│   └── .etag_cache.json          # ETag cache for change detection
└── .github/workflows/
    └── check-changes.yml         # Daily API poll (notification only)
```

---

## Database Schema

- **laws** — 157K rows, FTS5 full-text index, BM25 ranking, PageRank importance scores
- **citations** — 193K citation links (97K resolved to full URNs)
- **amendments** — Amendment tracking
- **law_metadata** — Domain clusters, citation counts (incoming/outgoing)
- **update_log** — Manual update history
- **api_changes** — API change detection records

---

## Data Stats

| Metric | Value |
|--------|-------|
| Total laws | 157,121 |
| Citations | 193,910 (97,360 resolved) |
| Legal domains | 12 |
| Database size | 764 MB |
| Collections | 22 vigente |
| Year range | 1861–2025 |

---

## License

MIT
