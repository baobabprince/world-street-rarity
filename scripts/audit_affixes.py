#!/usr/bin/env python3
"""Diagnostic script: inspect top prefixes/suffixes before and after normalization per country."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from normalize import fold_for_key, load_lang_rules, normalize_street  # noqa: E402

ROOT = SCRIPT_DIR.parent
CACHE_DIR = ROOT / ".cache"


def main() -> None:
    cfg = json.loads((ROOT / "config" / "countries.json").read_text(encoding="utf-8"))
    release = cfg["overture_release"]
    rules = load_lang_rules()

    selected = {a.upper() for a in sys.argv[1:]}
    for c in cfg["countries"]:
        code = c["code"]
        if selected and code not in selected:
            continue
        pq = CACHE_DIR / f"{code}_{release}_streets_v3.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq)
        first_after = Counter()
        last_after = Counter()
        for raw in df["street_name"].tolist():
            k, _, _ = normalize_street(raw, c["languages"], rules)
            if not k:
                continue
            parts = k.split()
            if len(parts) >= 2:
                first_after[parts[0]] += 1
                last_after[parts[-1]] += 1
        print(f"\n=== {code} ({c['name']}) — top 15 remaining first tokens / last tokens ===")
        print("  FIRST:", first_after.most_common(15))
        print("  LAST: ", last_after.most_common(15))


if __name__ == "__main__":
    main()
