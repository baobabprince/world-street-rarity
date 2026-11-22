#!/usr/bin/env python3
"""Multilingual street-name normalization for World Street Rarity."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
LANG_CONFIG_PATH = CONFIG_DIR / "languages.json"

# Hebrew niqqud / cantillation marks (excluding U+05BE maqaf ־)
_HE_NIQQUD_RE = re.compile(r"[\u0591-\u05BD\u05BF\u05C1\u05C2\u05C4\u05C5\u05C7]")
# Arabic diacritics (tashkeel)
_AR_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670]")

# Highway / route reference codes (e.g. "A1", "SS16", "SP42", "D940", "E45", "AP-7", "DW102", "B12", "NY 17", "US 9", "I-95")
_HIGHWAY_CODE_RE = re.compile(
    r"^(?:"
    r"(?:a|b|c|d|e|f|i|k|l|m|n|r|s|t|ss|sp|sr|sc|ap|au|cm|cv|gi|gr|ma|na|dk|dw|dp|rn|rd|cd|ch|cr|vc|v|us|ny|nj|ct|pa|vt|rt|rte)\s*[-/]?\s*\d+[a-z]?(?:\s+(?:bis|ter|dir|var|nord|sud|est|ovest|north|south|east|west|alt|bus|byp|spur))?"
    r"|(?:autostrada|autoroute|autobahn|autovia|autopista|autoput|strada statale|strada provinciale|strada regionale|route departementale|route nationale|droga krajowa|droga wojewodzka|bundesstrasse|landstrasse|state route|county route|county road|us route|us highway|interstate|new york state route|state highway|כביש)\s+[a-z0-9]{1,3}[-/]?\d+[a-z]?(?:\s+(?:north|south|east|west|nord|sud|est|ovest|bis|ter))?"
    r"|(?:autostrada|autoroute|autobahn|autovia|autopista|autoput|strada statale|strada provinciale|strada regionale|route departementale|route nationale|droga krajowa|droga wojewodzka|bundesstrasse|landstrasse|state route|county route|county road|us route|us highway|interstate|new york state route|state highway|כביש)\s+\d+[a-z]?(?:\s+(?:north|south|east|west|nord|sud|est|ovest|bis|ter))?"
    r")$",
    re.IGNORECASE,
)

# Purely numeric / ordinal street names (excluding historical dates like "20 Settembre", "14 Juillet", "3 Maja")
_PURE_NUMERIC_RE = re.compile(r"^(?:\d+[a-z]?|\d+(?:st|nd|rd|th|e|er|eme|o|a|°|º|ª)?)$", re.IGNORECASE)

# Special Latin & South Slavic Cyrillic transliterations before NFKD stripping
_LATIN_TRANSLIT = str.maketrans({
    "ß": "ss",
    "ẞ": "ss",
    "ł": "l",
    "Ł": "l",
    "ø": "o",
    "Ø": "o",
    "æ": "ae",
    "Æ": "ae",
    "œ": "oe",
    "Œ": "oe",
    "đ": "dj",
    "Đ": "dj",
    # Serbian / Macedonian / general Cyrillic -> Latin
    "а": "a", "А": "a",
    "б": "b", "Б": "b",
    "в": "v", "В": "v",
    "г": "g", "Г": "g",
    "д": "d", "Д": "d",
    "ђ": "dj", "Ђ": "dj",
    "ѓ": "g", "Ѓ": "g",
    "е": "e", "Е": "e",
    "ё": "e", "Ё": "e",
    "ж": "z", "Ж": "z",
    "з": "z", "З": "z",
    "ѕ": "dz", "Ѕ": "dz",
    "и": "i", "И": "i",
    "й": "j", "Й": "j",
    "ј": "j", "Ј": "j",
    "к": "k", "К": "k",
    "л": "l", "Л": "l",
    "љ": "lj", "Љ": "lj",
    "м": "m", "М": "m",
    "н": "n", "Н": "n",
    "њ": "nj", "Њ": "nj",
    "о": "o", "О": "o",
    "п": "p", "П": "p",
    "р": "r", "Р": "r",
    "с": "s", "С": "s",
    "т": "t", "Т": "t",
    "ћ": "c", "Ћ": "c",
    "ќ": "k", "Ќ": "k",
    "у": "u", "У": "u",
    "ф": "f", "Ф": "f",
    "х": "h", "Х": "h",
    "ц": "c", "Ц": "c",
    "ч": "c", "Ч": "c",
    "џ": "dz", "Џ": "dz",
    "ш": "s", "Ш": "s",
    "щ": "st", "Щ": "st",
    "ъ": "", "Ъ": "",
    "ы": "y", "Ы": "y",
    "ь": "", "Ь": "",
    "э": "e", "Э": "e",
    "ю": "ju", "Ю": "ju",
    "я": "ja", "Я": "ja",
})

# Nouns that should not lose a leading directional (e.g. "West End", "North Side", "East River")
_PROTECTED_AFTER_DIR = {
    "end", "side", "point", "park", "shore", "view", "river", "harbor",
    "bay", "island", "field", "wood", "hill", "hills", "gate", "bridge",
    "port", "beach", "lake", "creek", "brook", "haven", "ridge", "meadow",
}

_LANG_CACHE: dict[str, dict[str, list[str]]] | None = None
_PREPARED_CACHE: dict[tuple[int, tuple[str, ...]], tuple[list[tuple[str, bool]], list[tuple[str, bool]], list[str], list[str], list[str]]] = {}


def load_lang_rules(path: Path = LANG_CONFIG_PATH) -> dict[str, dict[str, list[str]]]:
    global _LANG_CACHE
    if _LANG_CACHE is not None and path == LANG_CONFIG_PATH:
        return _LANG_CACHE
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = {k: v for k, v in raw.items() if not k.startswith("_")}
    if path == LANG_CONFIG_PATH:
        _LANG_CACHE = rules
    return rules


def _prepare_lang_affixes(
    languages: list[str],
    rules: dict[str, dict[str, list[str]]],
) -> tuple[list[tuple[str, bool]], list[tuple[str, bool]], list[str], list[str], list[str]]:
    cache_key = (id(rules), tuple(languages))
    cached = _PREPARED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    raw_pref, raw_conn, raw_suf, raw_dir, raw_fused = [], [], [], [], []
    for lang in languages:
        lr = rules.get(lang, {})
        raw_pref.extend(lr.get("prefixes", []))
        raw_conn.extend(lr.get("connectors", []))
        raw_suf.extend(lr.get("suffixes", []))
        raw_dir.extend(lr.get("directionals", []))
        raw_fused.extend(lr.get("fused_suffixes", []))

    def _prep_pref(items: list[str]) -> list[tuple[str, bool]]:
        seen: set[tuple[str, bool]] = set()
        out: list[tuple[str, bool]] = []
        for item in sorted(items, key=len, reverse=True):
            f = fold_for_key(item)
            if not f:
                continue
            pair = (f, item.endswith("'"))
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
        out.sort(key=lambda x: len(x[0]), reverse=True)
        return out

    def _prep_plain(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            f = fold_for_key(item)
            if f and f not in seen:
                seen.add(f)
                out.append(f)
        out.sort(key=len, reverse=True)
        return out

    prepared = (
        _prep_pref(raw_pref),
        _prep_pref(raw_conn),
        _prep_plain(raw_suf),
        _prep_plain(raw_dir),
        _prep_plain(raw_fused),
    )
    _PREPARED_CACHE[cache_key] = prepared
    return prepared


_PUNCT_RE = re.compile(r"[.,;:!?()\[\]{}]")
_WS_RE = re.compile(r"\s+")
_ASCII_FOLD_TABLE = str.maketrans({
    "`": "'",
    "-": " ",
    "/": " ",
    "_": " ",
    ".": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    "!": " ",
    "?": " ",
    "(": " ",
    ")": " ",
    "[": " ",
    "]": " ",
    "{": " ",
    "}": " ",
})


def fold_for_key(text: str) -> str:
    """Fold case, diacritics, and punctuation for language-agnostic canonical key matching."""
    s = text.strip()
    if s.isascii():
        return _WS_RE.sub(" ", s.lower().translate(_ASCII_FOLD_TABLE)).strip()
    s = unicodedata.normalize("NFC", s)
    s = _HE_NIQQUD_RE.sub("", s)
    s = _AR_DIACRITICS_RE.sub("", s)
    s = s.translate(_LATIN_TRANSLIT).lower()
    # Strip combining marks (accents) from Latin scripts while preserving Hebrew/Arabic letters
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")
    for ch in ("'", "`", "’", "‘", "´", "׳"):
        s = s.replace(ch, "'")
    for ch in ('"', "”", "“", "״"):
        s = s.replace(ch, '"')
    for dash in ("-", "–", "—", "־", "−", "/", "_"):
        s = s.replace(dash, " ")
    # Remove stray punctuation except apostrophe & double-quote (needed for Hebrew acronyms)
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def clean_display(text: str) -> str:
    """Clean display text while preserving natural capitalization & diacritics."""
    s = unicodedata.normalize("NFC", text.strip())
    s = _HE_NIQQUD_RE.sub("", s)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    return re.sub(r"\s+", " ", s).strip()


def _strip_prefix_list(s: str, prefixes: list[tuple[str, bool]]) -> str:
    """Strip the longest matching pre-folded prefix token followed by space or apostrophe."""
    for p, is_apos in prefixes:
        if is_apos:
            if s.startswith(p) and len(s) > len(p):
                return s[len(p):].strip()
        else:
            if s.startswith(p + " ") and len(s) > len(p) + 1:
                return s[len(p) + 1:].strip()
    return s


def _strip_suffix_list(s: str, suffixes: list[str]) -> str:
    for sf in suffixes:
        if s.endswith(" " + sf) and len(s) > len(sf) + 1:
            return s[: -(len(sf) + 1)].strip()
    return s


def _strip_fused_german_suffix(s: str, fused_suffixes: list[str]) -> str:
    """Strip fused German compound road suffixes (e.g. 'goethestrasse' -> 'goethe')."""
    tokens = s.split()
    if not tokens:
        return s
    last = tokens[-1]
    for sf in fused_suffixes:
        if last.endswith(sf) and len(last) >= len(sf) + 3:
            stem = last[: -len(sf)]
            # Clean linking 's' only for common compound patterns like "bahnhofsplatz" -> "bahnhof"
            if stem.endswith(("hofs", "parks", "walds", "bergs", "dorfs", "markts", "turms", "bachs", "felds")):
                stem = stem[:-1]
            if len(stem) >= 3:
                tokens[-1] = stem
                return " ".join(tokens)
    return s


def normalize_street(
    raw_name: Any,
    languages: list[str],
    rules: dict[str, dict[str, list[str]]] | None = None,
) -> tuple[str, str, bool]:
    """Normalize a street name for the given country languages.

    Returns:
        (norm_key, display_name, is_numbered)
        If the street is empty, a highway route code, or noise, norm_key is "".
    """
    if not isinstance(raw_name, str):
        return "", "", False
    raw = raw_name.strip()
    if not raw or len(raw) < 2:
        return "", "", False

    # Strip trailing parenthetical disambiguations like "(tratto nord)"
    no_paren = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    if not no_paren:
        return "", "", False

    key = fold_for_key(no_paren)
    if not key:
        return "", "", False

    # Drop motorway/highway route references (e.g. "SS16", "A1", "D940", "SP12", "NY 17")
    if _HIGHWAY_CODE_RE.match(key):
        return "", "", False

    if rules is None:
        rules = load_lang_rules()

    prefixes, connectors, suffixes, directionals, fused_suffixes = _prepare_lang_affixes(languages, rules)

    # Strip road-type prefix (up to twice for nested forms like "Via Strada ...")
    for _ in range(2):
        nxt = _strip_prefix_list(key, prefixes)
        if nxt == key:
            break
        key = nxt

    # Strip trailing road-type suffix (up to twice, e.g. "Park Road Extension")
    for _ in range(2):
        nxt = _strip_suffix_list(key, suffixes)
        if nxt == key:
            break
        key = nxt

    # Strip English/US directional prefixes & suffixes ("West 42nd" -> "42nd", "Main North" -> "Main")
    if directionals:
        dir_pref = [(d, False) for d in directionals]
        cand = _strip_prefix_list(key, dir_pref)
        if cand != key and cand not in _PROTECTED_AFTER_DIR:
            key = cand
        cand_suf = _strip_suffix_list(key, directionals)
        if cand_suf != key and cand_suf not in _PROTECTED_AFTER_DIR:
            key = cand_suf
        key = _strip_suffix_list(key, suffixes)

    # Strip German fused suffixes ("Goethestraße" -> "goethe")
    if fused_suffixes:
        key = _strip_fused_german_suffix(key, fused_suffixes)

    # Strip leading prepositions / connectors ("de la", "della", "del", "d'")
    if connectors:
        key = _strip_prefix_list(key, connectors)

    # Strip trailing Hebrew alley/segment numbers or standalone trailing 1-3 digit sub-numbers
    if "he" in languages:
        key = re.sub(r"\s+סמט(?:ה|ת)?\s*\d+\s*$", "", key).strip()
        key = re.sub(r"\s+סמ'?\s*\d+\s*$", "", key).strip()

    key = re.sub(r"\s+", " ", key).strip()
    if not key:
        return "", "", False

    # Check if purely numeric / ordinal street (e.g. "42", "1st", "10a")
    if _PURE_NUMERIC_RE.match(key):
        return "", clean_display(no_paren), True

    # Drop single-character leftovers
    if len(key) < 2:
        return "", "", False

    return key, clean_display(no_paren), False
