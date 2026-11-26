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


def test_pan_european_normalization():
    # Dutch fused suffixes
    k, _, _ = normalize_street("Kerkstraat", ["nl"])
    assert k == "kerk"
    k, _, _ = normalize_street("Dorpsstraat", ["nl"])
    assert k == "dorp"
    # Swedish / Norwegian / Danish / Finnish fused suffixes
    k, _, _ = normalize_street("Storgatan", ["sv"])
    assert k == "stor"
    k, _, _ = normalize_street("Kyrkogatan", ["sv"])
    assert k == "kyrko"
    k, _, _ = normalize_street("Storgata", ["no"])
    assert k == "stor"
    k, _, _ = normalize_street("Hovedgaden", ["da"])
    assert k == "hovedgaden" or k == "hoved"
    k, _, _ = normalize_street("Mannerheimintie", ["fi"])
    assert k == "mannerheimin"
    # Portuguese, Romanian, Hungarian, Czech, Lithuanian
    k, _, _ = normalize_street("Rua da Liberdade", ["pt"])
    assert k == "liberdade"
    k, _, _ = normalize_street("Strada Mihai Eminescu", ["ro"])
    assert k == "mihai eminescu"
    k, _, _ = normalize_street("Kossuth Lajos utca", ["hu"])
    assert k == "kossuth lajos"
    k, _, _ = normalize_street("Gedimino prospektas", ["lt"])
    assert k == "gedimino"
    # Greek transliteration & prefix stripping
    k, _, _ = normalize_street("Λεωφόρος Κηφισίας", ["el"])
    assert k == "kifisias"
    # Ukrainian Cyrillic transliteration & prefix stripping
    k, _, _ = normalize_street("вулиця Тараса Шевченка", ["uk"])
    assert k == "tarasa sevce" or k == "tarasa sevcenka"
    # Albanian & Maltese
    k, _, _ = normalize_street("Rruga e Kavajës", ["sq"])
    assert k == "kavajes"
    k, _, _ = normalize_street("Triq il-Kbira", ["mt"])
    assert k == "kbira"


