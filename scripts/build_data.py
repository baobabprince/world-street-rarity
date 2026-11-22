#!/usr/bin/env python3
"""Build World Street Rarity static JSON files for configured pilot countries.

Default primary metric: square-root rarity (1 / sqrt(n)).
Includes dual-carriageway-corrected street lengths, active built-up urban area (250m grid),
municipal area, length-weighted rarity (Boulevard Cliché Gap), and power-law scaling fits.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _power_law_fit(x: pd.Series, y: pd.Series) -> dict[str, float] | None:
    """Fit log10(y) = a + exp * log10(x) on positive finite pairs; return exponent and Pearson r."""
    mask = (x > 0) & (y > 0) & x.notna() & y.notna()
    if int(mask.sum()) < 5:
        return None
    lx = np.log10(x[mask].astype(float).to_numpy())
    ly = np.log10(y[mask].astype(float).to_numpy())
    exp, _ = np.polyfit(lx, ly, 1)
    r = float(np.corrcoef(lx, ly)[0, 1])
    return {"exponent": round(float(exp), 3), "r": round(r, 3)}


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    mask = (x > 0) & x.notna() & y.notna()
    if int(mask.sum()) < 5:
        return None
    return round(float(x[mask].rank().corr(y[mask].rank())), 3)


def _drop_nested_overlapping_cities(
    cities_df: pd.DataFrame,
    dedup: pd.DataFrame,
    raw_streets_df: pd.DataFrame,
    overlap_threshold: float = 0.88,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Drop sub-districts/boroughs/duplicate polygons nested inside a larger overlapping city polygon.

    In Overture Maps, some metro areas (e.g. Belgrade, Zagreb, Warsaw, Long Island towns,
    or duplicate Beer Sheva polygons) have overlapping child `locality` polygons inside a
    parent `locality` polygon, which double-counts every street inside the child and artificially
    collapses exclusive_pct to 0%. Using exact raw street_name containment (>=88% within ~65km)
    cleanly catches true spatial containment without dropping independent neighboring towns
    that merely share common normalized names (e.g. Kiryat Motzkin near Haifa).
    """
    valid_cids = set(cities_df["city_id"].tolist())
    raw_sub = raw_streets_df[raw_streets_df["city_id"].isin(valid_cids)]
    street_sets: dict[Any, set[str]] = (
        raw_sub.groupby("city_id")["street_name"].apply(set).to_dict()
    )
    meta = cities_df.set_index("city_id")[["name", "population", "lat", "lon"]].to_dict("index")
    ordered_ids = sorted(
        street_sets.keys(),
        key=lambda cid: (len(street_sets[cid]), float(meta.get(cid, {}).get("population") or 0)),
        reverse=True,
    )

    kept: list[tuple[Any, float, float, set[str]]] = []
    dropped_ids: set[Any] = set()
    dropped_names: list[str] = []

    for cid in ordered_ids:
        m = meta.get(cid)
        if not m:
            continue
        lat = float(m["lat"])
        lon = float(m["lon"])
        sset = street_sets[cid]
        if not sset:
            continue

        is_nested = False
        for _, plat, plon, pset in kept:
            if abs(lat - plat) <= 0.6 and abs(lon - plon) <= 0.85:
                shared = len(sset & pset)
                if shared / len(sset) >= overlap_threshold:
                    is_nested = True
                    break
        if is_nested:
            dropped_ids.add(cid)
            dropped_names.append(str(m["name"]))
        else:
            kept.append((cid, lat, lon, sset))

    if dropped_ids:
        cities_df = cities_df[~cities_df["city_id"].isin(dropped_ids)].copy()
        dedup = dedup[~dedup["city_id"].isin(dropped_ids)].copy()
    return cities_df, dedup, dropped_names


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
    streets_df["len_m"] = pd.to_numeric(streets_df.get("len_m", 0.0), errors="coerce").fillna(0.0).clip(lower=0.0)

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

    numbered_per_city = streets_df.groupby("city_id")["is_numbered"].sum().to_dict()

    # Keep valid non-numbered normalized streets & sum adjusted lengths across merged raw variants per city!
    valid = streets_df[streets_df["norm_key"].str.len() > 0].copy()
    raw_valid_rows = len(valid)
    dedup = (
        valid.groupby(["city_id", "norm_key"], as_index=False)
        .agg(len_m=("len_m", "sum"), display=("display", "first"))
    )

    # Keep only cities with >= min_streets normalized streets
    city_counts = dedup.groupby("city_id")["norm_key"].count()
    kept_ids = set(city_counts[city_counts >= min_streets].index)
    dedup = dedup[dedup["city_id"].isin(kept_ids)].copy()
    cities_df = cities_df[cities_df["city_id"].isin(kept_ids)].copy()

    # Remove nested overlapping sub-districts / duplicate Overture polygons (e.g. Zemun inside Belgrade)
    cities_df, dedup, dropped_nested = _drop_nested_overlapping_cities(cities_df, dedup, streets_df)
    if dropped_nested:
        sample_drop = ", ".join(dropped_nested[:6])
        more = f" (+{len(dropped_nested) - 6} more)" if len(dropped_nested) > 6 else ""
        print(f"[{code}] Dropped {len(dropped_nested)} nested/duplicate overlapping localities: {sample_drop}{more}", flush=True)

    n_cities = len(cities_df)
    if n_cities == 0:
        raise RuntimeError(f"No cities remained for {code} after filtering!")

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
    dedup["lw_sqrt"] = dedup["len_m"] * dedup["w_sqrt"]

    # Length by rarity tier (Boulevard Cliché test)
    tier_bins = [0, 1, 5, 20, 100, 10**9]
    tier_labels = ["Unique (n=1)", "Rare (2-5)", "Medium (6-20)", "Common (21-100)", "Iconic (>100)"]
    dedup["tier"] = pd.cut(dedup["n"], bins=tier_bins, labels=tier_labels)
    length_by_tier: list[dict[str, Any]] = []
    for label in tier_labels:
        sub_len = dedup.loc[dedup["tier"] == label, "len_m"]
        if len(sub_len) > 0:
            length_by_tier.append({
                "tier": label,
                "count": int(len(sub_len)),
                "median_m": round(float(sub_len.median()), 1),
                "mean_m": round(float(sub_len.mean()), 1),
            })

    per = dedup.groupby("city_id").agg(
        streets=("norm_key", "count"),
        pure_exclusive=("n", lambda s: int((s == 1).sum())),
        rarity_sum_sqrt=("w_sqrt", "sum"),
        rarity_mean_sqrt=("w_sqrt", "mean"),
        rarity_sum_idf=("w_idf", "sum"),
        rarity_mean_idf=("w_idf", "mean"),
        rarity_sum_log=("w_log", "sum"),
        rarity_mean_log=("w_log", "mean"),
        total_len_m=("len_m", "sum"),
        lw_sqrt_sum=("lw_sqrt", "sum"),
        median_street_m=("len_m", "median"),
        mean_street_m=("len_m", "mean"),
    ).reset_index()

    merged = per.merge(cities_df, on="city_id", how="inner")

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

        pop = int(r["population"]) if pd.notna(r["population"]) else 0
        muni_area_km2 = round(float(r["muni_area_km2"]), 2) if pd.notna(r["muni_area_km2"]) and float(r["muni_area_km2"]) > 0 else None
        built_area_km2 = round(float(r["built_area_km2"]), 2) if pd.notna(r["built_area_km2"]) and float(r["built_area_km2"]) > 0 else muni_area_km2
        built_share_pct = round(min(100.0, built_area_km2 / muni_area_km2 * 100.0), 1) if (built_area_km2 and muni_area_km2) else None

        total_len_m = float(r["total_len_m"])
        total_street_km = round(total_len_m / 1000.0, 2)
        med_m = round(float(r["median_street_m"]), 1)
        mean_m = round(float(r["mean_street_m"]), 1)

        mean_sqrt = round(float(r["rarity_mean_sqrt"]) * 100.0, 1)
        sum_sqrt = round(float(r["rarity_sum_sqrt"]), 1)
        mean_idf = round(float(r["rarity_mean_idf"]) * 100.0, 1)
        sum_idf = round(float(r["rarity_sum_idf"]), 1)
        mean_log = round(float(r["rarity_mean_log"]) * 100.0, 1)
        sum_log = round(float(r["rarity_sum_log"]), 1)
        excl_pct = round(excl_n / streets_n * 100.0, 1)

        rarity_len_sqrt = round((float(r["lw_sqrt_sum"]) / total_len_m) * 100.0, 1) if total_len_m > 0 else mean_sqrt
        cliche_gap = round(mean_sqrt - rarity_len_sqrt, 1)

        # Primary density metrics use ACTIVE BUILT-UP URBAN AREA (built_area_km2)
        pop_density = round(pop / built_area_km2, 1) if built_area_km2 and pop > 0 else None
        muni_pop_density = round(pop / muni_area_km2, 1) if muni_area_km2 and pop > 0 else None
        street_km_per_km2 = round(total_street_km / built_area_km2, 2) if built_area_km2 else None
        pop_per_street = round(pop / streets_n, 1) if pop > 0 else None
        pop_per_street_km = round(pop / total_street_km, 1) if total_street_km > 0 and pop > 0 else None

        assert 0 <= excl_n <= streets_n, f"pure_exclusive > streets in {code}:{r['name']}"
        assert sum_sqrt <= streets_n + 0.2, f"sum_sqrt > streets in {code}:{r['name']}"
        assert 0.0 <= mean_sqrt <= 100.0
        assert 0.0 <= rarity_len_sqrt <= 100.0
        assert 0.0 <= mean_idf <= 100.0
        assert 0.0 <= mean_log <= 100.0

        uniq_disp, common_disp = city_samples.get(cid, ([], []))
        cities_out.append({
            "id": cid,
            "country": code,
            "name": str(r["name"]).strip(),
            "local_name": str(r["local_name"]).strip() if pd.notna(r["local_name"]) else None,
            "region": str(r["region"]).strip() if pd.notna(r["region"]) else None,
            "population": pop,
            "built_area_km2": built_area_km2,
            "muni_area_km2": muni_area_km2,
            "built_share_pct": built_share_pct,
            "pop_density": pop_density,
            "muni_pop_density": muni_pop_density,
            "lat": round(float(r["lat"]), 5),
            "lon": round(float(r["lon"]), 5),
            "streets": streets_n,
            "pure_exclusive": excl_n,
            "exclusive_pct": excl_pct,
            "numbered_pct": numbered_pct,
            "total_street_km": total_street_km,
            "street_km_per_km2": street_km_per_km2,
            "median_street_m": med_m,
            "mean_street_m": mean_m,
            "pop_per_street": pop_per_street,
            "pop_per_street_km": pop_per_street_km,
            "rarity_mean_sqrt": mean_sqrt,
            "rarity_len_sqrt": rarity_len_sqrt,
            "cliche_gap": cliche_gap,
            "rarity_sum_sqrt": sum_sqrt,
            "rarity_mean_idf": mean_idf,
            "rarity_sum_idf": sum_idf,
            "rarity_mean_log": mean_log,
            "rarity_sum_log": sum_log,
            "sample_unique": uniq_disp,
            "sample_common": common_disp,
        })

    cities_out.sort(key=lambda x: (-x["rarity_mean_sqrt"], -x["streets"], x["name"]))

    city_index_map = {c["id"]: idx for idx, c in enumerate(cities_out)}
    city_names_ordered = [c["name"] for c in cities_out]
    street_to_idxs: dict[str, list[int]] = defaultdict(list)
    for cid, k in zip(dedup["city_id"].tolist(), dedup["norm_key"].tolist()):
        idx = city_index_map.get(str(cid))
        if idx is not None:
            street_to_idxs[k].append(idx)

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

    cdf = pd.DataFrame(cities_out)
    scaling = {
        "streets_vs_built_area": _power_law_fit(cdf["built_area_km2"], cdf["streets"]),
        "pop_vs_built_area": _power_law_fit(cdf["built_area_km2"], cdf["population"]),
        "streets_vs_muni_area": _power_law_fit(cdf["muni_area_km2"], cdf["streets"]),
        "streets_vs_pop": _power_law_fit(cdf["population"], cdf["streets"]),
        "street_km_vs_built_area": _power_law_fit(cdf["built_area_km2"], cdf["total_street_km"]),
    }

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
        "mean_rarity_sqrt": round(float(cdf["rarity_mean_sqrt"].mean()), 1),
        "median_rarity_sqrt": round(float(cdf["rarity_mean_sqrt"].median()), 1),
        "median_rarity_len_sqrt": round(float(cdf["rarity_len_sqrt"].median()), 1),
        "median_cliche_gap": round(float(cdf["cliche_gap"].median()), 1),
        "pct_longer_more_generic": round(float((cdf["cliche_gap"] > 0).mean() * 100.0), 1),
        "mean_rarity_idf": round(float(cdf["rarity_mean_idf"].mean()), 1),
        "median_rarity_idf": round(float(cdf["rarity_mean_idf"].median()), 1),
        "mean_rarity_log": round(float(cdf["rarity_mean_log"].mean()), 1),
        "median_rarity_log": round(float(cdf["rarity_mean_log"].median()), 1),
        "mean_exclusive_pct": round(float(cdf["exclusive_pct"].mean()), 1),
        "mean_numbered_pct": round(float(cdf["numbered_pct"].mean()), 2),
        "median_built_area_km2": round(float(cdf["built_area_km2"].dropna().median()), 1),
        "median_muni_area_km2": round(float(cdf["muni_area_km2"].dropna().median()), 1),
        "median_built_share_pct": round(float(cdf["built_share_pct"].dropna().median()), 1),
        "median_pop_density": round(float(cdf["pop_density"].dropna().median()), 1),
        "median_street_km_per_km2": round(float(cdf["street_km_per_km2"].dropna().median()), 2),
        "median_pop_per_street": round(float(cdf["pop_per_street"].dropna().median()), 1),
        "median_pop_per_street_km": round(float(cdf["pop_per_street_km"].dropna().median()), 1),
        "median_street_m": round(float(cdf["median_street_m"].median()), 1),
        "mean_street_m": round(float(cdf["mean_street_m"].median()), 1),
        "spearman_built_area_rarity": _spearman(cdf["built_area_km2"], cdf["rarity_mean_sqrt"]),
        "spearman_muni_area_rarity": _spearman(cdf["muni_area_km2"], cdf["rarity_mean_sqrt"]),
        "scaling": scaling,
        "length_by_tier": length_by_tier,
        "most_unique_city": {"name": cities_out[0]["name"], "score_sqrt": cities_out[0]["rarity_mean_sqrt"], "streets": cities_out[0]["streets"]},
        "most_generic_city": {"name": cities_out[-1]["name"], "score_sqrt": cities_out[-1]["rarity_mean_sqrt"], "streets": cities_out[-1]["streets"]},
        "top_shared": top_shared,
    }

    street_prevalence = {str(k): (canon_display[str(k)], int(cnt)) for k, cnt in n_by_key.items() if int(cnt) >= 3}
    sa = scaling["streets_vs_built_area"]
    pa = scaling["pop_vs_built_area"]
    print(
        f"[{code}] Done: {n_cities} cities | built={country_summary['median_built_area_km2']}km² (muni {country_summary['median_muni_area_km2']}km², {country_summary['median_built_share_pct']}%) | "
        f"med_len={country_summary['median_street_m']}m | gap={country_summary['median_cliche_gap']:+.1f}% | "
        f"streets~built^{sa['exponent'] if sa else '?'} (r={sa['r'] if sa else '?'}) | "
        f"pop~built^{pa['exponent'] if pa else '?'} (r={pa['r'] if pa else '?'})",
        flush=True,
    )
    return country_summary, cities_out, search_index, street_prevalence


