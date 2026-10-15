---
name: django-python
description: "Use whenever writing, editing, reviewing, or planning Python in this project — Django models, migrations, admin, views, forms, management commands, ingestion code, report building, or tests. Django, GeoDjango, and PostGIS guardrails for Axisence."
paths:
  - "**/*.py"
---

# Django and Python guardrails

Everything in `CLAUDE.md` still applies; this adds the traps a model falls into most on this kind of project. Rules already stated in `CLAUDE.md` (timezone-aware datetimes, logging over `print`, no formatted SQL, HTTP timeouts, `Decimal` money, explicit choice values) are not repeated here.

## Geography

- `Point(x, y)` is `Point(longitude, latitude)`. Source data lists latitude first, and swapping the two silently moves Brooklyn to Antarctica. A test asserts that a known Brooklyn address lands near longitude -73.98, latitude 40.68.
- Store locations as `PointField(srid=4326)`. Measure distances on a `geography=True` field or after transforming into a projected reference system (EPSG:2263, New York Long Island state plane, units US feet). Never compute or compare distances in degrees.
- Radius queries filter with `location__dwithin=(point, D(mi=radius))` and order by an annotated `Distance`. Geometry fields carry a spatial index by default; keep it.
- Walking and driving times come from a routing service, never from straight-line distance multiplied by a guessed speed.
- GeoDjango needs the GDAL and GEOS native libraries, the usual setup failure on Windows. The route documented in the project README and `compose.yaml` is the only supported one. Do not point `GDAL_LIBRARY_PATH` at ad hoc libraries or install them another way without asking.

## Models and migrations

- One migration per logical change, named with `makemigrations <app_label> --name <descriptive_name>`.
- Data changes go in their own migration with `RunPython` and a reverse function. Use `RunPython.noop` as the reverse only when undoing is genuinely meaningless, and say why in a comment.
- String fields use the empty string as their empty value, not `null=True`. The exception is a unique string field that may be absent.
- Records from an external source carry the source's identifier under a unique constraint per source, so re-running ingestion updates rows instead of duplicating them.
- Prices store their amount as `DecimalField` and their period (daily, weekly, monthly) as an explicit choice field, never an assumed one.

## Queries

- No N+1 queries: `select_related` for forward foreign keys, `prefetch_related` for reverse and many-to-many relations. Report builders and list views prove their query count with `django_assert_num_queries`.
- Bulk ingestion uses `bulk_create(..., update_conflicts=True, unique_fields=..., update_fields=...)` in batches, wrapped in `transaction.atomic()`. Large reads use `.iterator(chunk_size=...)`.
- Use `.exists()` to test presence and `.count()` to count; never `len(queryset)` for either.

## Fetching and ingestion

- Fetching, storing, and parsing are separate steps. Fetch saves the raw response under `data/raw/<source>/` before anything parses it, so a parsing bug never needs a re-download and every load is reproducible. Parsing is pure functions from raw rows to clean records, unit-tested against fixtures.
- Retry with backoff on HTTP 429 and 5xx responses, and send a descriptive `User-Agent`.
- Socrata APIs (data.ny.gov, data.cityofnewyork.us) page with `$limit` and `$offset` plus an explicit `$order` on a unique column; without `$order`, pages can overlap or skip rows. Send the app token in the `X-App-Token` header when the setting is configured.
- An ingestion run records what it fetched, when, how many rows it created, updated, and marked missing, and from which raw snapshot.

## Django

- `USE_TZ = True` and `TIME_ZONE = "America/New_York"`.
- `DEBUG` defaults to `False`; only the local environment turns it on.
- Admin is internal tooling for researchers: give each model `list_display`, `search_fields`, `list_filter`, and `autocomplete_fields` for foreign keys. It is never exposed without authentication.
- Forms validate input; views never trust request data.
- Scheduled work is a management command, so it runs the same way from a scheduler, a terminal, and a test.

## Tests

- `pytest` with `pytest-django`. Mark tests that need the database with `@pytest.mark.django_db`.
- Spatial behaviour is tested against the Compose PostGIS database, never SQLite.
- Build model data with small factory functions in the tests package rather than large fixture files. Fixture files are for recorded API responses.
- No network access in tests.
