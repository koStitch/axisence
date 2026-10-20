"""Census tabulation areas, data profile variables, and their figures per vintage."""

from django.contrib.gis.db import models


class CensusTable(models.TextChoices):
    DP02 = "DP02", "DP02 Selected social characteristics"
    DP03 = "DP03", "DP03 Selected economic characteristics"
    DP04 = "DP04", "DP04 Selected housing characteristics"
    DP05 = "DP05", "DP05 Demographic and housing estimates"


class TabulationArea(models.Model):
    """A 2020 ZIP Code Tabulation Area, which approximates but is not a zip code.

    The code is text, because codes can start with zero.
    """

    code = models.CharField(max_length=5, unique=True)
    boundary = models.MultiPolygonField(srid=4326, null=True, blank=True)

    class Meta:
        ordering = ("code",)

    def __str__(self) -> str:
        return self.code


class CensusVariable(models.Model):
    """One data profile variable of one vintage, such as DP05_0005.

    Codes can be renumbered between vintages, so variables and their labels are kept
    per vintage.
    """

    vintage = models.PositiveSmallIntegerField()
    table = models.CharField(max_length=4, choices=CensusTable.choices)
    code = models.CharField(max_length=12)
    estimate_label = models.TextField(blank=True)
    percent_label = models.TextField(blank=True)

    class Meta:
        ordering = ("vintage", "code")
        constraints = (
            models.UniqueConstraint(
                fields=("vintage", "code"), name="unique_census_variable_per_vintage"
            ),
        )

    def __str__(self) -> str:
        return f"{self.code} ({self.vintage})"


class CensusFigure(models.Model):
    """The estimate, percent, and their margins of error of one variable in one area.

    Null means the Census Bureau published no usable value.
    """

    tabulation_area = models.ForeignKey(
        TabulationArea, on_delete=models.PROTECT, related_name="census_figures"
    )
    variable = models.ForeignKey(
        CensusVariable, on_delete=models.PROTECT, related_name="figures"
    )
    estimate = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    estimate_margin_of_error = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    percent = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    percent_margin_of_error = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("tabulation_area", "variable"),
                name="unique_census_figure_per_area_variable",
            ),
        )

    def __str__(self) -> str:
        return f"{self.variable.code} for {self.tabulation_area.code}"
