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
_PURE_NUMERIC_RE = re.compile(
    r"^(?:\d+[a-z]?|\d+(?:st|nd|rd|th|e|er|eme|o|a|°|º|ª)?|\d+\s+(?:j|ja|e))$",
    re.IGNORECASE,
)

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
    "ð": "d",
    "Ð": "d",
    "þ": "th",
    "Þ": "th",
    # Serbian / Macedonian / Ukrainian / Belarusian / Bulgarian / Russian Cyrillic -> Latin
    "а": "a", "А": "a",
    "б": "b", "Б": "b",
    "в": "v", "В": "v",
    "г": "g", "Г": "g",
    "ґ": "g", "Ґ": "g",
    "д": "d", "Д": "d",
    "ђ": "dj", "Ђ": "dj",
    "ѓ": "g", "Ѓ": "g",
    "е": "e", "Е": "e",
    "ё": "e", "Ё": "e",
    "є": "je", "Є": "je",
    "ж": "z", "Ж": "z",
    "з": "z", "З": "z",
    "ѕ": "dz", "Ѕ": "dz",
    "и": "i", "И": "i",
    "і": "i", "І": "i",
    "ї": "ji", "Ї": "ji",
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
    "ў": "u", "Ў": "u",
    "ф": "f", "Ф": "f",
    "х": "h", "Х": "h",
    "ц": "c", "Ц": "c",
    "ч": "c", "Ч": "c",
    "џ": "dz", "Џ": "dz",
    "ш": "s", "Ш": "s",
    "щ": "st", "Щ": "st",
    "ъ": "a", "Ъ": "a",
    "ы": "y", "Ы": "y",
    "ь": "", "Ь": "",
    "э": "e", "Э": "e",
    "ю": "ju", "Ю": "ju",
    "я": "ja", "Я": "ja",
    # Greek -> Latin (accents stripped by NFKD after NFC/translation)
    "α": "a", "Α": "a", "ά": "a", "Ά": "a",
    "β": "v", "Β": "v",
    "γ": "g", "Γ": "g",
    "δ": "d", "Δ": "d",
    "ε": "e", "Ε": "e", "έ": "e", "Έ": "e",
    "ζ": "z", "Ζ": "z",
    "η": "i", "Η": "i", "ή": "i", "Ή": "i",
    "θ": "th", "Θ": "th",
    "ι": "i", "Ι": "i", "ί": "i", "Ί": "i", "ϊ": "i", "ΐ": "i",
    "κ": "k", "Κ": "k",
    "λ": "l", "Λ": "l",
    "μ": "m", "Μ": "m",
    "ν": "n", "Ν": "n",
    "ξ": "x", "Ξ": "x",
    "ο": "o", "Ο": "o", "ό": "o", "Ό": "o",
    "π": "p", "Π": "p",
    "ρ": "r", "Ρ": "r",
    "σ": "s", "ς": "s", "Σ": "s",
    "τ": "t", "Τ": "t",
    "υ": "y", "Υ": "y", "ύ": "y", "Ύ": "y", "ϋ": "y", "ΰ": "y",
    "φ": "f", "Φ": "f",
    "χ": "ch", "Χ": "ch",
    "ψ": "ps", "Ψ": "ps",
    "ω": "o", "Ω": "o", "ώ": "o", "Ώ": "o",
})

# Nouns that should not lose a leading directional (e.g. "West End", "North Side", "East River")
_PROTECTED_AFTER_DIR = {
    "end", "side", "point", "park", "shore", "view", "river", "harbor",
    "bay", "island", "field", "wood", "hill", "hills", "gate", "bridge",
    "port", "beach", "lake", "creek", "brook", "haven", "ridge", "meadow",
}

