# Deploy Enhanced Lab — Step by Step

## What You're Deploying

A comprehensive jurisprudence research platform that:
- ✅ Includes full Normattiva dataset (vigente + abrogate + multivigente)
- ✅ Adds Constitutional Court (Corte Costituzionale) decisions
- ✅ Provides integrated search across laws and sentenze
- ✅ Includes advanced analytics (citation networks, trends, etc.)
- ✅ Remains separate from production (own space + dataset)

## Deployment Steps

### 1. Verify Files Exist

```bash
# Check all required files are present:
ls -la core/lab_db.py
ls -la space/enhanced_lab_app.py
ls -la jurisprudence_loader.py
ls -la clone_to_lab.py
```

### 2. Run Enhanced Lab Clone

```bash
# Deploy the enhanced lab
python clone_to_lab.py

# Or dry-run first to see what would happen:
python clone_to_lab.py --dry-run

# Skip dataset if it already exists (faster):
python clone_to_lab.py --skip-dataset
```

### 3. Wait for Deployment

The script will:
1. Create/update `diatribe00/normattiva-lab-data` (dataset)
2. Create/update `diatribe00/normattiva-lab` (space)
3. Copy all files to the space
4. Push to Hugging Face

### 4. Verify Lab is Running

Visit: https://huggingface.co/spaces/diatribe00/normattiva-lab

Expected:
- Space status: `Running`
- App title: `OpenLaw Lab — Enhanced Jurisprudence Research Platform`
- Sidebar shows: 🔬 Lab Dashboard, 🔍 Ricerca Integrata, 📖 Esplora Leggi, etc.

### 5. Test Features

1. **Lab Dashboard**: View stats and trends
2. **Integrated Search**: Try searching for "diritti" or "proprietà"
3. **Law Explorer**: Browse laws with jurisprudence connections
4. **Multivigente**: Explore historical law versions
5. **Analytics**: View citation networks and domains

## Database Structure

The enhanced lab database includes:

### Normattiva Tables (from production)
```sql
laws (urn, title, year, status, article_count, text_length, ...)
citations (citing_urn, cited_urn, ...)
domains (law_urn, domain, ...)
```

### NEW Jurisprudence Tables
```sql
sentenze (decision_id, court, decision_date, decision_year, title, full_text, ...)
sentenza_citations (sentenza_id, cited_urn, citation_type, ...)
sentenza_topics (sentenza_id, topic, relevance_score, ...)
law_jurisprudence_links (law_urn, sentenza_id, link_type, ...)
```

## Customization

### Add Real Constitutional Court Data

Once deployed, you can add real sentenze by:

```python
from jurisprudence_loader import JurisprudenceLoader

loader = JurisprudenceLoader('/path/to/laws.db')

# Load from JSON file
loader.load_from_json_file('path/to/sentenze.json')

# Or from Python objects
sentenze = [
    {
        'decision_id': 'sent_cc_1_2023',
        'court': 'Corte Costituzionale',
        'decision_date': '2023-01-15',
        'decision_year': 2023,
        'decision_type': 'sentenza',
        'number': '1',
        'title': 'Sentenza n. 1/2023',
        'citations': [
            {'cited_urn': 'urn:nir:stato:costituzione:...', 'citation_type': 'explicit'}
        ],
    },
    # ... more sentenze
]
loader.load_from_dict_list(sentenze)
```

### Connect to Normattiva Sentenze API (Future)

The `jurisprudence_loader.py` has a method `load_from_normattiva_api()` ready to connect to the Normattiva API once sentenze support is added to `normattiva_api_client.py`.

## Troubleshooting

### Space fails to build
- Check Docker build logs in space settings
- Ensure all Python files have correct imports
- Verify `requirements.txt` has all dependencies

### Database won't load
- Check that `/app/data/laws.db` exists
- Verify download from HF dataset succeeded
- Check database size (should be > 1 GB)

### App shows "Database not available"
- Wait for space to finish building
- Check space logs for download errors
- Verify HF token has dataset read access

## Production vs Lab

```
Production Space (normattiva-search)
├─ Standard app.py
├─ Vigente laws only
└─ No jurisprudence

Lab Space (normattiva-lab) ← NEW
├─ Enhanced app (enhanced_lab_app.py)
├─ Full data (vigente + abrogate + multivigente)
├─ Constitutional Court decisions
└─ Advanced analytics
```

## Next Steps

1. ✅ Deploy enhanced lab
2. ✅ Verify it's running
3. ✅ Explore sample data
4. 📋 (Optional) Add real Constitutional Court decisions from Normattiva API
5. 📋 (Optional) Add other data sources (Cassazione, etc.)

---

Questions or issues? Check the space logs at:
https://huggingface.co/spaces/diatribe00/normattiva-lab/logs
