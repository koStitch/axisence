"""Report whether the database and the geospatial libraries GeoDjango needs work."""

from collections.abc import Callable

from django.contrib.gis import gdal, geos
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _query_single_value(sql: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return str(row[0])


def _read_postgresql_version() -> str:
    # server_version reads like "18.0 (Debian 18.0-1.pgdg13+3)".
    return _query_single_value("SHOW server_version").split()[0]


def _read_postgis_version() -> str:
    # Fails when PostGIS is not installed in the configured database.
    return _query_single_value("SELECT postgis_lib_version()")


def _read_gdal_version() -> str:
    return gdal.gdal_version().decode()


def _read_geos_version() -> str:
    # geos_version reads like "3.13.1-CAPI-1.19.2".
    return geos.geos_version().decode().partition("-")[0]


_CHECKS: tuple[tuple[str, Callable[[], str]], ...] = (
    ("PostgreSQL", _read_postgresql_version),
    ("PostGIS", _read_postgis_version),
    ("GDAL", _read_gdal_version),
    ("GEOS", _read_geos_version),
)


class Command(BaseCommand):
    help = (
        "Check the database connection, PostGIS, GDAL, and GEOS, and report their "
        "versions. Exits with a non-zero status when any check fails."
    )

    def handle(self, *args: object, **options: object) -> None:
        database = connection.settings_dict
        self.stdout.write(
            f"Database: {database['NAME']}@{database['HOST']}:{database['PORT']}"
            f" (user {database['USER']})"
        )
        failed_checks: list[str] = []
        for label, read_version in _CHECKS:
            try:
                version = read_version()
            except Exception as error:
                # Any exception is a failed check; the remaining checks still run.
                failed_checks.append(label)
                self.stderr.write(f"{label}: failed: {error}")
            else:
                self.stdout.write(f"{label}: {version}")
        if failed_checks:
            raise CommandError(f"Health check failed: {', '.join(failed_checks)}.")
        self.stdout.write("All checks passed.")