_LANG_CACHE: dict[str, dict[str, list[str]]] | None = None
_PREPARED_CACHE: dict[
    tuple[int, tuple[str, ...]],
    tuple[list[tuple[str, bool]], list[tuple[str, bool]], list[str], list[str], list[str], list[str], list[str]],
] = {}
_SLASH_SEP_RE = re.compile(r"\s+/\s+")
_DASH_SEP_RE = re.compile(r"\s+[-–—]\s+")
_CYRILLIC_LANG_ORDER = ("ru", "sr", "mk", "bg", "uk")


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
) -> tuple[list[tuple[str, bool]], list[tuple[str, bool]], list[str], list[str], list[str], list[str], list[str]]:
    cache_key = (id(rules), tuple(languages))
    cached = _PREPARED_CACHE.get(cache_key)
    if cached is not None:
        return cached

    raw_pref, raw_conn, raw_suf, raw_dir, raw_fused, raw_titles, raw_titles_multi = (
        [], [], [], [], [], [], []
    )
    for lang in languages:
        lr = rules.get(lang, {})
        raw_pref.extend(lr.get("prefixes", []))
        raw_conn.extend(lr.get("connectors", []))
        raw_suf.extend(lr.get("suffixes", []))
        raw_dir.extend(lr.get("directionals", []))
        raw_fused.extend(lr.get("fused_suffixes", []))
        raw_titles.extend(lr.get("titles", []))
        raw_titles_multi.extend(lr.get("titles_multi", []))

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
        _prep_plain(raw_titles),
        _prep_plain(raw_titles_multi),
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
            # Clean linking 's' only for common Germanic/Dutch/Nordic compound patterns
            if stem.endswith((
                "hofs", "parks", "walds", "bergs", "dorfs", "markts", "turms", "bachs", "felds",
                "dorps", "stations", "konings", "kerks", "slots", "stads", "kyrks", "skogs",
            )):
                stem = stem[:-1]
            if len(stem) >= 3:
                tokens[-1] = stem
                return " ".join(tokens)
    return s


def _strip_titles(s: str, titles: list[str], titles_multi: list[str]) -> str:
    """Strip honorific/professional titles while guarding generic single-word nouns."""
    if not titles and not titles_multi:
        return s
    for _ in range(2):
        changed = False
        for t in titles:
            if s.startswith(t + " "):
                rem = s[len(t) + 1 :].strip()
                if len(rem) >= 2:
                    s = rem
                    changed = True
                    break
        if changed:
            continue
        for t in titles_multi:
            if s.startswith(t + " "):
                rem = s[len(t) + 1 :].strip()
                # Require at least 2 remaining tokens so "Don Bosco" / "Doctor Bos" stay distinct
                if len(rem.split()) >= 2:
                    s = rem
                    changed = True
                    break
        if not changed:
            break
    return s


def _strip_affixes(
    key: str,
    prefixes: list[tuple[str, bool]],
    connectors: list[tuple[str, bool]],
    suffixes: list[str],
    directionals: list[str],
    fused_suffixes: list[str],
) -> str:
    """Strip road-type prefixes, suffixes, directionals, German fused suffixes, and connectors."""
    for _ in range(2):
        nxt = _strip_prefix_list(key, prefixes)
        if nxt == key:
            break
        key = nxt

    for _ in range(2):
        nxt = _strip_suffix_list(key, suffixes)
        if nxt == key:
            break
        key = nxt

    if directionals:
        dir_pref = [(d, False) for d in directionals]
        cand = _strip_prefix_list(key, dir_pref)
        if cand != key and cand not in _PROTECTED_AFTER_DIR:
            key = cand
        cand_suf = _strip_suffix_list(key, directionals)
        if cand_suf != key and cand_suf not in _PROTECTED_AFTER_DIR:
            key = cand_suf
        key = _strip_suffix_list(key, suffixes)

    if fused_suffixes:
        key = _strip_fused_german_suffix(key, fused_suffixes)

    if connectors:
        key = _strip_prefix_list(key, connectors)

    return key