def test_bilingual_normalization():
    # Belgium (nl, fr, de): prefers first configured language (nl) and resolves shared affixes
    k, d, _ = normalize_street("Rue de l'Abbaye - Abdijstraat", ["nl", "fr", "de"])
    assert k == "abdij" and d == "Abdijstraat"
    k, _, _ = normalize_street("Avenue de l'Atomium - Atomiumlaan", ["nl", "fr", "de"])
    assert k == "atomium"
    k, _, _ = normalize_street("Rue Neuve - Nieuwstraat", ["nl", "fr", "de"])
    assert k == "nieuw"
    k, _, _ = normalize_street("Boulevard du Midi - Zuidlaan", ["nl", "fr", "de"])
    assert k == "zuid"
    k, _, _ = normalize_street("Boulevard Anspach - Anspachlaan", ["nl", "fr", "de"])
    assert k == "anspach"
    k, _, _ = normalize_street("Chaussée de Charleroi - Charleroisesteenweg", ["nl", "fr", "de"])
    assert k == "charleroise"

    # Italy (it, de) + Aosta fallback (other:fr) + highway/non-bilingual guards
    k, _, _ = normalize_street("Carduccistraße - Via Carducci", ["it", "de"])
    assert k == "carducci"
    k, _, _ = normalize_street("Brennerstraße - Via Brennero", ["it", "de"])
    assert k == "brennero"
    k, _, _ = normalize_street("Burgfrieden - Via Castelliere", ["it", "de"])
    assert k == "castelliere"
    k, _, _ = normalize_street("Piazza Porta Prætoria / Place de la Porte Prétorienne", ["it", "de"])
    assert k == "porta praetoria"
    k, _, _ = normalize_street("Strada Comunale n.29 - Santo Scalone", ["it", "de"])
    assert k == "n 29 santo scalone"
    k, _, _ = normalize_street("Via Capitano Antonino Rizzo / Strada Statale 113", ["it", "de"])
    assert k == "antonino rizzo"

    # Switzerland (de, fr, it)
    k, _, _ = normalize_street("Chemin des Alouettes / Lerchenweg", ["de", "fr", "it"])
    assert k == "lerchen"
    k, _, _ = normalize_street("Rue des Alpes / Alpenstrasse", ["de", "fr", "it"])
    assert k == "alpen"

    # Germany Sorbian bilingual (de)
    k, _, _ = normalize_street("Karl-Liebknecht-Straße - Karla Liebknechtowa", ["de"])
    assert k == "karl liebknecht"
    k, _, _ = normalize_street("Czornebohstraße - Čornobóska", ["de"])
    assert k == "czorneboh"

    # Spain (es, ca, eu, gl) + intercity road guard
    k, _, _ = normalize_street("Calle de Chalamera / Carrer de Xalamera", ["es", "ca", "eu", "gl"])
    assert k == "chalamera"
    k, _, _ = normalize_street("Elía / Elia", ["es", "ca", "eu", "gl"])
    assert k == "elia"
    k, _, _ = normalize_street("Carretera València - Torrent", ["es", "ca", "eu", "gl"])
    assert k == "valencia torrent"

    # Slovenia (sl) + neighbor Italian + intercity road guard
    k, _, _ = normalize_street("Morova ulica - Via Mario Moro", ["sl"])
    assert k == "morova"
    k, _, _ = normalize_street("Kačja ulica / Calle della Bissa", ["sl"])
    assert k == "kacja"
    k, _, _ = normalize_street("Gorjuša - Žeje", ["sl"])
    assert k == "gorjusa zeje"

    # Israel (he, ar) cross-script + order independence + non-bilingual guard
    k, _, _ = normalize_street("רחוב הרצל - Herzl Street", ["he", "ar"])
    assert k == "הרצל"
    k, _, _ = normalize_street("הכרמל / الكرمل", ["he", "ar"])
    assert k == "הכרמל"
    k, _, _ = normalize_street("الكرمل / הכרמל", ["he", "ar"])
    assert k == "הכרמל"
    k, _, _ = normalize_street("כיכר ברק - חטיבה 188", ["he", "ar"])
    assert k == "ברק חטיבה 188"

    # Greece, Serbia, Spain-route, France guards & same_core
    k, _, _ = normalize_street("Εθνική Οδός Χανίων - Αεροδρομίου", ["el"])
    assert k == "chanion aerodromioy"
    k, _, _ = normalize_street("Κλεισούρα - Βλάστη", ["el"])
    assert k == "kleisoyra vlasti"
    k, _, _ = normalize_street("Antiga Carretera de Vilanova i la Geltrú a Igualada - C-15z", ["es", "ca", "eu", "gl"])
    assert k == "antiga carretera de vilanova i la geltru a igualada c 15z"
    k, _, _ = normalize_street("Мирослава Антића - Мике", ["sr"])
    assert k == "miroslava antica mike"
    k, _, _ = normalize_street("Cité d'Arenberg - Allée B", ["fr"])
    assert k == "arenberg allee b"
    k, _, _ = normalize_street("Rue de Sévigné - Avenue de Sévigné", ["fr"])
    assert k == "sevigne"


