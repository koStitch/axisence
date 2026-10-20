"""Normalized keys and display names that group providers into competitors.

Keys are recomputed from the published values on every run.
"""

import difflib
import re

SIMILARITY_RATIO_THRESHOLD = 0.8

LEGAL_FORM_WORDS = frozenset({"INC", "LLC", "CORP", "CORPORATION", "LTD", "CO"})

# USPS Publication 28 standard abbreviations of the street types and directions that
# appear in New York addresses.
STREET_ABBREVIATIONS = {
    "ALLEY": "ALY",
    "AVENUE": "AVE",
    "AV": "AVE",
    "AVEN": "AVE",
    "AVENU": "AVE",
    "AVN": "AVE",
    "AVNUE": "AVE",
    "BOULEVARD": "BLVD",
    "BOUL": "BLVD",
    "BOULV": "BLVD",
    "BRIDGE": "BRG",
    "CENTER": "CTR",
    "CENTRE": "CTR",
    "CIRCLE": "CIR",
    "COURT": "CT",
    "CRESCENT": "CRES",
    "DRIVE": "DR",
    "DRIV": "DR",
    "DRV": "DR",
    "EXPRESSWAY": "EXPY",
    "EXPRESS": "EXPY",
    "EXPW": "EXPY",
    "EXPWY": "EXPY",
    "FORT": "FT",
    "FREEWAY": "FWY",
    "HEIGHTS": "HTS",
    "HIGHWAY": "HWY",
    "HIGHWY": "HWY",
    "ISLAND": "IS",
    "LANE": "LN",
    "MOUNT": "MT",
    "PARKWAY": "PKWY",
    "PARKWY": "PKWY",
    "PKY": "PKWY",
    "PLACE": "PL",
    "PLAZA": "PLZ",
    "PLZA": "PLZ",
    "POINT": "PT",
    "ROAD": "RD",
    "ROUTE": "RTE",
    "SQUARE": "SQ",
    "SQR": "SQ",
    "STREET": "ST",
    "STR": "ST",
    "STRT": "ST",
    "TERRACE": "TER",
    "TERR": "TER",
    "TRAIL": "TRL",
    "TURNPIKE": "TPKE",
    "TRNPK": "TPKE",
    "TURNPK": "TPKE",
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
}

_DELETED_CHARACTERS = str.maketrans("", "", "'\u2018\u2019`.")
_DOUBLE_QUOTES = str.maketrans("", "", '"\u201c\u201d')
_WHITESPACE = re.compile(r"\s+")
_QUEENS_HOUSE_NUMBER = re.compile(r"^\s*(\d+[A-Z]?)\s*-\s*(\d+[A-Z]?)(?=\s|$)")
_UNIT_DESIGNATOR_PATTERNS = (
    r"\bP\s?O\s+BOX\s+\S+",
    r"\b(?:SUITE|STE|APT|APARTMENT|UNIT|RM|ROOM|BLDG|BUILDING|STORE)(?:\s*#\s*|\s+)"
    r"(?:\d[A-Z0-9]*|[A-Z])(?:-[A-Z0-9]+)?\b",
    r"#\s*[A-Z0-9]+(?:-[A-Z0-9]+)?",
    r"\b\d+(?:ST|ND|RD|TH)?\s*(?:FL|FLR|FLOOR)\b",
    r"\b(?:FL|FLR|FLOOR)\s*\d+\b",
    r"\bGROUND\s+(?:FL|FLR|FLOOR)\b",
    r"\b(?:BASEMENT|BSMT)\b",
)
_UNIT_DESIGNATORS = tuple(re.compile(pattern) for pattern in _UNIT_DESIGNATOR_PATTERNS)
_UNIT_DESIGNATORS_ANY_CASE = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _UNIT_DESIGNATOR_PATTERNS
)
_ORDINAL_SUFFIX = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\b")
_FIVE_DIGIT_ZIP = re.compile(r"^\d{5}$")
_NINE_DIGIT_ZIP = re.compile(r"^(\d{5})-?\d{4}$")


def display_name(original_name: str) -> str:
    """Return the name without double quotation marks, with whitespace collapsed."""
    return _WHITESPACE.sub(" ", original_name.translate(_DOUBLE_QUOTES)).strip()


def name_key(original_name: str) -> str:
    """Return the key that makes one operator's names equal across both regulators."""
    text = original_name.upper().replace("DAYCARE", "DAY CARE")
    text = _replace_punctuation(text.translate(_DELETED_CHARACTERS))
    tokens = [token for token in text.split() if token not in LEGAL_FORM_WORDS]
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    return " ".join(tokens)


def address_key(house_number_and_street: str) -> str:
    """Return the key that makes one location's address equal across both regulators."""
    text = (
        house_number_and_street.upper().split(",", 1)[0].translate(_DELETED_CHARACTERS)
    )
    house_number = ""
    match = _QUEENS_HOUSE_NUMBER.match(text)
    if match:
        house_number = f"{match.group(1)}-{match.group(2)}"
        text = text[match.end() :]
    for pattern in _UNIT_DESIGNATORS:
        text = pattern.sub(" ", text)
    text = _ORDINAL_SUFFIX.sub(r"\1", _replace_punctuation(text))
    tokens = [STREET_ABBREVIATIONS.get(token, token) for token in text.split()]
    return " ".join([house_number, *tokens] if house_number else tokens)


def zip_key(published_zip_code: str) -> str:
    """Return the five-digit zip code, or "" when the published value is not one."""
    value = published_zip_code.strip()
    if _FIVE_DIGIT_ZIP.match(value):
        return value
    match = _NINE_DIGIT_ZIP.match(value)
    return match.group(1) if match else ""


def street_for_geocoding(house_number_and_street: str) -> str:
    """Return the published house number and street without unit text."""
    text = house_number_and_street.split(",", 1)[0]
    for pattern in _UNIT_DESIGNATORS_ANY_CASE:
        text = pattern.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def name_keys_are_similar(first: str, second: str) -> bool:
    """Return whether two name keys are close enough to ask a researcher."""
    if not first or not second:
        return False
    if first in second or second in first:
        return True
    ratio = difflib.SequenceMatcher(None, first, second).ratio()
    return ratio >= SIMILARITY_RATIO_THRESHOLD


def _replace_punctuation(text: str) -> str:
    return "".join(
        character if character.isalnum() or character.isspace() else " "
        for character in text
    )
