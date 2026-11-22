# World Street Name Rarity

Interactive cross-country dashboard, map, and street-search explorer measuring how unique or repetitive street names are across cities.

Built on **Overture Maps** (OpenStreetMap + open administrative divisions) queried directly via **DuckDB**.

## Live Site (GitHub Pages)
https://baobabprince.github.io/world-street-rarity/

## Pilot Countries
- **Italy (`IT`)**
- **France (`FR`)**
- **Germany (`DE`)**
- **Spain (`ES`)**
- **Switzerland (`CH`)**
- **Poland (`PL`)**
- **Israel (`IL`)** *(also benchmarked against official registries in [israel-street-rarity](https://github.com/baobabprince/israel-street-rarity))*

## Methodology
- **Standardized City Inclusion:** Municipalities/localities with population ≥ 10,000 and ≥ 20 normalized streets (or ≥ 50 streets when population metadata is unavailable).
- **Multilingual Normalization (`config/languages.json`):** Strips road-type prefixes (`Via`, `Rue de la`, `Calle de`, `ul.`, `רחוב`), suffixes and German fused compounds (`Goethestraße` → `goethe`, `Bahnhofsplatz` → `bahnhof`), folds accents/case for matching while keeping clean display names, preserves historical dates (`Via 20 Settembre`, `Rue du 14 Juillet`), filters highway route codes (`A1`, `SS16`), and tracks purely numbered streets separately (`numbered_pct`).
- **Intra-City Deduplication:** Each normalized street name is counted once per city.
- **Metrics (Default: Square-Root `sqrt`):**
  - **Sqrt (Default):** `1 / √n` (0–100% city mean) — strong penalty for nationwide recurring street names.
  - **Normalized IDF:** `1 - log(n) / log(C)` — normalized by number of cities `C` in each country for fair cross-country comparisons.
  - **Log:** `1 / log₂(1 + n)` — gentler penalty.

## Local Usage
```bash
pip install -r requirements.txt
python scripts/build_data.py --countries IT
python scripts/build_data.py
pytest
```
