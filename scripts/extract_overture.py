#!/usr/bin/env python3
"""Extract city boundaries (municipal area + 250m active built-up urban area) and
dual-carriageway-corrected street lengths from Overture Maps via DuckDB."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"

# Real urban street classes only (exclude motorway, trunk, service, footway, cycleway, path, track, steps)
URBAN_ROAD_CLASSES = (
    "'primary', 'secondary', 'tertiary', 'residential', "
    "'unclassified', 'living_street', 'pedestrian'"
)

# Oneway detection in Overture Maps access_restrictions
ONEWAY_EXPR = (
    "list_bool_or(list_transform(COALESCE(s.access_restrictions, []), "
    "lambda r: r.access_type = 'denied' AND r.\"when\".heading IN ('backward', 'forward')))"
)


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "INSTALL spatial; LOAD spatial; "
        "INSTALL httpfs; LOAD httpfs; "
        "SET s3_region='us-west-2'; "
        "SET geometry_always_xy = true;"
    )
    return con


def extract_country(
    country_cfg: dict[str, Any],
    release: str,
    min_population: int = 10000,
    force: bool = False,
) -> tuple[Path, Path]:
    """Extract city metadata (with muni_area_km2 & built_area_km2) and adjusted street lengths."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    code = country_cfg["code"]
    cities_pq = CACHE_DIR / f"{code}_{release}_cities_v3.parquet"
    streets_pq = CACHE_DIR / f"{code}_{release}_streets_v3.parquet"

    if not force and cities_pq.exists() and streets_pq.exists():
        print(f"[{code}] Using cached v3 Overture Parquet files", flush=True)
        return cities_pq, streets_pq

    xmin, ymin, xmax, ymax = country_cfg["bbox"]
    area_mode = country_cfg.get("area_mode", "locality")

    div_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division/*"
    area_url = f"s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*"
    seg_url = f"s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment/*"

    con = _connect()
    t0 = time.time()
    print(f"[{code}] Extracting divisions + municipal area (mode={area_mode}, pop>={min_population})...", flush=True)

    region_expr = (
        "COALESCE("
        "  list_extract(list_transform(list_filter(d.hierarchies[1], lambda h: h.subtype = 'region'), lambda h: h.name), 1),"
        "  d.region"
        ")"
    )

    if area_mode == "localadmin_spatial":
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
                ROUND(ST_Area_Spheroid(ANY_VALUE(a.geometry)) / 1e6, 2) AS muni_area_km2,
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
                ROUND(SUM(ST_Area_Spheroid(a.geometry)) / 1e6, 2) AS muni_area_km2,
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

    t1 = time.time()
    print(f"[{code}] Extracting urban roads (dual-carriageway adjusted) + 250m active built-up grid...", flush=True)

    # Materialize joined road segments once in temp table so we compute BOTH
    # dual-carriageway-corrected street lengths AND 250m x 250m active built-up area!
    con.execute(
        f"""
        CREATE TEMP TABLE joined_roads AS
        WITH roads AS (
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
        SELECT c.city_id,
               r.street_name,
               r.adj_len_m,
               CAST(FLOOR(r.mid_lon * 111320.0 * COS(RADIANS(c.lat)) / 250.0) AS BIGINT) AS gx,
               CAST(FLOOR(r.mid_lat * 111320.0 / 250.0) AS BIGINT) AS gy
        FROM cities_geom c
        JOIN roads r
          ON r.bbox.xmin <= c.bbox.xmax AND r.bbox.xmax >= c.bbox.xmin
         AND r.bbox.ymin <= c.bbox.ymax AND r.bbox.ymax >= c.bbox.ymin
         AND ST_Within(r.pt, c.geom)
        """
    )

    # Save cities with BOTH muni_area_km2 and built_area_km2 (active 250m x 250m urban cells: >=2 streets OR >=300m road)
    con.execute(
        f"""
        COPY (
            WITH cells AS (
                SELECT city_id, gx, gy,
                       COUNT(DISTINCT street_name) AS cell_streets,
                       SUM(adj_len_m) AS cell_m
                FROM joined_roads
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
            FROM cities_geom c
            LEFT JOIN built b ON b.city_id = c.city_id
        ) TO '{cities_pq.as_posix()}' (FORMAT PARQUET)
        """
    )

    # Save deduplicated (city_id, raw street_name, adj_len_m)
    con.execute(
        f"""
        COPY (
            SELECT city_id, street_name, ROUND(SUM(adj_len_m), 1) AS len_m
            FROM joined_roads
            GROUP BY city_id, street_name
        ) TO '{streets_pq.as_posix()}' (FORMAT PARQUET)
        """
    )

    n_pairs = con.execute(f"SELECT COUNT(*) FROM read_parquet('{streets_pq.as_posix()}')").fetchone()[0]
    print(f"[{code}] Extracted {n_pairs:,} adjusted (city, street, len_m) rows + built_area_km2 in {time.time() - t1:.1f}s", flush=True)
    con.close()
    return cities_pq, streets_pq
