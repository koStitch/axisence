# Project Architecture

How Axisence runs: what runs on the Windows host and what runs in containers, the Compose services, the package map, settings, the data pipeline, the research tool, commands, and the test and type-checking setup.

## Host and container

GeoDjango loads the GDAL and GEOS native libraries when Django starts. Installing them for native Windows Python is fragile and specific to one machine, so they come from Debian packages in the `web` image instead. That keeps the setup reproducible and matches a later container deployment.

| Runs on | What |
| --- | --- |
| Windows host | `uv`, for the host environment and the dependencies; ruff; git; the editor. |
| `web` container | Django, every `manage.py` command, pytest, and mypy. |

1. Compose bind-mounts the repository root at `/app`, so files the container writes, such as generated migrations, appear in the working tree at once.
2. The container's environment lives in `/opt/venv`, outside the bind mount, and comes first on `PATH`, so `python`, `pytest`, and `mypy` resolve to it. The host `.venv` serves only the editor and ruff; the two environments never share a directory.
3. `Dockerfile` builds the image. It installs the dependencies exactly as `uv.lock` pins them. The build fails when `uv.lock` does not match `pyproject.toml`, or when the base image's Python does not match `.python-version`.
4. The image never contains source code, data, secrets, or the host environment: `.dockerignore` admits only `.python-version`, `pyproject.toml`, and `uv.lock`.
5. File access across the boundary between Windows and the Linux virtual machine in which Docker Desktop runs containers is slower than native access. At the current size this costs seconds; the approach is revisited only if the test suite becomes measurably slow.

## Compose services

| Service | Image | Published port | Role |
| --- | --- | --- | --- |
| `db` | The PostGIS image pinned in `compose.yaml` | `127.0.0.1:5432` | PostgreSQL with PostGIS. Data lives in the named volume `postgres-data`, mounted at `/var/lib/postgresql`. A `pg_isready` health check reports when the server accepts connections. |
| `web` | Built from `Dockerfile` | `127.0.0.1:8000` | Django's development server, with the admin at `http://127.0.0.1:8000/admin/`. Starts after `db` reports healthy. |

1. Compose passes the variables in `.env` to both services. Python never reads `.env` itself.
2. Both ports are published on the loopback interface only, which keeps the database and the development server off the network.
3. The PostGIS image reads `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` on its first start and creates the database and its superuser, so each credential is defined once.
4. Starting the services never applies migrations; `migrate` is always an explicit command.

## Package map

| Package | Holds |
| --- | --- |
| `config` | `settings.py`, the only settings module; `environment.py`, the environment-variable readers; `urls.py`; `wsgi.py`; `asgi.py`. |
| `axisence.accounts` | The custom user model `User`, set as `AUTH_USER_MODEL = "accounts.User"` and registered in the admin with Django's `UserAdmin`. |
| `axisence.diagnostics` | The `healthcheck` management command. It reports the database target and the PostgreSQL, PostGIS, GDAL, and GEOS versions, and exits with a non-zero status when any check fails. No models. |
| `axisence.ingestion` | Pipeline infrastructure shared by every source. Models `IngestionRun` and `IngestionSnapshot`. `http_session.py` builds the HTTP sessions of the pipeline and of the research tool's lookups; `snapshots.py` saves and locates raw snapshots; `socrata.py` fetches a Socrata dataset's metadata and export; `runs.py` starts, completes, fails, and reports runs; `redaction.py` removes secrets from text and log records; `errors.py` holds `IngestionError`. View-only admin pages. |
| `axisence.providers` | Providers and competitors. Models `Provider`, `Competitor`, `ReviewFlag`, and `CompetitorDecision`. `normalization.py` derives display names and matching keys; `parsing.py`, `ny_state.py`, and `nyc_health.py` parse the exports into records; `storing.py` upserts records and tracks activity; `grouping.py` places providers into competitors and refreshes competitor summaries; `geocoding.py` geocodes providers without coordinates; `review.py` holds the researcher decisions; `pipeline.py` runs one provider ingestion. The commands `ingest_ny_state_providers` and `ingest_nyc_health_providers`. Admin pages where researchers review flags, move providers, and merge competitors. |
| `axisence.demographics` | Census data. Models `TabulationArea`, `CensusVariable`, and `CensusFigure`. `census_api.py` requests the Census Data API; `parsing.py` parses its responses; `boundaries.py` reads the boundary shapefile; `loading.py` stores figures and boundaries; `pipeline.py` runs both Census ingestions. The commands `ingest_census_demographics` and `ingest_census_boundaries`. View-only admin pages. |
| `axisence.research` | The research tool. Models `ResearchJob`, `JobCompetitor`, `Contact`, and `Observation`. `geocoding.py` looks target addresses up; `merges.py` resolves merged competitors; `distances.py` measures and formats straight-line distances; `search.py` finds a job's competitors and saves its selection; `labels.py` builds the labels of every listed competitor; `current_values.py` computes the current value of every slot; `job_competitors.py` lists a job's competitors and stores their distances; `jobs.py` holds the job lifecycle; `contacts.py` records and retracts contacts; `forms.py` and `call_sheet.py` hold the forms; `views.py` holds the pages inside the admin; `redaction.py` holds the logging filter for target addresses. Admin pages and their templates. It depends on `providers`, `demographics`, and `ingestion`, and none of them imports it. |
| `tests` | The test suite, mirroring the packages above. |

