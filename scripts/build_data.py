#!/usr/bin/env python3
"""Build World Street Rarity static JSON files for configured pilot countries.

Default primary metric: square-root rarity (1 / sqrt(n)).
Also computes Normalized IDF (1 - log(n)/log(C)) and Log (1 / log2(1 + n)).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_overture import extract_country  # noqa: E402
from normalize import load_lang_rules, normalize_street  # noqa: E402

ROOT = SCRIPT_DIR.parent
CONFIG_PATH = ROOT / "config" / "countries.json"
DATA_DIR = ROOT / "data"


def process_country(
    country_cfg: dict[str, Any],
    release: str,
    min_population: int,
    min_streets: int,
    lang_rules: dict[str, dict[str, list[str]]],
    force_extract: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, tuple[str, int]]]:
    """Process one country end-to-end and return (country_summary, cities_list, search_index, street_prevalence)."""
    code = country_cfg["code"]
    langs = country_cfg["languages"]

    cities_pq, streets_pq = extract_country(
        country_cfg,
        release=release,
        min_population=min_population,
        force=force_extract,
    )

    cities_df = pd.read_parquet(cities_pq)
    streets_df = pd.read_parquet(streets_pq)

    # Normalize every raw street name using the country's language profile
    norm_keys: list[str] = []
    disp_names: list[str] = []
    is_num_flags: list[bool] = []
    for raw in streets_df["street_name"].tolist():
        k, d, is_num = normalize_street(raw, langs, lang_rules)
        norm_keys.append(k)
        disp_names.append(d)
        is_num_flags.append(is_num)

    streets_df["norm_key"] = norm_keys
    streets_df["display"] = disp_names
    streets_df["is_numbered"] = is_num_flags

    # Count numbered streets per city before filtering them out
    numbered_per_city = streets_df.groupby("city_id")["is_numbered"].sum().to_dict()

    # Keep valid non-numbered normalized streets & deduplicate per city!
    valid = streets_df[streets_df["norm_key"].str.len() > 0].copy()
    raw_valid_rows = len(valid)
    dedup = valid.drop_duplicates(subset=["city_id", "norm_key"], keep="first").copy()

    # Keep only cities with >= min_streets normalized streets
    city_counts = dedup.groupby("city_id")["norm_key"].count()
    kept_ids = set(city_counts[city_counts >= min_streets].index)
    dedup = dedup[dedup["city_id"].isin(kept_ids)].copy()
    cities_df = cities_df[cities_df["city_id"].isin(kept_ids)].copy()

    n_cities = len(cities_df)
    if n_cities == 0:
        raise RuntimeError(f"No cities remained for {code} after filtering!")

    # Pick cleanest canonical display name per norm_key (most common original display form)
    display_votes: dict[str, Counter[str]] = defaultdict(Counter)
    for k, d in zip(dedup["norm_key"].tolist(), dedup["display"].tolist()):
        display_votes[k][d] += 1
    canon_display: dict[str, str] = {
        k: sorted(cnt.items(), key=lambda x: (-x[1], len(x[0]), x[0]))[0][0]
        for k, cnt in display_votes.items()
    }

    # Nationwide prevalence n = number of cities in this country containing norm_key
    n_by_key = dedup.groupby("norm_key")["city_id"].nunique()
    dedup["n"] = dedup["norm_key"].map(n_by_key)

    log_c = math.log(max(2, n_cities))
    dedup["w_sqrt"] = 1.0 / np.sqrt(dedup["n"].astype(float))
    dedup["w_log"] = 1.0 / np.log2(1.0 + dedup["n"].astype(float))
    dedup["w_idf"] = 1.0 - np.log(dedup["n"].astype(float)) / log_c

    # Per-city aggregation on deduplicated streets
    per = dedup.groupby("city_id").agg(
        streets=("norm_key", "count"),
        pure_exclusive=("n", lambda s: int((s == 1).sum())),
        rarity_sum_sqrt=("w_sqrt", "sum"),
        rarity_mean_sqrt=("w_sqrt", "mean"),
        rarity_sum_idf=("w_idf", "sum"),
        rarity_mean_idf=("w_idf", "mean"),
        rarity_sum_log=("w_log", "sum"),
        rarity_mean_log=("w_log", "mean"),
    ).reset_index()

    merged = per.merge(cities_df, on="city_id", how="inner")

    # Build per-city samples (8 unique + 8 most shared)
    city_samples: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
    for cid, grp in dedup.groupby("city_id"):
        uniq_keys = sorted(grp.loc[grp["n"] == 1, "norm_key"].tolist())
        if len(uniq_keys) > 8:
            rng = random.Random(hash(str(cid)) & 0xFFFFFFFF)
            uniq_pick = sorted(rng.sample(uniq_keys, 8))
        else:
            uniq_pick = uniq_keys
        uniq_disp = [canon_display[k] for k in uniq_pick]

        common_rows = grp[grp["n"] > 1].sort_values(["n", "norm_key"], ascending=[False, True]).head(8)
        common_disp = [
            {"name": canon_display[str(r["norm_key"])], "key": str(r["norm_key"]), "n": int(r["n"])}
            for _, r in common_rows.iterrows()
        ]
        city_samples[str(cid)] = (uniq_disp, common_disp)

    cities_out: list[dict[str, Any]] = []
    for _, r in merged.iterrows():
        cid = str(r["city_id"])
        streets_n = int(r["streets"])
        excl_n = int(r["pure_exclusive"])
        num_n = int(numbered_per_city.get(r["city_id"], 0))
        numbered_pct = round(num_n / (streets_n + num_n) * 100.0, 1) if (streets_n + num_n) > 0 else 0.0

        mean_sqrt = round(float(r["rarity_mean_sqrt"]) * 100.0, 1)
        sum_sqrt = round(float(r["rarity_sum_sqrt"]), 1)
        mean_idf = round(float(r["rarity_mean_idf"]) * 100.0, 1)
        sum_idf = round(float(r["rarity_sum_idf"]), 1)
        mean_log = round(float(r["rarity_mean_log"]) * 100.0, 1)
        sum_log = round(float(r["rarity_sum_log"]), 1)
        excl_pct = round(excl_n / streets_n * 100.0, 1)

        # Mathematical invariants
        assert 0 <= excl_n <= streets_n, f"pure_exclusive > streets in {code}:{r['name']}"
        assert sum_sqrt <= streets_n + 0.2, f"sum_sqrt > streets in {code}:{r['name']}"
        assert 0.0 <= mean_sqrt <= 100.0
        assert 0.0 <= mean_idf <= 100.0
        assert 0.0 <= mean_log <= 100.0

        uniq_disp, common_disp = city_samples.get(cid, ([], []))
        cities_out.append({
            "id": cid,
            "country": code,
            "name": str(r["name"]).strip(),
            "local_name": str(r["local_name"]).strip() if pd.notna(r["local_name"]) else None,
            "region": str(r["region"]).strip() if pd.notna(r["region"]) else None,
            "population": int(r["population"]) if pd.notna(r["population"]) else 0,
            "lat": round(float(r["lat"]), 5),
            "lon": round(float(r["lon"]), 5),
            "streets": streets_n,
            "pure_exclusive": excl_n,
            "exclusive_pct": excl_pct,
            "numbered_pct": numbered_pct,
            "rarity_mean_sqrt": mean_sqrt,
            "rarity_sum_sqrt": sum_sqrt,
            "rarity_mean_idf": mean_idf,
            "rarity_sum_idf": sum_idf,
            "rarity_mean_log": mean_log,
            "rarity_sum_log": sum_log,
            "sample_unique": uniq_disp,
            "sample_common": common_disp,
        })

    # Sort by default metric: rarity_mean_sqrt descending
    cities_out.sort(key=lambda x: (-x["rarity_mean_sqrt"], -x["streets"], x["name"]))

    # Build compact inverted search index for street queries (all shared streets n >= 2)
    city_index_map = {c["id"]: idx for idx, c in enumerate(cities_out)}
    city_names_ordered = [c["name"] for c in cities_out]
    street_to_idxs: dict[str, list[int]] = defaultdict(list)
    for cid, k in zip(dedup["city_id"].tolist(), dedup["norm_key"].tolist()):
        idx = city_index_map.get(str(cid))
        if idx is not None:
            street_to_idxs[k].append(idx)

    # Include all streets appearing in >= 2 cities (compact and covers every shared street!)
    search_streets: dict[str, list[Any]] = {}
    for k, idxs in street_to_idxs.items():
        if len(idxs) >= 2:
            uniq_idxs = sorted(set(idxs))
            search_streets[k] = [canon_display[k], len(uniq_idxs), uniq_idxs]

    search_index = {
        "country": code,
        "cities_count": len(cities_out),
        "cities": city_names_ordered,
        "streets": search_streets,
    }

    # Top 15 most common streets in this country
    top_shared_keys = n_by_key.sort_values(ascending=False).head(15)
    top_shared = [
        {
            "key": str(k),
            "name": canon_display[str(k)],
            "n_cities": int(cnt),
            "pct_cities": round(int(cnt) / n_cities * 100.0, 1),
            "weight_sqrt": round(1.0 / math.sqrt(int(cnt)), 3),
            "weight_idf": round(1.0 - math.log(int(cnt)) / log_c, 3),
            "weight_log": round(1.0 / math.log2(1 + int(cnt)), 3),
        }
        for k, cnt in top_shared_keys.items()
    ]

    sqrt_vals = [c["rarity_mean_sqrt"] for c in cities_out]
    idf_vals = [c["rarity_mean_idf"] for c in cities_out]
    log_vals = [c["rarity_mean_log"] for c in cities_out]
    excl_vals = [c["exclusive_pct"] for c in cities_out]
    num_vals = [c["numbered_pct"] for c in cities_out]

    country_summary = {
        "code": code,
        "name": country_cfg["name"],
        "color": country_cfg["color"],
        "center": country_cfg["center"],
        "zoom": country_cfg["zoom"],
        "bbox": country_cfg["bbox"],
        "languages": langs,
        "cities_count": n_cities,
        "total_city_streets": int(len(dedup)),
        "unique_norm_streets": int(len(n_by_key)),
        "pure_exclusive_streets": int((n_by_key == 1).sum()),
        "searchable_shared_streets": len(search_streets),
        "raw_valid_rows": raw_valid_rows,
        "mean_rarity_sqrt": round(float(np.mean(sqrt_vals)), 1),
        "median_rarity_sqrt": round(float(np.median(sqrt_vals)), 1),
        "mean_rarity_idf": round(float(np.mean(idf_vals)), 1),
        "median_rarity_idf": round(float(np.median(idf_vals)), 1),
        "mean_rarity_log": round(float(np.mean(log_vals)), 1),
        "median_rarity_log": round(float(np.median(log_vals)), 1),
        "mean_exclusive_pct": round(float(np.mean(excl_vals)), 1),
        "mean_numbered_pct": round(float(np.mean(num_vals)), 2),
        "most_unique_city": {"name": cities_out[0]["name"], "score_sqrt": cities_out[0]["rarity_mean_sqrt"], "streets": cities_out[0]["streets"]},
        "most_generic_city": {"name": cities_out[-1]["name"], "score_sqrt": cities_out[-1]["rarity_mean_sqrt"], "streets": cities_out[-1]["streets"]},
        "top_shared": top_shared,
    }

    street_prevalence = {str(k): (canon_display[str(k)], int(cnt)) for k, cnt in n_by_key.items() if int(cnt) >= 3}
    print(
        f"[{code}] Done: {n_cities} cities, {len(n_by_key):,} unique streets, "
        f"mean_sqrt={country_summary['mean_rarity_sqrt']}%, median_sqrt={country_summary['median_rarity_sqrt']}%, "
        f"top={top_shared[0]['name']} ({top_shared[0]['n_cities']})",
        flush=True,
    )
    return country_summary, cities_out, search_index, street_prevalence


def main() -> None:
    parser = argparse.ArgumentParser(description="Build World Street Rarity datasets.")
    parser.add_argument("--countries", default="", help="Comma-separated ISO2 country codes (default: all configured)")
    parser.add_argument("--force-extract", action="store_true", help="Ignore local Parquet cache and re-query Overture S3")
    args = parser.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    release = cfg["overture_release"]
    min_pop = cfg["defaults"]["min_population"]
    min_streets = cfg["defaults"]["min_streets"]
    lang_rules = load_lang_rules()

    selected_codes = {c.strip().upper() for c in args.countries.split(",") if c.strip()}
    country_cfgs = [c for c in cfg["countries"] if not selected_codes or c["code"] in selected_codes]
    if not country_cfgs:
        raise SystemExit(f"No matching countries for: {args.countries}")

    (DATA_DIR / "countries").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "search").mkdir(parents=True, exist_ok=True)

    # Preserve existing summary entries if running a subset of countries
    summary_path = DATA_DIR / "summary.json"
    existing_summaries: dict[str, dict[str, Any]] = {}
    if selected_codes and summary_path.exists():
        old = json.loads(summary_path.read_text(encoding="utf-8"))
        for cs in old.get("countries", []):
            existing_summaries[cs["code"]] = cs

    all_prevalence: dict[str, dict[str, tuple[str, int]]] = {}
    for c_cfg in country_cfgs:
        cs, cities_out, search_idx, prev = process_country(
            c_cfg, release, min_pop, min_streets, lang_rules, force_extract=args.force_extract
        )
        code = c_cfg["code"]
        existing_summaries[code] = cs
        all_prevalence[code] = prev

        # Write compact JSON per country
        (DATA_DIR / "countries" / f"{code}.json").write_text(
            json.dumps(cities_out, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        (DATA_DIR / "search" / f"{code}.json").write_text(
            json.dumps(search_idx, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    # Order country summaries according to config order
    cfg_order = {c["code"]: i for i, c in enumerate(cfg["countries"])}
    ordered_summaries = sorted(existing_summaries.values(), key=lambda x: cfg_order.get(x["code"], 999))

    # Cross-country shared names (names appearing in >= 3 cities in >= 3 pilot countries)
    cross_hits: dict[str, dict[str, int]] = defaultdict(dict)
    cross_disp: dict[str, str] = {}
    for code, prev in all_prevalence.items():
        for k, (disp, cnt) in prev.items():
            if len(k) >= 4:
                cross_hits[k][code] = cnt
                if k not in cross_disp:
                    cross_disp[k] = disp

    cross_shared = [
        {
            "key": k,
            "name": cross_disp[k],
            "countries_count": len(cmap),
            "total_cities": sum(cmap.values()),
            "by_country": cmap,
        }
        for k, cmap in cross_hits.items()
        if len(cmap) >= 3
    ]
    cross_shared.sort(key=lambda x: (-x["countries_count"], -x["total_cities"], x["key"]))

    summary = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "overture_release": release,
        "default_metric": "sqrt",
        "min_population": min_pop,
        "min_streets": min_streets,
        "total_countries": len(ordered_summaries),
        "total_cities": sum(c["cities_count"] for c in ordered_summaries),
        "countries": ordered_summaries,
        "cross_country_shared": cross_shared[:25],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {summary_path} ({summary['total_countries']} countries, {summary['total_cities']:,} cities)")


if __name__ == "__main__":
    main()