def test_title_cleaning_and_guards():
    # Hebrew
    assert normalize_street("רחוב הרב הרצוג", ["he", "ar"])[0] == "הרצוג"
    assert normalize_street("רחוב הנשיא ויצמן", ["he", "ar"])[0] == "ויצמן"
    assert normalize_street('רחוב ד"ר משה סנה', ["he", "ar"])[0] == "משה סנה"
    assert normalize_street("רחוב פרופ' אהרון צ'חנובר", ["he", "ar"])[0] == "אהרון צ'חנובר"
    assert normalize_street("רחוב אלוף הרב גורן", ["he", "ar"])[0] == "גורן"
    assert normalize_street("רחוב רבי יהודה הנשיא", ["he", "ar"])[0] == "יהודה הנשיא"
    assert normalize_street("רחוב הנשיא", ["he", "ar"])[0] == "הנשיא"
    assert normalize_street("רחוב רבי עקיבא", ["he", "ar"])[0] == "רבי עקיבא"
    assert normalize_street("רחוב המלך דוד", ["he", "ar"])[0] == "המלך דוד"

    # Italian
    assert normalize_street("Via Generale Carlo Alberto dalla Chiesa", ["it", "de"])[0] == "carlo alberto dalla chiesa"
    assert normalize_street("Via Papa Giovanni XXIII", ["it", "de"])[0] == "giovanni xxiii"
    assert normalize_street("Via Don Luigi Sturzo", ["it", "de"])[0] == "luigi sturzo"
    assert normalize_street("Via Avvocato Giovanni Agnelli", ["it", "de"])[0] == "giovanni agnelli"
    assert normalize_street("Via Don Bosco", ["it", "de"])[0] == "don bosco"
    assert normalize_street("Via Conte Verde", ["it", "de"])[0] == "conte verde"
    assert normalize_street("Via Padre Pio", ["it", "de"])[0] == "padre pio"

    # French
    assert normalize_street("Rue du Docteur Roux", ["fr"])[0] == "roux"
    assert normalize_street("Avenue du Maréchal Foch", ["fr"])[0] == "foch"
    assert normalize_street("Avenue du Général Charles de Gaulle", ["fr"])[0] == "charles de gaulle"
    assert normalize_street("Rue du Général de Gaulle", ["fr"])[0] == "de gaulle"
    assert normalize_street("Rue du Président Salvador Allende", ["fr"])[0] == "salvador allende"
    assert normalize_street("Rue du Général Moulin", ["fr"])[0] == "general moulin"
    assert normalize_street("Rue de l'Abbé Pierre", ["fr"])[0] == "abbe pierre"
    assert normalize_street("Rue Saint-Martin", ["fr"])[0] == "saint martin"

    # German
    assert normalize_street("Dr.-Robert-Koch-Straße", ["de"])[0] == "robert koch"
    assert normalize_street("Dr.-Martin-Luther-Straße", ["de"])[0] == "martin luther"
    assert normalize_street("Freiherr-vom-Stein-Straße", ["de"])[0] == "vom stein"
    assert normalize_street("Dr.-Lange-Straße", ["de"])[0] == "dr lange"
    assert normalize_street("Bürgermeister-Haupt-Straße", ["de"])[0] == "burgermeister haupt"
    assert normalize_street("Kaiser-Wilhelm-Straße", ["de"])[0] == "kaiser wilhelm"

    # Spanish
    assert normalize_street("Calle del Doctor Fleming", ["es", "ca", "eu", "gl"])[0] == "fleming"
    assert normalize_street("Calle del Pintor Velázquez", ["es", "ca", "eu", "gl"])[0] == "velazquez"
    assert normalize_street("Calle de Don Miguel de Cervantes", ["es", "ca", "eu", "gl"])[0] == "miguel de cervantes"
    assert normalize_street("Avenida del Presidente Adolfo Suárez", ["es", "ca", "eu", "gl"])[0] == "adolfo suarez"
    assert normalize_street("Calle del Maestro Luna", ["es", "ca", "eu", "gl"])[0] == "maestro luna"
    assert normalize_street("Calle de Doña Sol", ["es", "ca", "eu", "gl"])[0] == "dona sol"
    assert normalize_street("Calle del Cardenal Cervantes", ["es", "ca", "eu", "gl"])[0] == "cardenal cervantes"
    assert normalize_street("Calle Mayor", ["es", "ca", "eu", "gl"])[0] == "mayor"

    # Polish
    assert normalize_street("ul. Generała Tadeusza Kościuszki", ["pl"])[0] == "tadeusza kosciuszki"
    assert normalize_street("ul. Księdza Kardynała Stefana Wyszyńskiego", ["pl"])[0] == "stefana wyszynskiego"
    assert normalize_street("ul. im. Marszałka Józefa Piłsudskiego", ["pl"])[0] == "jozefa pilsudskiego"
    assert normalize_street("ul. Świętego Floriana", ["pl"])[0] == "swietego floriana"
    assert normalize_street("ul. Królowej Jadwigi", ["pl"])[0] == "krolowej jadwigi"

    # English, Serbian, Dutch, Portuguese, Ukrainian
    assert normalize_street("Dr. Martin Luther King Jr. Boulevard", ["en"])[0] == "martin luther king jr"
    assert normalize_street("King Edward Street", ["en"])[0] == "king edward"
    assert normalize_street("Lady Park Road", ["en"])[0] == "lady park"
    assert normalize_street("Doctor Hill Road", ["en"])[0] == "doctor hill"
    assert normalize_street("Ulica Kneza Miloša Obrenovića", ["sr"])[0] == "milosa obrenovica"
    assert normalize_street("Ulica Kneza Mihaila", ["sr"])[0] == "kneza mihaila"
    assert normalize_street("Professor Lorentzstraat", ["nl"])[0] == "lorentz"
    assert normalize_street("Doctor Aletta Jacobsstraat", ["nl"])[0] == "aletta jacobs"
    assert normalize_street("Doctor Bosstraat", ["nl"])[0] == "doctor bos"
    assert normalize_street("Rua Doutor Francisco Sá Carneiro", ["pt"])[0] == "francisco sa carneiro"
    assert normalize_street("Rua Padre Cruz", ["pt"])[0] == "padre cruz"
    assert normalize_street("вулиця Академіка Корольова", ["uk"])[0] == "korolova"


