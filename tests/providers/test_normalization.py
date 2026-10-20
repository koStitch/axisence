"""Tests for the display names and keys that group providers into competitors."""

import difflib

import pytest

from axisence.providers import normalization

SIMILAR_BY_RATIO = ("BRIGHT HORIZON ACADEMY", "BRIGHT HORIZONS ACADEMY")
UNRELATED = ("SUNSHINE KIDS", "LITTLE SCHOLARS ACADEMY")


def test_display_name_removes_double_quotation_marks_and_collapses_whitespace():
    assert (
        normalization.display_name('"""Almost Home Child Care"""')
        == "Almost Home Child Care"
    )
    assert (
        normalization.display_name('"LITTLE GENIUS"  DAY CARE')
        == "LITTLE GENIUS DAY CARE"
    )


@pytest.mark.parametrize(
    ("original_name", "expected"),
    [
        ("The Kids' Daycare, Inc.", "KIDS DAY CARE"),
        ("Kid\N{RIGHT SINGLE QUOTATION MARK}s World", "KIDS WORLD"),
        ("ABC Learning Center L.L.C.", "ABC LEARNING CENTER"),
        ("A & B Day Care Corp", "A B DAY CARE"),
        ("THE", ""),
        ("Theater Kids", "THEATER KIDS"),
    ],
)
def test_name_key(original_name, expected):
    assert normalization.name_key(original_name) == expected


@pytest.mark.parametrize(
    ("house_number_and_street", "expected"),
    [
        ("45-12 BELL BLVD", "45-12 BELL BLVD"),
        ("45 - 12 Bell Boulevard", "45-12 BELL BLVD"),
        ("150 WEST 4TH STREET, 2ND FL", "150 W 4 ST"),
        ("150 West 4 Street 2nd Floor", "150 W 4 ST"),
        ("123  MAIN   STREET", "123 MAIN ST"),
        ("123 Main St., Suite 3", "123 MAIN ST"),
        ("123 MAIN STREET SUITE 103-104", "123 MAIN ST"),
        ("123 MAIN ST #2", "123 MAIN ST"),
        ("123 MAIN ST APT 1F", "123 MAIN ST"),
        ("123 MAIN ST BLDG A", "123 MAIN ST"),
        ("100 WING ROAD", "100 WING RD"),
        ("401 West 49th Street", "401 W 49 ST"),
        ("12 UNITY AVENUE", "12 UNITY AVE"),
        ("", ""),
    ],
)
def test_address_key(house_number_and_street, expected):
    assert normalization.address_key(house_number_and_street) == expected


@pytest.mark.parametrize(
    ("published_zip_code", "expected"),
    [
        ("11217", "11217"),
        ("112170000", "11217"),
        ("11217-0000", "11217"),
        ("1121", ""),
        ("ABCDE", ""),
        ("", ""),
    ],
)
def test_zip_key(published_zip_code, expected):
    assert normalization.zip_key(published_zip_code) == expected


def test_street_for_geocoding_keeps_published_spelling_without_unit_text():
    assert (
        normalization.street_for_geocoding("401 West 49th Street, Suite 3")
        == "401 West 49th Street"
    )


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("X DAY CARE", "X DAY CARE CENTER", True),
        (*SIMILAR_BY_RATIO, True),
        (*UNRELATED, False),
        ("", "X DAY CARE", False),
    ],
    ids=["containment", "ratio", "unrelated", "blank"],
)
def test_name_keys_are_similar(first, second, expected):
    assert normalization.name_keys_are_similar(first, second) is expected
    if first and second and first not in second and second not in first:
        # Without containment the ratio alone decides, on the expected side.
        ratio = difflib.SequenceMatcher(None, first, second).ratio()
        assert (ratio >= normalization.SIMILARITY_RATIO_THRESHOLD) is expected
