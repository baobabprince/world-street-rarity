"""Unit and data-integrity tests for World Street Rarity."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from normalize import normalize_street  # noqa: E402


def test_italian_normalization():
    k, _, num = normalize_street("Via Giuseppe Garibaldi", ["it"])
    assert k == "giuseppe garibaldi" and not num
    k, _, _ = normalize_street("Piazza della Repubblica", ["it"])
    assert k == "repubblica"
    k, _, _ = normalize_street("Corso Vittorio Emanuele II", ["it"])
    assert k == "vittorio emanuele ii"
    # Historical date must be preserved (NOT treated as numbered street)
    k, _, num = normalize_street("Via 20 Settembre", ["it"])
    assert k == "20 settembre" and not num
    # Highway route code dropped
    k, _, num = normalize_street("SS16", ["it"])
    assert k == "" and not num


def test_french_normalization():
    k, _, _ = normalize_street("Rue de l'Église", ["fr"])
    assert k == "eglise"
    k, _, _ = normalize_street("Avenue Victor Hugo", ["fr"])
    assert k == "victor hugo"
    k, _, _ = normalize_street("Boulevard de la République", ["fr"])
    assert k == "republique"
    k, _, num = normalize_street("Rue du 14 Juillet", ["fr"])
    assert k == "14 juillet" and not num


def test_german_normalization():
    k, _, _ = normalize_street("Goethestraße", ["de"])
    assert k == "goethe"
    k, _, _ = normalize_street("Bahnhofsplatz", ["de"])
    assert k == "bahnhof"
    k, _, _ = normalize_street("Berliner Straße", ["de"])
    assert k == "berliner"
    k, _, _ = normalize_street("Hauptstraße", ["de"])
    assert k == "haupt"


def test_spanish_and_regional_normalization():
    k, _, _ = normalize_street("Calle de Alcalá", ["es", "ca", "eu", "gl"])
    assert k == "alcala"
    k, _, _ = normalize_street("Carrer de Balmes", ["es", "ca", "eu", "gl"])
    assert k == "balmes"
    k, _, _ = normalize_street("Avenida de la Constitución", ["es", "ca", "eu", "gl"])
    assert k == "constitucion"
    k, _, num = normalize_street("Calle 10", ["es", "ca", "eu", "gl"])
    assert k == "" and num is True


def test_polish_normalization():
    k, _, _ = normalize_street("ul. Marszałkowska", ["pl"])
    assert k == "marszalkowska"
    k, _, _ = normalize_street("Aleja Niepodległości", ["pl"])
    assert k == "niepodleglosci"
    k, _, num = normalize_street("ul. 3 Maja", ["pl"])
    assert k == "3 maja" and not num


def test_hebrew_normalization():
    k, _, _ = normalize_street("משעול הרקפת", ["he", "ar"])
    assert k == "הרקפת"
    k, _, _ = normalize_street("רחוב הרצל", ["he", "ar"])
    assert k == "הרצל"
    k, _, num = normalize_street("רחוב 200", ["he", "ar"])
    assert k == "" and num is True


def test_balkan_cyrillic_and_latin_normalization():
    k_cyr, _, _ = normalize_street("Улица Кнеза Михаила", ["sr"])
    k_lat, _, _ = normalize_street("Ulica Kneza Mihaila", ["sr"])
    assert k_cyr == "kneza mihaila" and k_lat == "kneza mihaila"
    k_dj1, _, _ = normalize_street("Ђуре Јакшића", ["sr"])
    k_dj2, _, _ = normalize_street("Đure Jakšića", ["sr"])
    assert k_dj1 == "djure jaksica" and k_dj2 == "djure jaksica"
    k_suf, _, _ = normalize_street("Balkanska ulica", ["sr"])
    assert k_suf == "balkanska"
    k_mk, _, _ = normalize_street("Булевар Партизански Одреди", ["mk", "sr"])
    assert k_mk == "partizanski odredi"


def test_english_new_york_normalization():
    # Manhattan numbered grid streets & avenues
    k, _, num = normalize_street("West 42nd Street", ["en"])
    assert k == "" and num is True
    k, _, num = normalize_street("East 14th Street", ["en"])
    assert k == "" and num is True
    k, _, num = normalize_street("5th Avenue", ["en"])
    assert k == "" and num is True
    # Named streets & directional merging
    k, _, num = normalize_street("Broadway", ["en"])
    assert k == "broadway" and not num
    k, _, _ = normalize_street("Lexington Avenue", ["en"])
    assert k == "lexington"
    k1, _, _ = normalize_street("East Houston Street", ["en"])
    k2, _, _ = normalize_street("West Houston Street", ["en"])
    assert k1 == "houston" and k2 == "houston"
    k, _, _ = normalize_street("West Street", ["en"])
    assert k == "west"
    k, _, _ = normalize_street("West End Avenue", ["en"])
    assert k == "west end"
    # US highway route codes dropped
    k, _, num = normalize_street("NY 17", ["en"])
    assert k == "" and not num
    k, _, num = normalize_street("US Route 9", ["en"])
    assert k == "" and not num


def test_generated_outputs_integrity():
    summary_path = ROOT / "data" / "summary.json"
    if not summary_path.exists():
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    countries = summary.get("countries", [])
    assert len(countries) >= 1

    for c in countries:
        code = c["code"]
        cpath = ROOT / "data" / "countries" / f"{code}.json"
        spath = ROOT / "data" / "search" / f"{code}.json"
        assert cpath.exists(), f"Missing {cpath}"
        assert spath.exists(), f"Missing {spath}"

        cities = json.loads(cpath.read_text(encoding="utf-8"))
        assert len(cities) == c["cities_count"]
        assert c.get("length_by_tier"), f"Missing length_by_tier in {code}"
        assert c.get("scaling", {}).get("streets_vs_built_area"), f"Missing built-area scaling in {code}"
        for city in cities:
            assert city["streets"] >= 20, city["name"]
            assert 0 <= city["pure_exclusive"] <= city["streets"], city["name"]
            assert city["rarity_sum_sqrt"] <= city["streets"] + 0.2, city["name"]
            assert 0.0 <= city["rarity_mean_sqrt"] <= 100.0, city["name"]
            assert 0.0 <= city["rarity_len_sqrt"] <= 100.0, city["name"]
            assert 0.0 <= city["rarity_mean_idf"] <= 100.0, city["name"]
            assert 0.0 <= city["rarity_mean_log"] <= 100.0, city["name"]
            assert city["built_area_km2"] is not None and city["built_area_km2"] > 0, city["name"]
            assert city["muni_area_km2"] is not None and city["built_area_km2"] <= city["muni_area_km2"] + 0.05, city["name"]
            assert city["total_street_km"] > 0 and city["median_street_m"] > 0, city["name"]
            assert city["lat"] is not None and city["lon"] is not None, city["name"]
