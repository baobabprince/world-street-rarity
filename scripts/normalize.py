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

# Highway / route reference codes (e.g. "A1", "SS16", "SP42", "D940", "E45", "AP-7", "DW102", "B12")
_HIGHWAY_CODE_RE = re.compile(
    r"^(?:"
    r"(?:a|b|c|d|e|f|k|l|m|n|r|s|t|ss|sp|sr|sc|ap|au|cm|cv|gi|gr|ma|na|dk|dw|dp|rn|rd|cd|ch|cr|vc|v)\s*[-/]?\s*\d+[a-z]?(?:\s*(?:bis|ter|dir|var|nord|sud|est|ovest))?"
    r"|(?:autostrada|autoroute|autobahn|autovia|autopista|strada statale|strada provinciale|strada regionale|route departementale|route nationale|droga krajowa|droga wojewodzka|bundesstrasse|landstrasse|כביש)\s+[a-z0-9\-/]+"
    r")$",
    re.IGNORECASE,
)

# Purely numeric / ordinal street names (excluding historical dates like "20 Settembre", "14 Juillet", "3 Maja")
_PURE_NUMERIC_RE = re.compile(r"^(?:\d+[a-z]?|\d+(?:st|nd|rd|th|e|er|eme|o|a|°|º|ª)?)$", re.IGNORECASE)

# Special Latin transliterations before NFKD stripping
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
    "đ": "d",
    "Đ": "d",
})

_LANG_CACHE: dict[str, dict[str, list[str]]] | None = None


def load_lang_rules(path: Path = LANG_CONFIG_PATH) -> dict[str, dict[str, list[str]]]:
    global _LANG_CACHE
    if _LANG_CACHE is not None and path == LANG_CONFIG_PATH:
        return _LANG_CACHE
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = {k: v for k, v in raw.items() if not k.startswith("_")}
    if path == LANG_CONFIG_PATH:
        _LANG_CACHE = rules
    return rules


def fold_for_key(text: str) -> str:
    """Fold case, diacritics, and punctuation for language-agnostic canonical key matching."""
    s = unicodedata.normalize("NFC", text.strip())
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
    s = re.sub(r"[.,;:!?()\[\]{}]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_display(text: str) -> str:
    """Clean display text while preserving natural capitalization & diacritics."""
    s = unicodedata.normalize("NFC", text.strip())
    s = _HE_NIQQUD_RE.sub("", s)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    return re.sub(r"\s+", " ", s).strip()


def _strip_prefix_list(s: str, prefixes: list[str]) -> str:
    """Strip the longest matching prefix token followed by space or apostrophe."""
    for pref in sorted(prefixes, key=len, reverse=True):
        p = fold_for_key(pref)
        if not p:
            continue
        if pref.endswith("'"):
            if s.startswith(p) and len(s) > len(p):
                return s[len(p):].strip()
        else:
            if s.startswith(p + " ") and len(s) > len(p) + 1:
                return s[len(p) + 1:].strip()
    return s


def _strip_suffix_list(s: str, suffixes: list[str]) -> str:
    for suf in sorted(suffixes, key=len, reverse=True):
        sf = fold_for_key(suf)
        if not sf:
            continue
        if s.endswith(" " + sf) and len(s) > len(sf) + 1:
            return s[: -(len(sf) + 1)].strip()
    return s


def _strip_fused_german_suffix(s: str, fused_suffixes: list[str]) -> str:
    """Strip fused German compound road suffixes (e.g. 'goethestrasse' -> 'goethe')."""
    tokens = s.split()
    if not tokens:
        return s
    last = tokens[-1]
    for suf in sorted(fused_suffixes, key=len, reverse=True):
        sf = fold_for_key(suf)
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

    # Drop motorway/highway route references (e.g. "SS16", "A1", "D940", "SP12")
    if _HIGHWAY_CODE_RE.match(fold_for_key(no_paren)):
        return "", "", False

    if rules is None:
        rules = load_lang_rules()

    prefixes: list[str] = []
    connectors: list[str] = []
    suffixes: list[str] = []
    fused_suffixes: list[str] = []
    for lang in languages:
        lr = rules.get(lang, {})
        prefixes.extend(lr.get("prefixes", []))
        connectors.extend(lr.get("connectors", []))
        suffixes.extend(lr.get("suffixes", []))
        fused_suffixes.extend(lr.get("fused_suffixes", []))

    key = fold_for_key(no_paren)
    if not key:
        return "", "", False

    # Strip road-type prefix (up to twice for nested forms like "Via Strada ...")
    for _ in range(2):
        nxt = _strip_prefix_list(key, prefixes)
        if nxt == key:
            break
        key = nxt

    # Strip trailing road-type suffix (e.g. German "Berliner Strasse", Basque "Kale")
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
