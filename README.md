# World Street Name Rarity

Interactive cross-country dashboard, map, and street-search explorer measuring how unique or repetitive street names are across cities.

Built on **Overture Maps** (OpenStreetMap + open administrative divisions) queried directly via **DuckDB**.

## Live Site (GitHub Pages)
https://baobabprince.github.io/world-street-rarity/

## Coverage (41 European Countries + Israel + New York)
- **Western Europe:** United Kingdom (`GB`), Ireland (`IE`), France (`FR`), Netherlands (`NL`), Belgium (`BE`), Luxembourg (`LU`), Germany (`DE`), Switzerland (`CH`), Austria (`AT`)
- **Southern Europe:** Spain (`ES`), Portugal (`PT`), Italy (`IT`), Greece (`GR`), Malta (`MT`), Cyprus (`CY`)
- **Northern Europe (Nordics & Baltics):** Denmark (`DK`), Sweden (`SE`), Norway (`NO`), Finland (`FI`), Iceland (`IS`), Estonia (`EE`), Latvia (`LV`), Lithuania (`LT`)
- **Central & Eastern Europe:** Poland (`PL`), Czechia (`CZ`), Slovakia (`SK`), Hungary (`HU`), Romania (`RO`), Bulgaria (`BG`), Ukraine (`UA`), Belarus (`BY`), Moldova (`MD`), Russia (`RU`)
- **Balkans:** Slovenia (`SI`), Croatia (`HR`), Serbia (`RS`), Bosnia & Herzegovina (`BA`), Montenegro (`ME`), North Macedonia (`MK`), Albania (`AL`), Kosovo (`XK`)
- **Benchmarks outside Europe:** Israel (`IL`, also benchmarked against official registries in [israel-street-rarity](https://github.com/baobabprince/israel-street-rarity)) & New York State (`NY` / `US-NY`, with NYC's 5 boroughs separated to compare Manhattan's numbered grid)

## Methodology
- **Standardized City Inclusion:** Municipalities/localities with population ≥ 10,000 and ≥ 20 normalized streets (or ≥ 50 streets when population metadata is unavailable).
- **Multilingual Normalization (`config/languages.json`):** Strips road-type prefixes (`Via`, `Rue de la`, `Calle de`, `ul.`, `רחוב`), suffixes and German fused compounds (`Goethestraße` → `goethe`, `Bahnhofsplatz` → `bahnhof`), folds accents/case for matching while keeping clean display names, preserves historical dates (`Via 20 Settembre`, `Rue du 14 Juillet`), filters highway route codes (`A1`, `SS16`), and tracks purely numbered streets separately (`numbered_pct`).
- **Intra-City & Nested-Locality Deduplication:** Each normalized street name is counted once per city, and overlapping sub-district/borough or duplicate polygons (≥88% exact street containment inside a larger nearby locality) are automatically removed so cities like Belgrade, Zagreb, Warsaw, and Be'er Sheva are never double-counted. For New York City, the aggregate city polygon is excluded so Manhattan, Brooklyn, Queens, The Bronx, and Staten Island appear cleanly as distinct non-overlapping boroughs.
- **Metrics (Default: Square-Root `sqrt`):**
  - **Sqrt (Default):** `1 / √n` (0–100% city mean) — strong penalty for nationwide recurring street names.
  - **Normalized IDF:** `1 - log(n) / log(C)` — normalized by number of cities `C` in each country for fair cross-country comparisons.
  - **Log:** `1 / log₂(1 + n)` — gentler penalty.
- **Urban Form, Active Built-Up Area & Corrected Street Length:**
  - **Active Built-Up Urban Area (`built_area_km2`):** Computed via an equal-area 250m × 250m grid counting cells with intensive street activity (≥ 2 distinct named streets or ≥ 300m of urban roads), separating real built-up urban footprints from rural/desert municipal borders (`muni_area_km2`, `built_share_pct`).
  - **Dual-Carriageway-Corrected Street Length:** Filters to urban road classes only (`primary`, `secondary`, `tertiary`, `residential`, `unclassified`, `living_street`, `pedestrian` — excluding `service`, `footway`, `cycleway`, `track`, `motorway`, `trunk`) and applies a 0.5× correction to one-way arterial segments (`primary`/`secondary`/`tertiary`) so divided boulevards aren't double-counted.
  - **Length-Weighted Rarity & Boulevard Cliché Gap:** Compares count-weighted vs length-weighted rarity (`rarity_mean_sqrt - rarity_len_sqrt`) and street lengths across rarity tiers.
  - **Power-Law Scaling Laws:** Empirical log-log scaling fits on active built-up area (`streets ~ built_area^α`, `population ~ built_area^γ`, `streets ~ population^β`).

## Local Usage
```bash
pip install -r requirements.txt
python scripts/build_data.py --countries IT
python scripts/build_data.py
pytest
```