def _scripts_of(text: str) -> set[str]:
    """Identify coarse writing systems present in raw text before transliteration."""
    scripts: set[str] = set()
    for ch in text:
        code = ord(ch)
        if 0x0590 <= code <= 0x05FF:
            scripts.add("he")
        elif 0x0600 <= code <= 0x06FF:
            scripts.add("ar")
        elif 0x0400 <= code <= 0x04FF:
            scripts.add("cyrl")
        elif 0x0370 <= code <= 0x03FF:
            scripts.add("grek")
        elif ("a" <= ch.lower() <= "z") or (0x00C0 <= code <= 0x024F):
            scripts.add("latn")
    return scripts


def _detect_side_matches(
    raw_side: str,
    languages: list[str],
    rules: dict[str, dict[str, list[str]]],
    scripts: set[str],
) -> tuple[dict[str, tuple[str, bool]], str | None]:
    """Match a bilingual street side against country languages, falling back to other known rules."""
    s = fold_for_key(raw_side)
    matches: dict[str, tuple[str, bool]] = {}
    if not s:
        return matches, None

    def _match_lang(
        lang: str,
        min_affix_len: int = 2,
        allow_fused: bool = True,
        min_fused_len: int = 3,
    ) -> tuple[str, bool] | None:
        prefs, conns, sufs, _, fused, _, _ = _prepare_lang_affixes([lang], rules)
        for p, is_apos in prefs:
            if len(p) < min_affix_len:
                continue
            matched = (
                (s.startswith(p) and len(s) > len(p))
                if is_apos
                else (s.startswith(p + " ") and len(s) > len(p) + 1)
            )
            if matched:
                rem = (s[len(p) :] if is_apos else s[len(p) + 1 :]).strip()
                conn_ok = bool(conns and _strip_prefix_list(rem, conns) != rem)
                return ("prefix", conn_ok)
        for sf in sufs:
            if len(sf) >= min_affix_len and s.endswith(" " + sf) and len(s) > len(sf) + 1:
                return ("suffix", False)
        if fused and allow_fused:
            last = s.split()[-1]
            for sf in fused:
                if len(sf) >= min_fused_len and last.endswith(sf) and len(last) >= len(sf) + 3:
                    return ("fused", False)
        return None

    for lang in languages:
        hit = _match_lang(lang, min_affix_len=2, allow_fused=True, min_fused_len=3)
        if hit is not None:
            matches[lang] = hit

    if not matches:
        latin_only = scripts == {"latn"}
        for lang in rules:
            if lang in languages:
                continue
            # Require >=3-char affixes (and Latin script + >=4-char fused) outside country languages
            hit = _match_lang(lang, min_affix_len=3, allow_fused=latin_only, min_fused_len=4)
            if hit is not None:
                matches[f"other:{lang}"] = hit
                break

    script_lang: str | None = None
    if "he" in scripts and "ar" not in scripts:
        script_lang = "he"
    elif "ar" in scripts and "he" not in scripts:
        script_lang = "ar"
    elif "grek" in scripts:
        script_lang = "el"
    elif "cyrl" in scripts:
        for cl in _CYRILLIC_LANG_ORDER:
            if cl in languages:
                script_lang = cl
                break
        if script_lang is None:
            script_lang = "cyrl"

    return matches, script_lang


def _pick_side_lang(
    matches: dict[str, tuple[str, bool]],
    other_matches: dict[str, tuple[str, bool]],
    script_lang: str | None,
    languages: list[str],
) -> tuple[str | None, str | None]:
    """Disambiguate shared affixes (e.g. 'Boulevard' in fr+nl) and choose best (kind, lang)."""
    if not matches:
        return None, script_lang

    confirmed = [lang for lang, (_, conn_ok) in matches.items() if conn_ok]
    if confirmed:
        cands = confirmed
    else:
        cands = list(matches.keys())
        if len(cands) > 1 and len(other_matches) == 1:
            other_only = next(iter(other_matches.keys()))
            narrowed = [lang for lang in cands if lang != other_only]
            if narrowed:
                cands = narrowed

    def _lang_rank(lang: str) -> int:
        return languages.index(lang) if lang in languages else 999

    best_lang = min(cands, key=_lang_rank)
    return matches[best_lang][0], best_lang


