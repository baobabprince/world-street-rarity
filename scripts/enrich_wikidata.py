#!/usr/bin/env python3
"""Enrich World Street Rarity cities with founding / earliest-record years from Wikidata.

1. Maps each city's Overture division `id` to its `wikidata` QID in a single DuckDB scan.
2. Fetches P571 (inception) & P1249 (earliest written record) in batches of 50 via Wikidata API.
3. Writes `.cache/wikidata_founded.json` and injects `founded_year` & `founded_type` into
   `data/countries/*.json` and `data/summary.json` (plus `updated_at_iso` timestamp).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
CACHE_DIR = ROOT / ".cache"
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "countries.json"
QID_CACHE = CACHE_DIR / "wikidata_qids.json"
FOUNDED_CACHE = CACHE_DIR / "wikidata_founded.json"

_TIME_RE = re.compile(r"^([+-])(\d{1,5})-")


def _parse_wikidata_year(claim_list: list[dict[str, Any]]) -> int | None:
    years: list[int] = []
    for cl in claim_list:
        mainsnak = cl.get("mainsnak") or {}
        dv = mainsnak.get("datavalue") or {}
        if dv.get("type") != "time":
            continue
        tval = (dv.get("value") or {}).get("time", "")
        m = _TIME_RE.match(tval)
        if not m:
            continue
        sign, ystr = m.group(1), m.group(2)
        yr = int(ystr)
        if sign == "-":
            yr = -yr
        if -5000 <= yr <= 2026 and yr != 0:
            years.append(yr)
    return min(years) if years else None


def fetch_overture_qids(release: str, needed_ids: set[str], countries: list[str]) -> dict[str, str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached: dict[str, str] = {}
    if QID_CACHE.exists():
        cached = json.loads(QID_CACHE.read_text(encoding="utf-8"))
    missing = needed_ids - set(cached.keys())
    if not missing:
        print(f"All {len(needed_ids):,} city QIDs already cached!", flush=True)
        return cached

    div_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division/*"
    cc_sql = ", ".join(f"'{c}'" for c in sorted(set(countries)))
    print(f"Fetching Wikidata QIDs from Overture ({release}) for {len(missing):,} cities across {len(set(countries))} countries...", flush=True)
    t0 = time.time()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; SET threads=4;")
    rows = con.execute(
        f"""
        SELECT id, wikidata
        FROM read_parquet('{div_url}')
        WHERE country IN ({cc_sql})
          AND subtype IN ('locality', 'localadmin', 'county', 'region')
          AND wikidata IS NOT NULL
        """
    ).fetchall()
    con.close()

    matched = 0
    for cid, qid in rows:
        if cid in needed_ids and qid:
            cached[cid] = qid
            matched += 1
    QID_CACHE.write_text(json.dumps(cached, separators=(",", ":")), encoding="utf-8")
    print(f"Matched {matched:,}/{len(needed_ids):,} cities ({matched/max(1,len(needed_ids))*100:.1f}%) to Wikidata QIDs in {time.time()-t0:.1f}s", flush=True)
    return cached


def _fetch_batch(qids: list[str]) -> dict[str, tuple[int | None, str | None]]:
    url = f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={'|'.join(qids)}&props=claims&format=json"
    headers = {"User-Agent": "WorldStreetRarity/1.0 (https://github.com/baobabprince/world-street-rarity)"}
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out: dict[str, tuple[int | None, str | None]] = {}
            for qid in qids:
                ent = (data.get("entities") or {}).get(qid, {})
                claims = ent.get("claims") or {}
                y_inc = _parse_wikidata_year(claims.get("P571", []))
                y_rec = _parse_wikidata_year(claims.get("P1249", []))
                # Prefer earliest historical date when both exist (avoids 1970s municipal-merger P571 hiding medieval origin)
                if y_inc is not None and y_rec is not None:
                    if y_rec < y_inc:
                        out[qid] = (y_rec, "earliest_record")
                    else:
                        out[qid] = (y_inc, "founded")
                elif y_inc is not None:
                    out[qid] = (y_inc, "founded")
                elif y_rec is not None:
                    out[qid] = (y_rec, "earliest_record")
                else:
                    out[qid] = (None, None)
            return out
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(1.5 * (attempt + 1))
    return {q: (None, None) for q in qids}


def fetch_wikidata_years(qids: set[str]) -> dict[str, list[Any]]:
    cached: dict[str, list[Any]] = {}
    if FOUNDED_CACHE.exists():
        cached = json.loads(FOUNDED_CACHE.read_text(encoding="utf-8"))
    missing = sorted(qids - set(cached.keys()))
    if not missing:
        print(f"All {len(qids):,} Wikidata founding records already cached!", flush=True)
        return cached

    batches = [missing[i : i + 50] for i in range(0, len(missing), 50)]
    print(f"Querying Wikidata API for {len(missing):,} QIDs in {len(batches)} batches (4 workers)...", flush=True)
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_fetch_batch, b) for b in batches]
        for fut in as_completed(futs):
            res = fut.result()
            for q, (yr, ftype) in res.items():
                cached[q] = [yr, ftype]
            done += 1
            if done % 30 == 0 or done == len(batches):
                print(f"  Completed {done}/{len(batches)} batches ({time.time()-t0:.1f}s)...", flush=True)
    FOUNDED_CACHE.write_text(json.dumps(cached, separators=(",", ":")), encoding="utf-8")
    return cached


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    s1 = pd.Series(xs)
    s2 = pd.Series(ys)
    val = s1.rank().corr(s2.rank())
    return round(float(val), 3) if pd.notna(val) else None


def main() -> None:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    release = cfg["overture_release"]
    summary_path = DATA_DIR / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    overture_countries = [c.get("overture_country", c["code"]) for c in cfg["countries"]]
    all_city_ids: set[str] = set()
    cities_by_cc: dict[str, list[dict[str, Any]]] = {}
    for cs in summary["countries"]:
        code = cs["code"]
        cpath = DATA_DIR / "countries" / f"{code}.json"
        clist = json.loads(cpath.read_text(encoding="utf-8"))
        cities_by_cc[code] = clist
        for c in clist:
            all_city_ids.add(c["id"])

    qid_map = fetch_overture_qids(release, all_city_ids, overture_countries)
    needed_qids = {qid_map[cid] for cid in all_city_ids if cid in qid_map}
    founded_map = fetch_wikidata_years(needed_qids)

    total_with_year = 0
    total_cities = 0
    print("\n=== FOUNDING YEAR COVERAGE & RARITY CORRELATION BY COUNTRY ===")
    print(f"{'CC':4} {'Name':22} {'Cities':>6} {'HasYr':>6} {'Cov%':>6} {'MedYr':>6} {'rho(Yr,Rar)':>11}")
    print("-" * 70)

    for cs in summary["countries"]:
        code = cs["code"]
        clist = cities_by_cc[code]
        xs, ys = [], []
        yrs = []
        for c in clist:
            qid = qid_map.get(c["id"])
            yr, ftype = founded_map.get(qid, [None, None]) if qid else (None, None)
            c["founded_year"] = yr
            c["founded_type"] = ftype
            if yr is not None:
                yrs.append(yr)
                xs.append(float(yr))
                ys.append(float(c["rarity_mean_sqrt"]))

        cov_pct = round(len(yrs) / max(1, len(clist)) * 100.0, 1)
        rho = _spearman(xs, ys)
        med_yr = int(round(float(pd.Series(yrs).median()))) if yrs else None
        cs["founded_coverage_pct"] = cov_pct
        cs["median_founded_year"] = med_yr
        cs["spearman_founded_rarity"] = rho
        total_with_year += len(yrs)
        total_cities += len(clist)

        (DATA_DIR / "countries" / f"{code}.json").write_text(
            json.dumps(clist, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        rho_s = f"{rho:+.3f}" if rho is not None else "   —  "
        med_s = f"{med_yr:6d}" if med_yr is not None else "     —"
        print(f"{code:4} {cs['name'][:22]:22} {len(clist):6d} {len(yrs):6d} {cov_pct:5.1f}% {med_s} {rho_s:>11}")

    now_utc = datetime.now(timezone.utc)
    summary["updated_at"] = now_utc.strftime("%Y-%m-%d")
    summary["updated_at_iso"] = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    summary["total_with_founded_year"] = total_with_year
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nDONE: {total_with_year:,}/{total_cities:,} cities ({total_with_year/max(1,total_cities)*100:.1f}%) enriched with Wikidata founding/earliest-record years!")


if __name__ == "__main__":
    main()