Every Django app is a subpackage of `axisence`, for example `axisence.providers`, and is listed in `INSTALLED_APPS` in `config/settings.py`. `django.contrib.postgres`, which ships with Django, is installed too, for the array of provider kinds a research job searches. The admin at `/admin/` is the only URL.

## Settings and environment variables

`config/settings.py` reads every environment-specific value through the readers in `config/environment.py`: `read_string`, `read_boolean`, `read_integer`, and `read_list`.

1. Each read is either required or has an explicit default.
2. A variable that is unset or blank counts as unset.
3. A missing required variable, or a value that cannot be parsed, stops startup with `ImproperlyConfigured` naming the variable. The message never repeats the value.

`.env.example` documents every variable and is the template for `.env`.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DJANGO_SECRET_KEY` | yes | none | `SECRET_KEY` |
| `DJANGO_DEBUG` | no | false | `DEBUG` |
| `DJANGO_ALLOWED_HOSTS` | no | empty list | `ALLOWED_HOSTS`, comma-separated |
| `DJANGO_LOG_LEVEL` | no | `INFO` | Console logging level for every logger, Django's own included, except Django's SQL logger, which stays at `INFO` |
| `POSTGRES_HOST` | yes | none | Database host; `db` under Compose |
| `POSTGRES_PORT` | no | `5432` | Database port |
| `POSTGRES_DB` | yes | none | Database name; also read by the PostGIS image |
| `POSTGRES_USER` | yes | none | Database user; also read by the PostGIS image |
| `POSTGRES_PASSWORD` | yes | none | Database password; also read by the PostGIS image |
| `SOCRATA_APP_TOKEN` | no | empty | Socrata app token sent as `X-App-Token` |
| `CENSUS_API_KEY` | no | empty | US Census Data API key; the Census commands require it |

Values that are not environment-specific are fixed in code: the PostGIS database engine, `USE_TZ = True`, `TIME_ZONE = "America/New_York"`, and `RAW_DATA_DIRECTORY`, the `data/raw/` directory of the repository.

## Data pipeline

Four management commands load public data into the database: providers from both regulators, Census demographics, and tabulation-area boundaries.

### Provider runs

`ingest_ny_state_providers` and `ingest_nyc_health_providers` share one flow, `axisence.providers.pipeline.run_provider_ingestion`:

1. Fetch the dataset's metadata, then its CSV export, each saved as a snapshot. With `--snapshot`, take a saved export instead and fetch nothing.
2. Parse the export with pure functions: validate the headers, skip blank, too long, or repeated identifiers, and turn every row into a record, with a field warning for each unusable cell. A value too long for its column, or a count too large for it, is an unusable cell, so no published value can fail a write.
3. In one transaction: upsert the records, mark absent providers missing behind the mass-disappearance guard, raise changed-keys flags, place unplaced providers into competitors, and refresh every competitor summary. A failure anywhere in it rolls all of it back.
4. Geocode the candidates, each batch in a transaction of its own, so a geocoding failure never undoes stored providers.

### Census runs

1. `ingest_census_demographics --vintage <year>` checks for the key before any request, saves each of the tables `DP02` to `DP05` and its labels as snapshots, parses every saved file, and then stores the vintage's variables and figures in one transaction, writing only new and changed figures.
2. `ingest_census_boundaries` checks for the key, saves the NY State tabulation-area list and the boundary zip as snapshots, reads the zip in place through GDAL's `/vsizip/` path, transforms the wanted areas to SRID 4326, and stores them in one transaction.
3. Either command creates the tabulation areas it needs, so they can run in any order.

### Run records and snapshots

1. Every invocation that passes argument parsing creates an `IngestionRun` at its start and completes it at its end, outside every data transaction, so a failed run keeps its record. A count left null was not applicable or not reached.
2. An `IngestionError` fails the run with its message. Any other exception fails it with only the exception's class name, because its message may hold row values, and is raised again.
3. A failed run makes the command raise `CommandError`, so the exit status is non-zero. A succeeded run prints its summary.
4. Each saved or re-ingested file is an `IngestionSnapshot` of its run, with its path relative to `RAW_DATA_DIRECTORY`, its size, and its SHA-256.
5. Only successful responses become snapshots. A response is streamed into `<name>.part`, hashed while writing, and renamed when complete. The pipeline never overwrites, modifies, or deletes a snapshot, and nothing prunes them.

Raw snapshot layout under `data/raw/`, where every file name reads `<UTC timestamp>-run-<run id>-<name>`:

| Directory | Contents |
| --- | --- |
| `ny-ocfs/` | NY State metadata (`child-care-regulated-programs-metadata.json`) and export (`child-care-regulated-programs-export.csv`) |
| `nyc-dohmh/` | NYC Health metadata (`active-child-care-programs-metadata.json`) and export (`active-child-care-programs-export.csv`) |
| `census-geocoder/` | Geocoder results, one per batch (`addressbatch-result-<batch>.csv`) |
| `census-acs-profile/` | Data profile tables and their labels (`acs5-profile-<vintage>-<table>.json`, `acs5-profile-<vintage>-<table>-variables.json`) |
| `census-boundaries/` | The NY State tabulation-area list (`acs5-profile-2024-new-york-tabulation-areas.json`) and the boundary zip (`cb_2020_us_zcta520_500k.zip`) |

### HTTP and secrets

1. Every outbound request goes through a session that `axisence.ingestion.http_session` builds:
    1. `build_session()`, for the pipeline: a timeout of 10 seconds to connect and 120 to read, up to 5 retries with exponential backoff on HTTP 429, 500, 502, 503, and 504 for GET and POST, `Retry-After` honoured, and the User-Agent `Axisence-Data-Pipeline (public data ingestion)`.
    2. `build_lookup_session()`, for the research tool's target address lookups: a timeout of 5 seconds to connect and 10 to read, no retry, because a researcher waits on the page, and the User-Agent `Axisence-Research-Tool (target address lookup)`.
2. The Census key travels as the `key` query parameter, so request URLs carry it. `redact_secrets` removes every `key` parameter and both configured secrets from error messages before they are logged, stored, or shown, and a logging filter on the console handler does the same for every log record, urllib3's request lines included.
3. Logs and run records identify a provider by source and identifier only, never by name, address, or phone number.

## Research tool

Researchers run research jobs and record contacts in pages inside the admin, behind its login.

### Pages

| Page | URL | Holds |
| --- | --- | --- |
| Research job | `/admin/research/researchjob/<id>/change/` | The job; the target point on GeoDjango's OpenStreetMap map widget, with its confirmation and its tabulation area; the job's competitors with their research statuses; the status buttons. |
| Competitor search | `/admin/research/researchjob/<id>/search/` | The candidate competitors with their straight-line distances and labels, the selection, and adding a competitor by name. |
| Call sheet | `/admin/research/contact/call-sheet/<competitor id>/` | The competitor's published data, labels, current values, and previous contacts, and the entry form of a new contact. |
| Retraction | `/admin/research/contact/<id>/retract/` | The confirmation and the reason of a retraction, of a whole contact or, with `?observation=<id>`, of one observation. |

1. The views live in `axisence/research/views.py` and are wired through the model admins' `get_urls`, so the admin login guards them. Each view checks its own permissions.
2. The competitor page's "Call sheet" link comes from `admin/providers/competitor/change_form.html`, a template the research app ships for the providers admin, so `providers` never imports `research`.
3. The add-by-name field asks a job-specific autocomplete view, `/admin/research/researchjob/<id>/competitor-autocomplete/`, which serves the admin's autocomplete without the job's own competitor. No competitor autocomplete ever offers a retired competitor.
4. Status changes are submit buttons on the job page, applied after the inline research statuses are saved, so "Mark complete" sees the statuses saved with it.
5. The map loads OpenLayers from `cdn.jsdelivr.net` and its tiles from OpenStreetMap in the researcher's browser, so it needs an internet connection.

### Researchers group

The data migration `research.0002_create_researchers_group` creates the group "Researchers". It creates the permissions it grants first, because Django creates permissions only after all migrations.

| App | Permissions |
| --- | --- |
| `providers` | View and change providers, competitors, and review flags; view competitor decisions. |
| `demographics` | View tabulation areas, Census variables, and Census figures. |
| `research` | Add, view, and change research jobs and job competitors; add and view contacts and observations; `retract_contact`, the permission to retract contacts and observations. |

A researcher therefore cannot manage accounts or change ingested data other than through the competitor review actions.

### Append-only records

Contacts and observations are never changed or deleted, and research jobs are never deleted. Three layers enforce it:

1. The admin refuses to change or delete contacts and observations, and to delete research jobs, for every user, the superuser included. Contacts are added only through the call sheet.
2. `Contact.save` and `Observation.save` refuse any update other than of the three retraction columns. Their instance `delete` and their querysets' `update` and `delete`, and with it `bulk_update`, raise `PermanentRecordError`, and research jobs refuse deletion the same way.
3. Every foreign key to these rows is `PROTECT`, and check constraints keep each observation kind to its own columns, every retraction complete, and research on a confirmed target point.

### Merged competitors

Research rows keep the competitor they were recorded for and are never rewritten when competitors merge. `merges.MergeChains` loads where every retired competitor's merges lead in one query, and every display resolves rows to the current competitor at read time: a job lists a competitor once, current values include the observations of merged competitors, contacts are recorded only for current competitors, and a retired competitor's call sheet redirects to the competitor that received it.

### Logging

1. Every module that logs uses `logging.getLogger(__name__)` and identifies jobs, contacts, observations, and competitors by number only, never by address, notes, or value.
2. urllib3 logs request URLs, and the single-address geocoder's URL holds the target address in its `address` query parameter. `axisence.research.redaction.RedactTargetAddressesFilter`, on the console handler next to the secrets filter, redacts that parameter from every record. A failed lookup logs only the class name of its error, because a requests error message holds the URL.
3. In debug mode Django's SQL logger, `django.db.backends`, writes every statement with its parameters at `DEBUG`, and those parameters hold target addresses and call notes. The logger is pinned to `INFO` in `LOGGING`, so `DJANGO_LOG_LEVEL=DEBUG` never prints SQL.

## Commands

Each command has exactly one form, used the same way in `CLAUDE.md`, `.claude/settings.json`, the migration hook, and the README.

| Purpose | Command | Runs on |
| --- | --- | --- |
| Start the services | `docker compose up -d` | host |
| Build the web image | `docker compose build` | host |
| Django system check | `docker compose run --rm web python manage.py check` | container |
| Create a migration | `docker compose run --rm web python manage.py makemigrations <app_label> --name <descriptive_name>` | container |
| Apply migrations | `docker compose run --rm web python manage.py migrate` | container |
| List migrations | `docker compose run --rm web python manage.py showmigrations` | container |
| Health check | `docker compose run --rm web python manage.py healthcheck` | container |
| Load NY State providers | `docker compose run --rm web python manage.py ingest_ny_state_providers` | container |
| Load NYC Health providers | `docker compose run --rm web python manage.py ingest_nyc_health_providers` | container |
| Load Census demographics | `docker compose run --rm web python manage.py ingest_census_demographics --vintage 2024` | container |
| Load tabulation-area boundaries | `docker compose run --rm web python manage.py ingest_census_boundaries` | container |
| Tests | `docker compose run --rm web pytest` | container |
| Type check | `docker compose run --rm web mypy .` | container |
| Lint | `uv run ruff check` | host |
| Format | `uv run ruff format` | host |
| Sync the host environment | `uv sync` | host |

`docker compose run --rm` works whether or not `web` is running, starts `db` when needed, and costs a container start of a few seconds per command. The provider commands also take `--snapshot PATH` and `--allow-mass-disappearance`; the README describes when to use them.

## Tests and type checking

1. pytest with pytest-django is configured in `pyproject.toml`, with `DJANGO_SETTINGS_MODULE = "config.settings"` and tests under `tests/`. The suite runs only in the `web` container, against the Compose PostGIS server, where Django creates and destroys its test database. Tests never use SQLite and never touch the network.
2. Tests that need the database are marked `@pytest.mark.django_db`. A test that needs a model no production app defines declares a throwaway model in an isolated app registry (`django.test.utils.isolate_apps`) and creates its table with the schema editor inside the test's transaction, as `tests/test_postgis.py` does.
3. mypy runs in strict mode with the django-stubs plugin, configured in `pyproject.toml`. The plugin imports the settings and models, and with them GDAL, so mypy runs only in the container. Test code may leave function definitions unannotated; their bodies are still checked. Migrations and `scratch/` are excluded.
4. `tests/fixtures/` holds the recorded inputs: provider export rows cut from real snapshots, with every phone number, licensee name, and home name, address, and coordinate replaced by synthetic values; trimmed Census Data API responses; a recorded batch geocoder response; and a recorded single-address geocoder response for Brooklyn Borough Hall, a public building. They hold public or synthetic data only.
5. Autouse fixtures in `tests/conftest.py` keep every test off the network and off the real data: `http_session.build_session` and `http_session.build_lookup_session` are wrapped to mount a fake transport, `tests/ingestion/fake_transport.py`, which answers only the routes a test registers and records each request and its timeout; `RAW_DATA_DIRECTORY` points at a temporary directory; and both secrets are blank unless a test sets synthetic values.
6. `tests/demographics/shapefiles.py` writes a minimal polygon shapefile with a NAD83 projection into a zip, so the boundary tests run GDAL on synthetic areas instead of the 67 MB national file.
7. `tests/admin_forms.py` builds the data an admin change page posts, its main form and inline formsets included, and `tests/research/call_sheet_posts.py` does the same for the call sheet.
8. django-stubs declares `ModelAdmin`, `TabularInline`, and `ModelForm` generic, but those classes cannot be subscripted at runtime. Each `admin.py` therefore defines its parameterized base classes under `if TYPE_CHECKING:` and the plain classes otherwise, which keeps strict typing without a runtime dependency or `type: ignore` comments.
9. ruff runs on the host and is configured in `pyproject.toml`. Its rule set enforces the `CLAUDE.md` rules that have a linter equivalent, among them timezone-aware datetimes, no `print`, annotated signatures outside tests, no SQL built by string formatting, explicit HTTP timeouts, and no hard-coded secrets. Migrations are excluded from linting and formatting.
