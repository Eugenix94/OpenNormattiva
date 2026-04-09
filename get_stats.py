#!/usr/bin/env python3
"""Fetch current dataset stats."""
import os
from huggingface_hub import hf_hub_download
import json

DATASET_REPO = "diatribe00/normattiva-data-raw"

print("Fetching current dataset metrics...\n")
try:
    local = hf_hub_download(
        repo_id=DATASET_REPO,
        filename="data/indexes/laws_vigente_metrics.json",
        repo_type="dataset",
        token=os.environ.get("HF_TOKEN")
    )
    with open(local, encoding='utf-8') as f:
        metrics = json.load(f)
    
    print(f"Total Laws Parsed: {metrics.get('total_laws', 0):,}")
    print(f"Generated: {metrics.get('generated', 'unknown')}")
    print(f"Total Articles: {metrics['article_stats']['total']:,}")
    print(f"Avg Articles per Law: {metrics['article_stats']['avg']:.1f}")
    print(f"Total Characters: {metrics['text_stats']['total_chars']:,}")
    print(f"Avg Chars per Law: {metrics['text_stats']['avg_chars']:,.0f}")
    
    print(f"\n{'Document Type':<40} {'Count':>10}")
    print("="*52)
    for typ, count in sorted(metrics.get('by_type', {}).items(), key=lambda x: -x[1]):
        print(f"{typ:<40} {count:>10,}")
    
    print(f"\nYear Span: {min(metrics.get('by_year', {}).keys() or ['?'])} to {max(metrics.get('by_year', {}).keys() or ['?'])}")
    
except Exception as e:
    print(f"Error: {e}")
