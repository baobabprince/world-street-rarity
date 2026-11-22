#!/usr/bin/env python3
"""Extract city boundaries + named road segments per country from Overture Maps via DuckDB."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "INSTALL spatial; LOAD spatial; "
        "INSTALL httpfs; LOAD httpfs; "
        "SET s3_region='us-west-2';"
    )
    return con


def extract_country(
    country_cfg: dict[str, Any],
    release: str,
    min_population: int = 10000,
    force: bool = False,
) -> tuple[Path, Path]:
    """Extract city metadata and (city_id, street_name) pairs to local Parquet cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    code = country_cfg["code"]
    cities_pq = CACHE_DIR / f"{code}_{release}_cities.parquet"
    streets_pq = CACHE_DIR / f"{code}_{release}_streets.parquet"

    if not force and cities_pq.exists() and streets_pq.exists():
        print(f"[{code}] Using cached Overture Parquet files", flush=True)
        return cities_pq, streets_pq

    xmin, ymin, xmax, ymax = country_cfg["bbox"]
    area_mode = country_cfg.get("area_mode", "locality")

    div_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division/*"
    area_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*"
    seg_url = f"s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment/*"

    con = _connect()
    t0 = time.time()
    print(f"[{code}] Extracting divisions (mode={area_mode}, pop>={min_population})...", flush=True)

    # Extract human-readable region name from Overture hierarchies struct array
    region_expr = (
        "COALESCE("
        "  list_extract(list_transform(list_filter(d.hierarchies[1], lambda h: h.subtype = 'region'), lambda h: h.name), 1),"
        "  d.region"
        ")"
    )

    if area_mode == "localadmin_spatial":
        # Switzerland: polygons live on localadmin (Gemeinde), population on locality points
        con.execute(
            f"""
            CREATE TEMP TABLE cities_geom AS
            WITH locs AS (
                SELECT d.id, d.names.primary AS loc_name, d.names.common['en'] AS en_name,
                       d.population, d.geometry, d.bbox,
                       {region_expr} AS region_name
                FROM read_parquet('{div_url}') d
                WHERE d.country = '{code}'
                  AND d.subtype = 'locality'
                  AND d.population >= {min_population}
            ),
            areas AS (
                SELECT a.division_id, a.names.primary AS area_name, a.names.common['en'] AS area_en,
                       a.region, a.geometry, a.bbox
                FROM read_parquet('{area_url}') a
                WHERE a.country = '{code}'
                  AND a.is_land = true
                  AND a.subtype = 'localadmin'
            )
            SELECT
                a.division_id AS city_id,
                COALESCE(ANY_VALUE(a.area_en), ANY_VALUE(l.en_name), ANY_VALUE(a.area_name)) AS name,
                ANY_VALUE(a.area_name) AS local_name,
                COALESCE(ANY_VALUE(l.region_name), ANY_VALUE(a.region)) AS region,
                MAX(l.population) AS population,
                ROUND(ST_Y(ST_Centroid(ANY_VALUE(a.geometry))), 5) AS lat,
                ROUND(ST_X(ST_Centroid(ANY_VALUE(a.geometry))), 5) AS lon,
                ANY_VALUE(a.geometry) AS geom,
                ANY_VALUE(a.bbox) AS bbox
            FROM areas a
            JOIN locs l
              ON l.bbox.xmin >= a.bbox.xmin AND l.bbox.xmax <= a.bbox.xmax
             AND l.bbox.ymin >= a.bbox.ymin AND l.bbox.ymax <= a.bbox.ymax
             AND ST_Within(l.geometry, a.geometry)
            GROUP BY a.division_id
            """
        )
    else:
        # Standard countries: locality division_id directly matches division_area
        con.execute(
            f"""
            CREATE TEMP TABLE cities_geom AS
            WITH locs AS (
                SELECT d.id,
                       COALESCE(d.names.common['en'], d.names.primary) AS name,
                       d.names.primary AS local_name,
                       {region_expr} AS region_name,
                       d.population,
                       ST_Y(d.geometry) AS pt_lat,
                       ST_X(d.geometry) AS pt_lon
                FROM read_parquet('{div_url}') d
                WHERE d.country = '{code}'
                  AND d.subtype = 'locality'
                  AND d.population >= {min_population}
            )
            SELECT
                l.id AS city_id,
                ANY_VALUE(l.name) AS name,
                ANY_VALUE(l.local_name) AS local_name,
                ANY_VALUE(l.region_name) AS region,
                MAX(l.population) AS population,
                ROUND(COALESCE(ANY_VALUE(l.pt_lat), ST_Y(ST_Centroid(ANY_VALUE(a.geometry)))), 5) AS lat,
                ROUND(COALESCE(ANY_VALUE(l.pt_lon), ST_X(ST_Centroid(ANY_VALUE(a.geometry)))), 5) AS lon,
                ANY_VALUE(a.geometry) AS geom,
                ANY_VALUE(a.bbox) AS bbox
            FROM locs l
            JOIN read_parquet('{area_url}') a
              ON a.division_id = l.id
             AND a.country = '{code}'
             AND a.subtype = 'locality'
             AND a.is_land = true
            GROUP BY l.id
            """
        )

    n_cities = con.execute("SELECT COUNT(*) FROM cities_geom").fetchone()[0]
    print(f"[{code}] Found {n_cities} cities with polygons in {time.time() - t0:.1f}s", flush=True)

    # Save city metadata (without heavy polygons)
    con.execute(
        f"""
        COPY (
            SELECT city_id, name, local_name, region, population, lat, lon
            FROM cities_geom
        ) TO '{cities_pq.as_posix()}' (FORMAT PARQUET)
        """
    )

    t1 = time.time()
    print(f"[{code}] Extracting & spatially joining named roads in bbox {country_cfg['bbox']}...", flush=True)
    con.execute(
        f"""
        COPY (
            WITH roads AS (
                SELECT s.names.primary AS street_name,
                       ST_Point((s.bbox.xmin + s.bbox.xmax) / 2.0, (s.bbox.ymin + s.bbox.ymax) / 2.0) AS pt,
                       s.bbox
                FROM read_parquet('{seg_url}') s
                WHERE s.subtype = 'road'
                  AND s.names.primary IS NOT NULL
                  AND COALESCE(s.class, '') NOT IN ('motorway', 'trunk')
                  AND s.bbox.xmin >= {xmin} AND s.bbox.xmax <= {xmax}
                  AND s.bbox.ymin >= {ymin} AND s.bbox.ymax <= {ymax}
            )
            SELECT DISTINCT c.city_id, r.street_name
            FROM cities_geom c
            JOIN roads r
              ON r.bbox.xmin <= c.bbox.xmax AND r.bbox.xmax >= c.bbox.xmin
             AND r.bbox.ymin <= c.bbox.ymax AND r.bbox.ymax >= c.bbox.ymin
             AND ST_Within(r.pt, c.geom)
        ) TO '{streets_pq.as_posix()}' (FORMAT PARQUET)
        """
    )
    n_pairs = con.execute(f"SELECT COUNT(*) FROM read_parquet('{streets_pq.as_posix()}')").fetchone()[0]
    print(f"[{code}] Extracted {n_pairs:,} distinct (city, raw_street) pairs in {time.time() - t1:.1f}s", flush=True)
    con.close()
    return cities_pq, streets_pq