def _resolve_bilingual(
    raw_name: str,
    languages: list[str],
    rules: dict[str, dict[str, list[str]]],
) -> str:
    """Resolve dual-language street names ('Rue X - Xstraat', 'Chemin A / Aweg') to one canonical side."""
    if " / " not in raw_name and " - " not in raw_name and " – " not in raw_name and " — " not in raw_name:
        return raw_name

    cur = raw_name
    for _ in range(2):
        parts = _SLASH_SEP_RE.split(cur)
        sep = "/"
        if len(parts) != 2:
            parts = _DASH_SEP_RE.split(cur)
            sep = "-"
        if len(parts) != 2:
            break
        left, right = parts[0].strip(), parts[1].strip()
        if not left or not right:
            break

        sc_l, sc_r = _scripts_of(left), _scripts_of(right)
        diff_script = bool(sc_l and sc_r and sc_l.isdisjoint(sc_r))

        m_l, sl_l = _detect_side_matches(left, languages, rules, sc_l)
        m_r, sl_r = _detect_side_matches(right, languages, rules, sc_r)
        k_l, lang_l = _pick_side_lang(m_l, m_r, sl_l, languages)
        k_r, lang_r = _pick_side_lang(m_r, m_l, sl_r, languages)

        fold_l, fold_r = fold_for_key(left), fold_for_key(right)
        same_fold = fold_l == fold_r
        same_core = False
        if not same_fold and k_l is not None and k_r is not None:
            prefs, conns, sufs, dirs, fused, _, _ = _prepare_lang_affixes(languages, rules)
            core_l = _strip_affixes(fold_l, prefs, conns, sufs, dirs, fused)
            core_r = _strip_affixes(fold_r, prefs, conns, sufs, dirs, fused)
            same_core = len(core_l) >= 2 and core_l == core_r

        is_dual = (
            diff_script
            or same_fold
            or same_core
            or (sep == "/" and (k_r is not None or k_l in ("suffix", "fused")))
            or (sep == "-" and (k_r is not None or k_l in ("suffix", "fused")) and (lang_l != lang_r or lang_l is None))
        )
        if not is_dual:
            break

        rank_l = languages.index(lang_l) if lang_l in languages else 999
        rank_r = languages.index(lang_r) if lang_r in languages else 999
        primary, fallback = (right, left) if rank_r < rank_l else (left, right)

        primary_fold = fold_for_key(primary)
        if not primary_fold or _HIGHWAY_CODE_RE.match(primary_fold):
            cur = fallback
        else:
            cur = primary

    return cur


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

    if rules is None:
        rules = load_lang_rules()

    chosen = _resolve_bilingual(no_paren, languages, rules)

    key = fold_for_key(chosen)
    if not key:
        return "", "", False

    # Drop motorway/highway route references (e.g. "SS16", "A1", "D940", "SP12", "NY 17")
    if _HIGHWAY_CODE_RE.match(key):
        return "", "", False

    prefixes, connectors, suffixes, directionals, fused_suffixes, titles, titles_multi = (
        _prepare_lang_affixes(languages, rules)
    )

    key = _strip_affixes(key, prefixes, connectors, suffixes, directionals, fused_suffixes)

    # Strip honorific/academic/military titles (must stay AFTER connectors so "de Gaulle" keeps "de")
    key = _strip_titles(key, titles, titles_multi)

    # Strip trailing Hebrew alley/segment numbers or standalone trailing 1-3 digit sub-numbers
    if "he" in languages:
        key = re.sub(r"\s+סמט(?:ה|ת)?\s*\d+\s*$", "", key).strip()
        key = re.sub(r"\s+סמ'?\s*\d+\s*$", "", key).strip()

    key = re.sub(r"\s+", " ", key).strip()
    if not key:
        return "", "", False

    # Check if purely numeric / ordinal street (e.g. "42", "1st", "10a")
    if _PURE_NUMERIC_RE.match(key):
        return "", clean_display(chosen), True

    # Drop single-character leftovers
    if len(key) < 2:
        return "", "", False

    return key, clean_display(chosen), False
