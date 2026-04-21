#!/usr/bin/env python3
"""Rebuild FTS5 index."""
import sys, time
sys.path.insert(0, '.')
from core.db import LawDatabase
db = LawDatabase('data/laws.db')
print('Rebuilding FTS5 index...')
t0 = time.time()
db.conn.execute("INSERT INTO laws_fts(laws_fts) VALUES('rebuild')")
db.conn.commit()
print(f'FTS5 rebuilt in {time.time()-t0:.1f}s')
db.close()
