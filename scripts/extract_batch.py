#!/usr/bin/env python3
"""Fast single-connection batched Overture Maps extractor for multiple countries.

Scans Overture `division` and `division_area` ONCE across all requested countries,
then scans `transportation/segment` once per compact geographic tile (~4 tiles for
all of Europe instead of 29 separate full S3 scans), writing standard v3 Parquet cache
files compatible with `scripts/build_data.py`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import duckdb

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
CACHE_DIR = ROOT / ".cache"
CONFIG_PATH = ROOT / "config" / "countries.json"

URBAN_ROAD_CLASSES = (
    "'primary', 'secondary', 'tertiary', 'residential', "
    "'unclassified', 'living_street', 'pedestrian'"
)

ONEWAY_EXPR = (
    "list_bool_or(list_transform(COALESCE(s.access_restrictions, []), "
    "lambda r: r.access_type = 'denied' AND r.\"when\".heading IN ('backward', 'forward')))"
)

# Compact non-bloated geographic tiles so each S3 segment scan touches only local row groups
TILES: list[tuple[str, list[str]]] = [
    ("UK-IE-Benelux", ["GB", "IE", "NL", "BE", "LU"]),
    ("Portugal", ["PT"]),
    ("Central-Europe", ["AT", "CZ", "SK", "HU", "SI"]),
    ("Nordics-Baltics", ["DK", "SE", "NO", "FI", "EE", "LV", "LT"]),
    ("Iceland", ["IS"]),
    ("SouthEast-Europe", ["RO", "BG", "GR", "AL", "XK"]),
    ("Cyprus", ["CY"]),
    ("Malta", ["MT"]),
    ("East-Europe", ["UA", "BY", "MD"]),
]


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "INSTALL spatial; LOAD spatial; "
        "INSTALL httpfs; LOAD httpfs; "
        "SET s3_region='us-west-2'; "
        "SET geometry_always_xy = true; "
        "SET threads = 4;"
    )
    return con


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    release = cfg["overture_release"]
    min_pop = cfg["defaults"]["min_population"]
    cfg_by_code: dict[str, dict[str, Any]] = {c["code"]: c for c in cfg["countries"]}

    # Filter to uncached countries from TILES (or CLI args if given)
    cli_codes = {a.upper() for a in sys.argv[1:]}
    needed_tiles: list[tuple[str, list[dict[str, Any]]]] = []
    all_needed: list[dict[str, Any]] = []
    for tname, codes in TILES:
        t_cfgs = []
        for code in codes:
            if cli_codes and code not in cli_codes:
                continue
            if code not in cfg_by_code:
                continue
            cpq = CACHE_DIR / f"{code}_{release}_cities_v3.parquet"
            spq = CACHE_DIR / f"{code}_{release}_streets_v3.parquet"
            if cpq.exists() and spq.exists():
                print(f"[{code}] Already cached, skipping", flush=True)
                continue
            t_cfgs.append(cfg_by_code[code])
            all_needed.append(cfg_by_code[code])
        if t_cfgs:
            needed_tiles.append((tname, t_cfgs))

    if not all_needed:
        print("All countries already cached!", flush=True)
        return

    div_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division/*"
    area_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*"
    seg_url = f"s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment/*"

    con = _connect()
    t0 = time.time()
    all_codes_sql = ", ".join(f"'{c['code']}'" for c in all_needed)
    print(f"Step 1/2: Extracting divisions & polygons ONCE for {len(all_needed)} countries...", flush=True)

    region_expr = (
        "COALESCE("
        "  list_extract(list_transform(list_filter(d.hierarchies[1], lambda h: h.subtype = 'region'), lambda h: h.name), 1),"
        "  d.region"
        ")"
    )

    con.execute(
        f"""
        CREATE TEMP TABLE all_locs AS
        SELECT d.country, d.id,
               COALESCE(d.names.common['en'], d.names.primary) AS name,
               d.names.primary AS local_name,
               d.names.common['en'] AS en_name,
               {region_expr} AS region_name,
               d.population,
               ST_Y(d.geometry) AS pt_lat,
               ST_X(d.geometry) AS pt_lon,
               d.geometry,
               d.bbox
        FROM read_parquet('{div_url}') d
        WHERE d.country IN ({all_codes_sql})
          AND d.subtype = 'locality'
          AND d.population >= {min_pop}
        """
    )
    n_locs = con.execute("SELECT COUNT(*) FROM all_locs").fetchone()[0]
    print(f"  Loaded {n_locs:,} localities (pop>={min_pop}) in {time.time() - t0:.1f}s", flush=True)

    t_area = time.time()
    con.execute(
        f"""
        CREATE TEMP TABLE all_areas AS
        SELECT a.country, a.subtype, a.division_id,
               a.names.primary AS area_name,
               a.names.common['en'] AS area_en,
               a.region,
               a.geometry,
               a.bbox
        FROM read_parquet('{area_url}') a
        WHERE a.country IN ({all_codes_sql})
          AND a.is_land = true
          AND a.subtype IN ('locality', 'localadmin', 'county', 'region')
        """
    )
    n_areas = con.execute("SELECT COUNT(*) FROM all_areas").fetchone()[0]
    print(f"  Loaded {n_areas:,} division polygons in {time.time() - t_area:.1f}s", flush=True)

    # Build unified cities_geom table honoring each country's area_mode
    con.execute(
        """
        CREATE TEMP TABLE all_cities_geom (
            country VARCHAR,
            city_id VARCHAR,
            name VARCHAR,
            local_name VARCHAR,
            region VARCHAR,
            population BIGINT,
            muni_area_km2 DOUBLE,
            lat DOUBLE,
            lon DOUBLE,
            geom GEOMETRY,
            bbox STRUCT(xmin FLOAT, xmax FLOAT, ymin FLOAT, ymax FLOAT)
        )
        """
    )

    spatial_subtypes = {
        "localadmin_spatial": "localadmin",
        "county_spatial": "county",
        "region_spatial": "region",
    }

    for c_cfg in all_needed:
        code = c_cfg["code"]
        mode = c_cfg.get("area_mode", "locality")
        if mode in spatial_subtypes:
            st = spatial_subtypes[mode]
            con.execute(
                f"""
                INSERT INTO all_cities_geom
                SELECT
                    '{code}' AS country,
                    a.division_id AS city_id,
                    COALESCE(ANY_VALUE(a.area_en), ANY_VALUE(l.en_name), ANY_VALUE(a.area_name)) AS name,
                    ANY_VALUE(a.area_name) AS local_name,
                    COALESCE(ANY_VALUE(l.region_name), ANY_VALUE(a.region)) AS region,
                    MAX(l.population) AS population,
                    ROUND(ST_Area_Spheroid(ANY_VALUE(a.geometry)) / 1e6, 2) AS muni_area_km2,
                    ROUND(ST_Y(ST_Centroid(ANY_VALUE(a.geometry))), 5) AS lat,
                    ROUND(ST_X(ST_Centroid(ANY_VALUE(a.geometry))), 5) AS lon,
                    ANY_VALUE(a.geometry) AS geom,
                    ANY_VALUE(a.bbox) AS bbox
                FROM all_areas a
                JOIN all_locs l
                  ON l.country = '{code}' AND a.country = '{code}' AND a.subtype = '{st}'
                 AND l.bbox.xmin >= a.bbox.xmin AND l.bbox.xmax <= a.bbox.xmax
                 AND l.bbox.ymin >= a.bbox.ymin AND l.bbox.ymax <= a.bbox.ymax
                 AND ST_Within(l.geometry, a.geometry)
                GROUP BY a.division_id
                """
            )
        else:
            con.execute(
                f"""
                INSERT INTO all_cities_geom
                SELECT
                    '{code}' AS country,
                    l.id AS city_id,
                    ANY_VALUE(l.name) AS name,
                    ANY_VALUE(l.local_name) AS local_name,
                    ANY_VALUE(l.region_name) AS region,
                    MAX(l.population) AS population,
                    ROUND(SUM(ST_Area_Spheroid(a.geometry)) / 1e6, 2) AS muni_area_km2,
                    ROUND(COALESCE(ANY_VALUE(l.pt_lat), ST_Y(ST_Centroid(ANY_VALUE(a.geometry)))), 5) AS lat,
                    ROUND(COALESCE(ANY_VALUE(l.pt_lon), ST_X(ST_Centroid(ANY_VALUE(a.geometry)))), 5) AS lon,
                    ANY_VALUE(a.geometry) AS geom,
                    ANY_VALUE(a.bbox) AS bbox
                FROM all_locs l
                JOIN all_areas a
                  ON a.division_id = l.id
                 AND a.country = '{code}' AND l.country = '{code}'
                 AND a.subtype = 'locality'
                GROUP BY l.id
                """
            )

    counts = con.execute("SELECT country, COUNT(*) FROM all_cities_geom GROUP BY country ORDER BY country").fetchall()
    print(f"  Matched cities across {len(counts)} countries: " + ", ".join(f"{c}={n}" for c, n in counts), flush=True)
    con.execute("DROP TABLE all_locs; DROP TABLE all_areas;")

    # Step 2: Scan roads tile by tile (1 S3 pass per tile!)
    print(f"\nStep 2/2: Extracting roads across {len(needed_tiles)} geographic tiles...", flush=True)
    for ti, (tname, t_cfgs) in enumerate(needed_tiles, 1):
        codes = [c["code"] for c in t_cfgs]
        codes_sql = ", ".join(f"'{c}'" for c in codes)
        xmin = min(c["bbox"][0] for c in t_cfgs)
        ymin = min(c["bbox"][1] for c in t_cfgs)
        xmax = max(c["bbox"][2] for c in t_cfgs)
        ymax = max(c["bbox"][3] for c in t_cfgs)
        t1 = time.time()
        print(f"  [Tile {ti}/{len(needed_tiles)}: {tname}] ({','.join(codes)}) bbox=({xmin},{ymin},{xmax},{ymax})...", flush=True)

        con.execute("DROP TABLE IF EXISTS tile_joined")
        con.execute(
            f"""
            CREATE TEMP TABLE tile_joined AS
            WITH tile_cities AS (
                SELECT * FROM all_cities_geom WHERE country IN ({codes_sql})
            ),
            roads AS (
                SELECT s.names.primary AS street_name,
                       CASE
                         WHEN s.class IN ('primary', 'secondary', 'tertiary')
                          AND {ONEWAY_EXPR}
                         THEN ST_Length_Spheroid(s.geometry) * 0.5
                         ELSE ST_Length_Spheroid(s.geometry)
                       END AS adj_len_m,
                       (s.bbox.xmin + s.bbox.xmax) / 2.0 AS mid_lon,
                       (s.bbox.ymin + s.bbox.ymax) / 2.0 AS mid_lat,
                       ST_Point((s.bbox.xmin + s.bbox.xmax) / 2.0, (s.bbox.ymin + s.bbox.ymax) / 2.0) AS pt,
                       s.bbox
                FROM read_parquet('{seg_url}') s
                WHERE s.subtype = 'road'
                  AND s.names.primary IS NOT NULL
                  AND s.class IN ({URBAN_ROAD_CLASSES})
                  AND s.bbox.xmin >= {xmin} AND s.bbox.xmax <= {xmax}
                  AND s.bbox.ymin >= {ymin} AND s.bbox.ymax <= {ymax}
            )
            SELECT c.country,
                   c.city_id,
                   r.street_name,
                   r.adj_len_m,
                   CAST(FLOOR(r.mid_lon * 111320.0 * COS(RADIANS(c.lat)) / 250.0) AS BIGINT) AS gx,
                   CAST(FLOOR(r.mid_lat * 111320.0 / 250.0) AS BIGINT) AS gy
            FROM tile_cities c
            JOIN roads r
              ON r.bbox.xmin <= c.bbox.xmax AND r.bbox.xmax >= c.bbox.xmin
             AND r.bbox.ymin <= c.bbox.ymax AND r.bbox.ymax >= c.bbox.ymin
             AND ST_Within(r.pt, c.geom)
            """
        )
        print(f"    Tile {tname} spatial join finished in {time.time() - t1:.1f}s — writing per-country caches...", flush=True)

        for code in codes:
            cities_pq = CACHE_DIR / f"{code}_{release}_cities_v3.parquet"
            streets_pq = CACHE_DIR / f"{code}_{release}_streets_v3.parquet"
            con.execute(
                f"""
                COPY (
                    WITH cells AS (
                        SELECT city_id, gx, gy,
                               COUNT(DISTINCT street_name) AS cell_streets,
                               SUM(adj_len_m) AS cell_m
                        FROM tile_joined
                        WHERE country = '{code}'
                        GROUP BY city_id, gx, gy
                    ),
                    built AS (
                        SELECT city_id,
                               ROUND(COUNT(*) FILTER (WHERE cell_streets >= 2 OR cell_m >= 300.0) * 0.0625, 2) AS raw_built_km2
                        FROM cells
                        GROUP BY city_id
                    )
                    SELECT c.city_id, c.name, c.local_name, c.region, c.population,
                           c.muni_area_km2,
                           ROUND(LEAST(c.muni_area_km2, GREATEST(0.06, COALESCE(b.raw_built_km2, 0.06))), 2) AS built_area_km2,
                           c.lat, c.lon
                    FROM all_cities_geom c
                    LEFT JOIN built b ON b.city_id = c.city_id
                    WHERE c.country = '{code}'
                ) TO '{cities_pq.as_posix()}' (FORMAT PARQUET)
                """
            )
            con.execute(
                f"""
                COPY (
                    SELECT city_id, street_name, ROUND(SUM(adj_len_m), 1) AS len_m
                    FROM tile_joined
                    WHERE country = '{code}'
                    GROUP BY city_id, street_name
                ) TO '{streets_pq.as_posix()}' (FORMAT PARQUET)
                """
            )
            n_c = con.execute(f"SELECT COUNT(*) FROM read_parquet('{cities_pq.as_posix()}')").fetchone()[0]
            n_s = con.execute(f"SELECT COUNT(*) FROM read_parquet('{streets_pq.as_posix()}')").fetchone()[0]
            print(f"    [{code}] Saved {n_c:,} cities, {n_s:,} street rows", flush=True)

        con.execute("DROP TABLE tile_joined")

    con.close()
    print(f"\nAll batched extractions complete in {(time.time() - t0)/60:.1f} min!", flush=True)


if __name__ == "__main__":
    main()
