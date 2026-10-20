"""The Census figures a report shows, per vintage, checked against the stored labels
before use. A new vintage is added only after its codes and labels are checked by hand,
because the Census Bureau can renumber codes between vintages.
"""

import dataclasses
import enum
from collections.abc import Mapping


class FigureDisplay(enum.Enum):
    COUNT = enum.auto()
    COUNT_AND_PERCENT = enum.auto()
    PERCENT = enum.auto()
    DOLLARS = enum.auto()


@dataclasses.dataclass(frozen=True, slots=True)
class FigureDefinition:
    code: str
    table: str
    expected_estimate_label: str
    display: FigureDisplay
    heading: str
    # May hold {vintage}, replaced by the vintage's year.
    report_label: str
    percent_base: str = ""
    # A median in the highest interval is published as the cap plus one.
    top_coding_cap: int | None = None
    # A median in the lowest interval is published as the floor minus one.
    bottom_coding_floor: int | None = None


_CHILDREN = "Children"
_WORKING_PARENTS = "Working parents"
_HOUSEHOLDS_AND_SCHOOLING = "Households and schooling"
_INCOME_AND_POVERTY = "Income and poverty"
_HOUSING = "Housing"

_INCOME_LABEL_PREFIX = (
    "Estimate!!INCOME AND BENEFITS (IN 2024 INFLATION-ADJUSTED DOLLARS)!!"
)

FIGURE_LISTS: Mapping[int, tuple[FigureDefinition, ...]] = {
    2024: (
        FigureDefinition(
            code="DP05_0001",
            table="DP05",
            expected_estimate_label="Estimate!!SEX AND AGE!!Total population",
            display=FigureDisplay.COUNT,
            heading=_CHILDREN,
            report_label="Total population",
        ),
        FigureDefinition(
            code="DP05_0005",
            table="DP05",
            expected_estimate_label=(
                "Estimate!!SEX AND AGE!!Total population!!Under 5 years"
            ),
            display=FigureDisplay.COUNT_AND_PERCENT,
            heading=_CHILDREN,
            report_label="Children under 5 years (Census age bracket)",
            percent_base="of total population",
        ),
        FigureDefinition(
            code="DP05_0006",
            table="DP05",
            expected_estimate_label=(
                "Estimate!!SEX AND AGE!!Total population!!5 to 9 years"
            ),
            display=FigureDisplay.COUNT_AND_PERCENT,
            heading=_CHILDREN,
            report_label="Children 5 to 9 years (Census age bracket)",
            percent_base="of total population",
        ),
        FigureDefinition(
            code="DP03_0014",
            table="DP03",
            expected_estimate_label=(
                "Estimate!!EMPLOYMENT STATUS!!Own children of the householder under "
                "6 years"
            ),
            display=FigureDisplay.COUNT,
            heading=_WORKING_PARENTS,
            report_label="Own children of the householder under 6 years",
        ),
        FigureDefinition(
            code="DP03_0015",
            table="DP03",
            expected_estimate_label=(
                "Estimate!!EMPLOYMENT STATUS!!Own children of the householder under "
                "6 years!!All parents in family in labor force"
            ),
            display=FigureDisplay.PERCENT,
            heading=_WORKING_PARENTS,
            report_label="With all parents in the family in the labor force",
            percent_base="of own children under 6 years",
        ),
        FigureDefinition(
            code="DP02_0001",
            table="DP02",
            expected_estimate_label="Estimate!!HOUSEHOLDS BY TYPE!!Total households",
            display=FigureDisplay.COUNT,
            heading=_HOUSEHOLDS_AND_SCHOOLING,
            report_label="Total households",
        ),
        FigureDefinition(
            code="DP02_0014",
            table="DP02",
            expected_estimate_label=(
                "Estimate!!HOUSEHOLDS BY TYPE!!Total households!!Households with one "
                "or more people under 18 years"
            ),
            display=FigureDisplay.PERCENT,
            heading=_HOUSEHOLDS_AND_SCHOOLING,
            report_label="Households with one or more people under 18 years",
            percent_base="of households",
        ),
        FigureDefinition(
            code="DP02_0054",
            table="DP02",
            expected_estimate_label=(
                "Estimate!!SCHOOL ENROLLMENT!!Population 3 years and over enrolled in "
                "school!!Nursery school, preschool"
            ),
            display=FigureDisplay.COUNT,
            heading=_HOUSEHOLDS_AND_SCHOOLING,
            report_label="Enrolled in nursery school or preschool",
        ),
        FigureDefinition(
            code="DP02_0114",
            table="DP02",
            expected_estimate_label=(
                "Estimate!!LANGUAGE SPOKEN AT HOME!!Population 5 years and "
                "over!!Language other than English"
            ),
            display=FigureDisplay.PERCENT,
            heading=_HOUSEHOLDS_AND_SCHOOLING,
            report_label="Speak a language other than English at home",
            percent_base="of population 5 years and over",
        ),
        FigureDefinition(
            code="DP03_0062",
            table="DP03",
            expected_estimate_label=(
                f"{_INCOME_LABEL_PREFIX}Total households!!Median household income "
                "(dollars)"
            ),
            display=FigureDisplay.DOLLARS,
            heading=_INCOME_AND_POVERTY,
            report_label="Median household income ({vintage} dollars)",
            top_coding_cap=250000,
            bottom_coding_floor=2500,
        ),
        FigureDefinition(
            code="DP03_0086",
            table="DP03",
            expected_estimate_label=(
                f"{_INCOME_LABEL_PREFIX}Families!!Median family income (dollars)"
            ),
            display=FigureDisplay.DOLLARS,
            heading=_INCOME_AND_POVERTY,
            report_label="Median family income ({vintage} dollars)",
            top_coding_cap=250000,
            bottom_coding_floor=2500,
        ),
        FigureDefinition(
            code="DP03_0121",
            table="DP03",
            expected_estimate_label=(
                "Estimate!!PERCENTAGE OF FAMILIES AND PEOPLE WHOSE INCOME IN THE PAST "
                "12 MONTHS IS BELOW THE POVERTY LEVEL!!All families!!With related "
                "children of the householder under 18 years!!With related children "
                "of the householder under 5 years only"
            ),
            display=FigureDisplay.PERCENT,
            heading=_INCOME_AND_POVERTY,
            report_label="Families with children under 5 only: below the poverty level",
        ),
        FigureDefinition(
            code="DP03_0021",
            table="DP03",
            expected_estimate_label=(
                "Estimate!!COMMUTING TO WORK!!Workers 16 years and over!!Public "
                "transportation"
            ),
            display=FigureDisplay.PERCENT,
            heading=_INCOME_AND_POVERTY,
            report_label="Workers commuting by public transportation",
            percent_base="of workers 16 years and over",
        ),
        FigureDefinition(
            code="DP04_0047",
            table="DP04",
            expected_estimate_label=(
                "Estimate!!HOUSING TENURE!!Occupied housing units!!Renter-occupied"
            ),
            display=FigureDisplay.PERCENT,
            heading=_HOUSING,
            report_label="Renter-occupied housing units",
            percent_base="of occupied housing units",
        ),
        FigureDefinition(
            code="DP04_0134",
            table="DP04",
            expected_estimate_label=(
                "Estimate!!GROSS RENT!!Occupied units paying rent!!Median (dollars)"
            ),
            display=FigureDisplay.DOLLARS,
            heading=_HOUSING,
            report_label="Median gross rent",
            top_coding_cap=3500,
        ),
    ),
}
