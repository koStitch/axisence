"""Read tabulation-area boundaries from the Census cartographic boundary shapefile.

The file is read in place inside its zip through GDAL's /vsizip/ virtual path and
transformed from NAD83 to WGS 84.
"""

import dataclasses
import zipfile
from collections.abc import Collection
from pathlib import Path

from django.contrib.gis.gdal import CoordTransform, DataSource, SpatialReference
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon

from axisence.ingestion.errors import IngestionError

BOUNDARY_FILE_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip"
)
BOUNDARY_FILE_NAME = "cb_2020_us_zcta520_500k.zip"
BOUNDARY_CODE_FIELD = "ZCTA5CE20"
BOUNDARY_RAW_DIRECTORY_NAME = "census-boundaries"

WGS_84_SRID = 4326


@dataclasses.dataclass(frozen=True, slots=True)
class BoundaryReadResult:
    features_read: int
    boundaries: dict[str, MultiPolygon]
    missing_codes: tuple[str, ...]
    unusable_codes: tuple[str, ...]


def shapefile_member(zip_path: Path) -> str:
    """Return the name of the only shapefile inside the zip."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        raise IngestionError("The boundary file is not a zip archive.") from None
    members = [name for name in names if name.lower().endswith(".shp")]
    if len(members) != 1:
        raise IngestionError("The boundary file must contain exactly one shapefile.")
    return members[0]


def read_boundaries(
    zip_path: Path, wanted_codes: Collection[str]
) -> BoundaryReadResult:
    """Return the wanted areas' boundaries as multipolygons in SRID 4326."""
    wanted = set(wanted_codes)
    data_source = DataSource(
        f"/vsizip/{zip_path.resolve().as_posix()}/{shapefile_member(zip_path)}"
    )
    layer = data_source[0]
    if BOUNDARY_CODE_FIELD not in layer.fields or layer.srs is None:
        raise IngestionError(
            f"The boundary file has no {BOUNDARY_CODE_FIELD} field or no coordinate "
            "system."
        )
    # One transform for the layer, as GeoDjango's LayerMapping builds it, so it never
    # depends on each feature geometry carrying its own spatial reference.
    transform = CoordTransform(layer.srs, SpatialReference(WGS_84_SRID))
    features_read = 0
    found: set[str] = set()
    boundaries: dict[str, MultiPolygon] = {}
    unusable_codes: list[str] = []
    for feature in layer:
        features_read += 1
        code = feature.get(BOUNDARY_CODE_FIELD)
        if code not in wanted:
            continue
        found.add(code)
        geometry = feature.geom
        geometry.transform(transform)
        # GEOSGeometry reads a memoryview as WKB; bytes would be read as text.
        converted = GEOSGeometry(geometry.wkb, srid=WGS_84_SRID)
        if isinstance(converted, Polygon):
            boundaries[code] = MultiPolygon(converted, srid=WGS_84_SRID)
        elif isinstance(converted, MultiPolygon):
            boundaries[code] = converted
        else:
            unusable_codes.append(code)
    return BoundaryReadResult(
        features_read=features_read,
        boundaries=boundaries,
        missing_codes=tuple(sorted(wanted - found)),
        unusable_codes=tuple(sorted(unusable_codes)),
    )
