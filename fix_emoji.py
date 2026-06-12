#!/usr/bin/env python3
"""Fix garbled emoji characters in space/app.py"""
SCROLL = "\U0001f4dc"   # 📜
CLIPBOARD = "\U0001f4cb"  # 📋
REPLACEMENT = "\ufffd"   # garbled placeholder

src = open("space/app.py", encoding="utf-8").read()

before = src.count(REPLACEMENT)
src = src.replace(REPLACEMENT + " Storia Normativa", SCROLL + " Storia Normativa")
src = src.replace(REPLACEMENT + CLIPBOARD + " Browse", CLIPBOARD + " Browse")
after = src.count(REPLACEMENT)

open("space/app.py", "w", encoding="utf-8").write(src)
print(f"Fixed {before - after} replacement chars (remaining: {after})")
