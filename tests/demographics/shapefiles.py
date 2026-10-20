"""Write a minimal polygon shapefile into a zip, standing in for the boundary file.

Only what GDAL needs is written: the .shp geometry, its .shx index, a .dbf holding the
ZCTA5CE20 code, and a NAD83 .prj, as in the Census cartographic boundary file.
"""

import io
import struct
import zipfile

NAD83_PROJECTION = (
    'GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
    'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]]'
)
FILE_CODE = 9994
VERSION = 1000
POLYGON_SHAPE_TYPE = 5
HEADER_LENGTH_WORDS = 50
RECORD_HEADER_LENGTH_BYTES = 8
CODE_FIELD_NAME = "ZCTA5CE20"
CODE_LENGTH = 5


def square(longitude, latitude, longitude_offset, latitude_offset):
    """Return a closed, clockwise ring around a point."""
    west = longitude - longitude_offset
    east = longitude + longitude_offset
    south = latitude - latitude_offset
    north = latitude + latitude_offset
    return [(west, south), (west, north), (east, north), (east, south), (west, south)]


def write_polygon_shapefile_zip(zip_path, stem, features):
    """Write features, each (code, parts) with closed clockwise rings, into a zip."""
    contents = [_record_content(parts) for _, parts in features]
    bounding_box = _bounding_box(
        [point for _, parts in features for part in parts for point in part]
    )
    shp = io.BytesIO()
    shx = io.BytesIO()
    shp_length_words = HEADER_LENGTH_WORDS + sum(
        (RECORD_HEADER_LENGTH_BYTES + len(content)) // 2 for content in contents
    )
    shp.write(_header(shp_length_words, bounding_box))
    shx.write(_header(HEADER_LENGTH_WORDS + 4 * len(contents), bounding_box))
    offset_words = HEADER_LENGTH_WORDS
    for record_number, content in enumerate(contents, start=1):
        content_length_words = len(content) // 2
        shp.write(struct.pack(">ii", record_number, content_length_words))
        shp.write(content)
        shx.write(struct.pack(">ii", offset_words, content_length_words))
        offset_words += (RECORD_HEADER_LENGTH_BYTES + len(content)) // 2
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(f"{stem}.shp", shp.getvalue())
        archive.writestr(f"{stem}.shx", shx.getvalue())
        archive.writestr(f"{stem}.dbf", _dbf([code for code, _ in features]))
        archive.writestr(f"{stem}.prj", NAD83_PROJECTION)


def _bounding_box(points):
    longitudes = [longitude for longitude, _ in points]
    latitudes = [latitude for _, latitude in points]
    return (min(longitudes), min(latitudes), max(longitudes), max(latitudes))


def _header(file_length_words, bounding_box):
    return (
        struct.pack(">7i", FILE_CODE, 0, 0, 0, 0, 0, file_length_words)
        + struct.pack("<2i", VERSION, POLYGON_SHAPE_TYPE)
        + struct.pack("<8d", *bounding_box, 0.0, 0.0, 0.0, 0.0)
    )


def _record_content(parts):
    points = [point for part in parts for point in part]
    part_starts = []
    start = 0
    for part in parts:
        part_starts.append(start)
        start += len(part)
    return (
        struct.pack("<i", POLYGON_SHAPE_TYPE)
        + struct.pack("<4d", *_bounding_box(points))
        + struct.pack("<2i", len(parts), len(points))
        + struct.pack(f"<{len(parts)}i", *part_starts)
        + b"".join(
            struct.pack("<2d", longitude, latitude) for longitude, latitude in points
        )
    )


def _dbf(codes):
    header_length = 32 + 32 + 1
    record_length = 1 + CODE_LENGTH
    header = struct.pack(
        "<B3BIHH20x", 0x03, 126, 1, 1, len(codes), header_length, record_length
    )
    field_descriptor = (
        CODE_FIELD_NAME.encode("ascii").ljust(11, b"\x00")
        + b"C"
        + b"\x00" * 4
        + bytes((CODE_LENGTH, 0))
        + b"\x00" * 14
    )
    records = b"".join(
        b" " + code.encode("ascii").ljust(CODE_LENGTH, b" ") for code in codes
    )
    return header + field_descriptor + b"\x0d" + records + b"\x1a"