def test_russian_normalization():
    # Road-type suffixes & prefixes
    assert normalize_street("Тверская улица", ["ru"])[0] == "tverskaja"
    assert normalize_street("Невский проспект", ["ru"])[0] == "nevskij"
    assert normalize_street("Сосинский проезд", ["ru"])[0] == "sosinskij"
    assert normalize_street("улица Якушева", ["ru"])[0] == "jakuseva"
    assert normalize_street("Красная площадь", ["ru"])[0] == "krasnaja"
    assert normalize_street("Кутузовский проспект", ["ru"])[0] == "kutuzovskij"
    assert normalize_street("Варшавское шоссе", ["ru"])[0] == "varsavskoe"
    assert normalize_street("Котельническая набережная", ["ru"])[0] == "kotelniceskaja"
    assert normalize_street("Сиреневая аллея", ["ru"])[0] == "sirenevaja"

    # Titles & multi-token guards
    assert normalize_street("улица Академика Королёва", ["ru"])[0] == "koroleva"
    assert normalize_street("проспект Маршала Жукова", ["ru"])[0] == "marsala zukova"
    assert normalize_street("улица Маршала Георгия Жукова", ["ru"])[0] == "georgija zukova"
    assert normalize_street("улица имени Ленина", ["ru"])[0] == "lenina"

    # Numbered ordinal streets vs historical/named ordinals
    assert normalize_street("1-я линия", ["ru"]) == ("", "1-я линия", True)
    assert normalize_street("2-й проезд", ["ru"]) == ("", "2-й проезд", True)
    assert normalize_street("2-я Дубровская улица", ["ru"]) == ("2 ja dubrovskaja", "2-я Дубровская улица", False)
    assert normalize_street("улица 3 Интернационала", ["ru"]) == ("3 internacionala", "улица 3 Интернационала", False)
    assert normalize_street("улица 50 лет ВЛКСМ", ["ru"]) == ("50 let vlksm", "улица 50 лет ВЛКСМ", False)


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
        if c["cities_count"] >= 5:
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