def main() -> None:
    parser = argparse.ArgumentParser(description="Build World Street Rarity datasets.")
    parser.add_argument("--countries", default="", help="Comma-separated ISO2 country codes (default: all configured)")
    parser.add_argument("--force-extract", action="store_true", help="Ignore local Parquet cache and re-query Overture S3")
    parser.add_argument("--workers", type=int, default=1, help="Parallel S3 extraction workers (default: 1)")
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

    summary_path = DATA_DIR / "summary.json"
    existing_summaries: dict[str, dict[str, Any]] = {}
    existing_cross_shared: list[dict[str, Any]] = []
    if selected_codes and summary_path.exists():
        old = json.loads(summary_path.read_text(encoding="utf-8"))
        for cs in old.get("countries", []):
            existing_summaries[cs["code"]] = cs
        existing_cross_shared = old.get("cross_country_shared", [])

    # Pre-extract any uncached countries concurrently via ThreadPoolExecutor (each uses its own DuckDB connection)
    if args.workers > 1 and len(country_cfgs) > 1:
        def _extract_one(c_cfg: dict[str, Any]) -> str:
            extract_country(c_cfg, release=release, min_population=min_pop, force=args.force_extract)
            return c_cfg["code"]

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(_extract_one, c_cfg) for c_cfg in country_cfgs]
            for fut in as_completed(futs):
                fut.result()

    all_prevalence: dict[str, dict[str, tuple[str, int]]] = {}
    for c_cfg in country_cfgs:
        cs, cities_out, search_idx, prev = process_country(
            c_cfg, release, min_pop, min_streets, lang_rules, force_extract=False
        )
        code = c_cfg["code"]
        existing_summaries[code] = cs
        all_prevalence[code] = prev

        (DATA_DIR / "countries" / f"{code}.json").write_text(
            json.dumps(cities_out, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        (DATA_DIR / "search" / f"{code}.json").write_text(
            json.dumps(search_idx, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    cfg_order = {c["code"]: i for i, c in enumerate(cfg["countries"])}
    ordered_summaries = sorted(existing_summaries.values(), key=lambda x: cfg_order.get(x["code"], 999))

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
        "cross_country_shared": existing_cross_shared if selected_codes and existing_cross_shared else cross_shared[:25],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {summary_path} ({summary['total_countries']} countries, {summary['total_cities']:,} cities)")


if __name__ == "__main__":
    main()
