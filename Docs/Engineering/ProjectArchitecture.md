# Project Architecture

How Axisence runs: what runs on the Windows host and what runs in containers, the Compose services, the live copy, the package map, settings, the data pipeline, the research tool, the report and teaser generator, subscriptions and repeat jobs, commands, and the test and type-checking setup.

## Host and container

GeoDjango loads the GDAL and GEOS native libraries when Django starts. Installing them for native Windows Python is fragile and specific to one machine, so they come from Debian packages in the `web` image instead. That keeps the setup reproducible, and the live copy runs the same image.

| Runs on | What |
| --- | --- |
| Windows host | `uv`, for the host environment and the dependencies; ruff; git; the editor; the live copy's PowerShell scripts under `scripts/`. |
| `web` container | Django, every `manage.py` command, pytest, and mypy. |

1. Compose bind-mounts the repository root at `/app`, so files the container writes, such as generated migrations, appear in the working tree at once.
2. The container's environment lives in `/opt/venv`, outside the bind mount, and comes first on `PATH`, so `python`, `pytest`, and `mypy` resolve to it. The host `.venv` serves only the editor and ruff; the two environments never share a directory.
3. `Dockerfile` builds the image. It installs the dependencies exactly as `uv.lock` pins them. The build fails when `uv.lock` does not match `pyproject.toml`, or when the base image's Python does not match `.python-version`.
4. The image also holds what reports need, for the same reason: Pango and HarfBuzz's subsetting library, which WeasyPrint loads to lay out text and embed font subsets; the Inter font, in which reports are set; and poppler-utils, whose `pdftoppm`, `pdffonts`, and `pdfinfo` turn report pages into images and list their fonts and metadata during layout inspection.
5. The image never contains source code, data, secrets, or the host environment: `.dockerignore` admits only `.python-version`, `pyproject.toml`, and `uv.lock`. Both copies, development and live, bind-mount their own checkout at `/app`, so each runs its own code on the same image definition.
6. File access across the boundary between Windows and the Linux virtual machine in which Docker Desktop runs containers is slower than native access. At the current size this costs seconds; the approach is revisited only if the test suite becomes measurably slow.

## Compose services

Two Compose files define two independent stacks, each run from its own checkout. The development stack of `compose.yaml`, Compose project `axisence`, named after its folder:

| Service | Image | Published port | Role |
| --- | --- | --- | --- |
| `db` | The PostGIS image pinned in `compose.yaml` | `127.0.0.1:5432` | PostgreSQL with PostGIS. Data lives in the named volume `postgres-data`, mounted at `/var/lib/postgresql`. A `pg_isready` health check reports when the server accepts connections. |
| `web` | Built from `Dockerfile` | `127.0.0.1:8000` | Django's development server, with the admin at `http://127.0.0.1:8000/admin/`. Starts after `db` reports healthy. |

1. Compose passes the variables in `.env` to both services. Python never reads `.env` itself.
2. Both ports are published on the loopback interface only, which keeps the database and the development server off the network.
3. The PostGIS image reads `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` on its first start and creates the database and its superuser, so each credential is defined once.

### Live copy

The live copy is the shared database that the user and the teammate work in. It runs the live stack of `compose.live.yaml`, Compose project `axisence-live`, from a second checkout at `C:\Work\axisence-live` on the user's PC, and is published only through a Cloudflare Tunnel behind Cloudflare Access. The README's "Live copy" section holds its setup and operation.

| Service | Image | Published port | Role |
| --- | --- | --- | --- |
| `db` | The same PostGIS image as `compose.yaml` | none | PostgreSQL with PostGIS, as in development, with its own volume: `postgres-data` of the live project. |
| `web` | Built from `Dockerfile` | none | Runs `collectstatic`, then gunicorn with three synchronous workers and a 300-second timeout. Starts after `db` reports healthy. |
| `tunnel` | `cloudflare/cloudflared`, pinned in `compose.live.yaml` | none | Connects outward to Cloudflare and forwards the requests for the live hostname to `http://web:8000`. The route and its HTTP Host Header, the live hostname, are configured in the Cloudflare dashboard. |

1. The live `.env` sets `COMPOSE_FILE=compose.live.yaml`, and the top-level `name:` fixes the project name, so the live folder's commands are the everyday ones and its volume never mixes with the development one.
2. No service publishes a port. Cloudflare Access admits only the listed email addresses before a request reaches the tunnel.
3. Every live service restarts unless stopped, so the stack returns when Docker Desktop starts.
4. The 300-second timeout exceeds Cloudflare's 125-second limit, so a slow generation completes even when the browser shows Cloudflare's timeout page.
5. `CLOUDFLARE_TUNNEL_TOKEN` reaches the `tunnel` service through interpolation; `env_file` passes the whole `.env` to `db` and `web`, as in development.
6. The `db` image is identical in both files.
7. Starting the services never applies migrations in either copy; `migrate` is always an explicit command.

## Package map

| Package | Holds |
| --- | --- |
| `config` | `settings.py`, the only settings module; `environment.py`, the environment-variable readers; `urls.py`; `wsgi.py`; `asgi.py`. |
| `axisence.accounts` | The custom user model `User`, set as `AUTH_USER_MODEL = "accounts.User"` and registered in the admin with Django's `UserAdmin`. |
| `axisence.diagnostics` | The `healthcheck` management command. It reports the database target and the PostgreSQL, PostGIS, GDAL, and GEOS versions, and exits with a non-zero status when any check fails. No models. |
| `axisence.ingestion` | Pipeline infrastructure shared by every source. Models `IngestionRun` and `IngestionSnapshot`. `http_session.py` builds the HTTP sessions of the pipeline and of the research tool's lookups; `snapshots.py` saves and locates raw snapshots; `socrata.py` fetches a Socrata dataset's metadata and export; `runs.py` starts, completes, fails, and reports runs; `redaction.py` removes secrets from text and log records; `errors.py` holds `IngestionError`; `coordinates.py` holds the NY State bounding box every source's locations are checked against; `view_only_admin.py` holds the mixin of every view-only admin page. View-only admin pages. |
| `axisence.providers` | Providers and competitors. Models `Provider`, `Competitor`, `ReviewFlag`, and `CompetitorDecision`. `normalization.py` derives display names and matching keys; `parsing.py`, `ny_state.py`, and `nyc_health.py` parse the exports into records; `storing.py` upserts records and tracks activity; `grouping.py` places providers into competitors and refreshes competitor summaries; `geocoding.py` geocodes providers without coordinates; `review.py` holds the researcher decisions; `pipeline.py` runs one provider ingestion. The commands `ingest_ny_state_providers` and `ingest_nyc_health_providers`. Admin pages where researchers review flags, move providers, and merge competitors. |
| `axisence.demographics` | Census data. Models `TabulationArea`, `CensusVariable`, and `CensusFigure`. `census_api.py` requests the Census Data API; `parsing.py` parses its responses; `boundaries.py` reads the boundary shapefile; `loading.py` stores figures and boundaries; `pipeline.py` runs both Census ingestions. The commands `ingest_census_demographics` and `ingest_census_boundaries`. View-only admin pages. |
| `axisence.transit` | Transit stops. Models `TransitLine` and `TransitStop`. `feeds.py` names the seven MTA feeds and takes each one for a run; `parsing.py` reads one feed's stops and their weekday daytime lines; `combining.py` merges the feeds into stops to store and groups stations; `storing.py` upserts stops and lines and marks absent stops inactive; `pipeline.py` runs one transit ingestion; `nearby.py` finds the station groups and bus lines near points. The command `ingest_mta_transit`. A view-only admin page of transit stops. It depends on `ingestion` only, and only `reports` imports it. |
| `axisence.research` | The research tool. Models `ResearchJob`, `JobCompetitor`, `Contact`, and `Observation`; `ResearchJob.predecessor` records the job a repeat job repeats. `geocoding.py` looks target addresses up; `merges.py` resolves merged competitors; `distances.py` measures and formats straight-line distances; `search.py` finds a job's competitors and saves its selection; `labels.py` builds the labels of every listed competitor and sums licensed capacities; `current_values.py` computes the current value of every slot; both also apply a report's cut-off; `stated_values.py` defines what a value states and the order of value slots; `value_history.py` builds the call sheet's history; `job_competitors.py` lists a job's competitors and stores their distances; `jobs.py` holds the job lifecycle; `signals.py` holds `job_status_changing`, through which other apps add status rules; `contacts.py` records and retracts contacts; `forms.py` and `call_sheet.py` hold the forms; `views.py` holds the pages inside the admin; `redaction.py` holds the logging filter for target addresses. Admin pages and their templates. It depends on `providers`, `demographics`, and `ingestion`, and none of them imports it. |
| `axisence.reports` | The report and teaser generator. Models `Report` and `ListedCompetitor`. `figure_lists.py` holds the Census figures a report shows, and the subset a teaser shows, per vintage; `demographics.py` checks the list and builds the demographics section; `prices.py` computes monthly equivalents and the tuition comparison; `schematic_map.py` draws the map; `competitors.py` loads the competitors and builds their texts and profiles; `provider_runs.py` loads the provider runs a document reads and words their as-of dates; `transit_access.py` loads the transit run a client report reads and builds its transit access section and profile texts; `listed_competitors.py` records and reads the competitors a report lists, the compared report, and a repeat job's carried-over competitors; `changes.py` builds a repeat report's changes section; `content.py` builds everything a report shows; `teaser_content.py` builds everything a teaser shows; `rendering.py` renders the HTML and the PDF, and checks a teaser's single page; `storage.py` writes report and teaser files; `generation.py` checks the rules, generates, and records a report or a teaser; `views.py` holds the download; `errors.py` holds the errors. The commands `generate_report` and `generate_teaser`. Admin pages, the research job page's report and teaser additions, and a repeat job's first selection. It depends on `research`, `providers`, `demographics`, `transit`, and `ingestion`, and none of them imports it. |
| `axisence.subscriptions` | Subscriptions and repeat jobs. Models `Subscription` and `ServingJob`. `schedule.py` computes due dates, due states, and progress; `lines.py` follows lines of repeat jobs; `lifecycle.py` validates and saves subscriptions; `repeats.py` prepares repeat jobs and refuses reopening served due dates; `forms.py`, `admin.py`, and `views.py` hold the pages and the research job page's repeat additions. It depends on `reports` and `research`, and no other app imports it. |
| `tests` | The test suite, mirroring the packages above. |

Every Django app is a subpackage of `axisence`, for example `axisence.providers`, and is listed in `INSTALLED_APPS` in `config/settings.py`. `django.contrib.postgres`, which ships with Django, is installed too, for the array of provider kinds a research job searches. The admin at `/admin/` is the only URL Django routes. WhiteNoise's middleware, directly after the security middleware, serves `/static/` in front of it, from `STATIC_ROOT` when `DEBUG` is off.

`scripts/` holds the user's PowerShell scripts for the live copy. They are invoked with `powershell -NoProfile -ExecutionPolicy Bypass -File`, run under Windows PowerShell 5.1, and contain ASCII characters only, because Windows PowerShell 5.1 reads a file without a byte order mark in the ANSI code page.

1. `common.ps1`: the helpers the other three dot-source: running a native command and failing on a non-zero exit code; copying new and changed files with robocopy, leaving out `.part` files and never deleting any; and recognizing the live folder by the line `COMPOSE_FILE=compose.live.yaml` in its `.env`.
2. `back-up-data.ps1`: backs one copy up to a folder: a custom-format `pg_dump` of its database, then every new report file and raw download. The dump leaves out the four extensions that the PostGIS image installs into every new database, and their schemas `tiger`, `tiger_data`, and `topology`, so that it restores into a fresh database of the image. It keeps the newest 30 dumps, never prunes files, and appends its outcome to `backup-log.txt`. `-Copy Development` backs up the development copy, from its own folder.
3. `restore-data.ps1`: restores such a backup into a fresh live copy only, in one transaction, then copies the files. It refuses a database that has Django's migration table, and a `data/private/reports/` or `data/raw/` that holds files.
4. `release-live-copy.ps1`: checks that the live checkout is clean and on `main`, backs up, fast-forwards from GitHub, builds, migrates, and recreates `web`, because gunicorn loads the code only once.

A backup moves `data/raw/` with the database, because snapshot records hold paths relative to `RAW_DATA_DIRECTORY` and a transit run sends a feed's ETag only while its latest snapshot is on disk. Report numbers come from the primary-key sequence of the report table, which the dump carries, so a restored copy continues the numbering.

## Settings and environment variables

`config/settings.py` reads every environment-specific value through the readers in `config/environment.py`: `read_string`, `read_boolean`, `read_integer`, `read_list`, and `read_https_url`, which also requires the value to start with `https://`.

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
| `DJANGO_BEHIND_HTTPS_PROXY` | no | false | Django's HTTPS settings for running behind the tunnel; see below |
| `POSTGRES_HOST` | yes | none | Database host; `db` under Compose |
| `POSTGRES_PORT` | no | `5432` | Database port |
| `POSTGRES_DB` | yes | none | Database name; also read by the PostGIS image |
| `POSTGRES_USER` | yes | none | Database user; also read by the PostGIS image |
| `POSTGRES_PASSWORD` | yes | none | Database password; also read by the PostGIS image |
| `SOCRATA_APP_TOKEN` | no | empty | Socrata app token sent as `X-App-Token` |
| `CENSUS_API_KEY` | no | empty | US Census Data API key; the Census commands require it |
| `AXISENCE_WEBSITE_URL` | no | empty | Axisence's website in the teaser's call to action; must start with `https://` |
| `AXISENCE_CONTACT_EMAIL` | no | empty | Axisence's email address in the teaser's call to action |
| `AXISENCE_CONTACT_PHONE` | no | empty | Axisence's phone number in the teaser's call to action, printed as written |
| `CLOUDFLARE_TUNNEL_TOKEN` | live copy only | empty | The tunnel's token; read by `compose.live.yaml`, never by Django |
| `COMPOSE_FILE` | live copy only | unset | `compose.live.yaml` in the live copy; read by Compose, never by Django |

Generating a teaser refuses while any of the three `AXISENCE_` variables is blank; the refusal names the blank variables.

`DJANGO_BEHIND_HTTPS_PROXY=true` turns on six settings together: `SECURE_PROXY_SSL_HEADER`, which trusts `X-Forwarded-Proto: https`; `SECURE_SSL_REDIRECT`; secure session and CSRF cookies; and HSTS for one year, subdomains included. One variable switches all of them, because values that disagree are a silent weakness or a redirect loop. The header is trustworthy only in the live copy: Cloudflare sets `X-Forwarded-Proto` to the protocol the visitor used, overwriting any value the client sent, and nothing but the tunnel reaches gunicorn. `SECURE_HSTS_PRELOAD` stays unset, so `check --deploy` reports `security.W021`; preloading would commit the domain to the browsers' built-in lists. The development copy leaves the variable unset.

Values that are not environment-specific are fixed in code: the PostGIS database engine, `USE_TZ = True`, `TIME_ZONE = "America/New_York"`, `RAW_DATA_DIRECTORY`, the `data/raw/` directory of the repository, `REPORT_DIRECTORY`, the git-ignored `data/private/reports/` directory that holds generated reports and teasers, and `STATIC_ROOT`, the git-ignored `staticfiles/` directory that `collectstatic` fills and WhiteNoise serves.

## Data pipeline

Five management commands load public data into the database: providers from both regulators, Census demographics, tabulation-area boundaries, and MTA transit stops.

### Provider runs

`ingest_ny_state_providers` and `ingest_nyc_health_providers` share one flow, `axisence.providers.pipeline.run_provider_ingestion`:

1. Fetch the dataset's metadata, then its CSV export, each saved as a snapshot. With `--snapshot`, take a saved export instead and fetch nothing. The metadata saved beside that export, which a fetch names with the same time and run, is re-ingested with it, so the run records the publisher's last update time of the data it loads, never the time of the re-ingestion. A run whose export has no saved metadata beside it records no publisher time and logs a warning.
2. Parse the export with pure functions: validate the headers, skip blank, too long, or repeated identifiers, and turn every row into a record, with a field warning for each unusable cell. A value too long for its column, or a count too large for it, is an unusable cell, so no published value can fail a write.
3. In one transaction: upsert the records, mark absent providers missing behind the mass-disappearance guard, raise changed-keys flags, place unplaced providers into competitors, and refresh every competitor summary. A failure anywhere in it rolls all of it back.
4. Geocode the candidates, each batch in a transaction of its own, so a geocoding failure never undoes stored providers.

### Census runs

1. `ingest_census_demographics --vintage <year>` checks for the key before any request, saves each of the tables `DP02` to `DP05` and its labels as snapshots, parses every saved file, and then stores the vintage's variables and figures in one transaction, writing only new and changed figures.
2. `ingest_census_boundaries` checks for the key, saves the NY State tabulation-area list and the boundary zip as snapshots, reads the zip in place through GDAL's `/vsizip/` path, transforms the wanted areas to SRID 4326, and stores them in one transaction.
3. Either command creates the tabulation areas it needs, so they can run in any order.

### Transit runs

`ingest_mta_transit` runs `axisence.transit.pipeline.run_transit_ingestion`:

1. Take all seven feeds before parsing any. Each feed is requested with the ETag of its latest saved snapshot, from a run of any outcome, when that file is still on disk. HTTP 200 saves a new snapshot with the response's `ETag` and `Last-Modified`; HTTP 304 records the latest snapshot again; any other answer or a failed request fails the run before any stop is written. With `--from-latest-snapshots`, nothing is requested and every feed's latest snapshot is read, which fails naming a feed that has none on disk.
2. Parse each zip in place with the standard library, streaming its members: check the required members and columns first, then read stops, routes, calendar, trips, stop times, and transfers. A service counts while it runs on or after the local date the run started.
3. Combine the feeds: one stop per mode and published ID, bus stops shared between feeds taking the first feed's name and position, and same-name stations joined by transfers grouped under one key.
4. In one transaction: upsert lines and stops, set each stop's lines, and mark absent stops inactive behind a mass-disappearance guard per mode, which refuses before any write to mark more than 10% of a mode's active stops inactive unless `--allow-mass-disappearance` is given. A stop whose rows were all skipped for their coordinates stays as stored.
5. Print the shared run summary, where "Marked missing" counts stops marked inactive, then each feed's outcome: `downloaded`, `unchanged`, or `read from the latest snapshot`.

### Run records and snapshots

1. Every invocation that passes argument parsing creates an `IngestionRun` at its start and completes it at its end, outside every data transaction, so a failed run keeps its record. A count left null was not applicable or not reached.
2. An `IngestionError` fails the run with its message. Any other exception fails it with only the exception's class name, because its message may hold row values, and is raised again.
3. A failed run makes the command raise `CommandError`, so the exit status is non-zero. A succeeded run prints its summary.
4. Each saved or re-ingested file is an `IngestionSnapshot` of its run, with its path relative to `RAW_DATA_DIRECTORY`, its size, and its SHA-256. A snapshot a transit run saved also records the response's `ETag` and `Last-Modified` headers as sent, which the next run's conditional request uses; a file recorded again has a blank fetch time and blank headers.
5. Only successful responses become snapshots. A response is streamed into `<name>.part`, hashed while writing, and renamed when complete. An unchanged transit feed, answered with HTTP 304, saves nothing: its latest snapshot is recorded again. The pipeline never overwrites, modifies, or deletes a snapshot, and nothing prunes them.

Raw snapshot layout under `data/raw/`, where every file name reads `<UTC timestamp>-run-<run id>-<name>`:

| Directory | Contents |
| --- | --- |
| `ny-ocfs/` | NY State metadata (`child-care-regulated-programs-metadata.json`) and export (`child-care-regulated-programs-export.csv`) |
| `nyc-dohmh/` | NYC Health metadata (`active-child-care-programs-metadata.json`) and export (`active-child-care-programs-export.csv`) |
| `census-geocoder/` | Geocoder results, one per batch (`addressbatch-result-<batch>.csv`) |
| `census-acs-profile/` | Data profile tables and their labels (`acs5-profile-<vintage>-<table>.json`, `acs5-profile-<vintage>-<table>-variables.json`) |
| `census-boundaries/` | The NY State tabulation-area list (`acs5-profile-2024-new-york-tabulation-areas.json`) and the boundary zip (`cb_2020_us_zcta520_500k.zip`) |
| `mta-gtfs/` | The MTA static GTFS feeds (`gtfs_subway.zip`, `gtfs_b.zip`, `gtfs_bx.zip`, `gtfs_m.zip`, `gtfs_q.zip`, `gtfs_si.zip`, `gtfs_busco.zip`), each saved only when the MTA has republished it |

### HTTP and secrets

1. Every outbound request goes through a session that `axisence.ingestion.http_session` builds:
    1. `build_session()`, for the pipeline: a timeout of 10 seconds to connect and 120 to read, up to 5 retries with exponential backoff on HTTP 429, 500, 502, 503, and 504 for GET and POST, `Retry-After` honoured, and the User-Agent `Axisence-Data-Pipeline (public data ingestion)`.
    2. `build_lookup_session()`, for the research tool's target address lookups: a timeout of 5 seconds to connect and 10 to read, no retry, because a researcher waits on the page, and the User-Agent `Axisence-Research-Tool (target address lookup)`.
2. The Census key travels as the `key` query parameter, so request URLs carry it. `redact_secrets` removes every `key` parameter and both configured secrets from error messages before they are logged, stored, or shown, and a logging filter on the console handler does the same for every log record, urllib3's request lines included.
3. Logs and run records identify a provider by source and identifier only, never by name, address, or phone number. Transit logs name only feeds, HTTP outcomes, and published stop IDs, and nothing logs the transit near a report's target location.

## Research tool

Researchers run research jobs and record contacts in pages inside the admin, behind its login.

### Pages

| Page | URL | Holds |
| --- | --- | --- |
| Research job | `/admin/research/researchjob/<id>/change/` | The job; the target point on GeoDjango's OpenStreetMap map widget, with its confirmation and its tabulation area; the job's competitors with their research statuses; the status buttons; the "Generate report" and "Generate teaser" buttons and the Reports section, which the reports app adds; the "Repeats" module and the "Prepare repeat job" button, which the subscriptions app adds. |
| Competitor search | `/admin/research/researchjob/<id>/search/` | The candidate competitors with their straight-line distances and labels, the selection, a repeat job's first selection, and adding a competitor by name. |
| Call sheet | `/admin/research/contact/call-sheet/<competitor id>/` | The competitor's published data, labels, current values, the history of its values, and previous contacts, and the entry form of a new contact. |
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
| `reports` | View reports; `generate_report`, the permission to generate reports; `generate_teaser`, the permission to generate teasers. |
| `subscriptions` | View subscriptions; `prepare_repeat_job`, the permission to prepare repeat jobs. |
| `transit` | View transit stops. |

The data migration `reports.0002_grant_report_permissions_to_researchers` adds the `reports` permissions to the group, creating them first in the same way. `reports.0004_grant_teaser_permission_to_researchers` adds `generate_teaser` the same way. `subscriptions.0002_grant_subscription_permissions_to_researchers` adds the `subscriptions` permissions the same way. `reports.0008_grant_transit_stop_permission_to_researchers` adds the `transit` permission the same way; it lives in the reports app, which already depends on both `research` and `transit`, so neither of those depends on the other.

A researcher therefore cannot manage accounts or change ingested data other than through the competitor review actions.

### Append-only records

Contacts and observations are never changed or deleted, and research jobs are never deleted. Three layers enforce it:

1. The admin refuses to change or delete contacts and observations, and to delete research jobs, for every user, the superuser included. Contacts are added only through the call sheet.
2. `Contact.save` and `Observation.save` refuse any update other than of the three retraction columns. Their instance `delete` and their querysets' `update` and `delete`, and with it `bulk_update`, raise `PermanentRecordError`, and research jobs refuse deletion the same way.
3. Every foreign key to these rows is `PROTECT`, and check constraints keep each observation kind to its own columns, every retraction complete, and research on a confirmed target point.

A repeat job's predecessor is written once, by the preparing service, when the job is created; it is not a form field, and no service changes it afterwards.

### Merged competitors

Research rows keep the competitor they were recorded for and are never rewritten when competitors merge. `merges.MergeChains` loads where every retired competitor's merges lead in one query, and every display resolves rows to the current competitor at read time: a job lists a competitor once, current values include the observations of merged competitors, contacts are recorded only for current competitors, and a retired competitor's call sheet redirects to the competitor that received it.

### Logging

1. Every module that logs uses `logging.getLogger(__name__)` and identifies jobs, contacts, observations, and competitors by number only, never by address, notes, or value.
2. urllib3 logs request URLs, and the single-address geocoder's URL holds the target address in its `address` query parameter. `axisence.research.redaction.RedactTargetAddressesFilter`, on the console handler next to the secrets filter, redacts that parameter from every record. A failed lookup logs only the class name of its error, because a requests error message holds the URL.
3. In debug mode Django's SQL logger, `django.db.backends`, writes every statement with its parameters at `DEBUG`, and those parameters hold target addresses and call notes. The logger is pinned to `INFO` in `LOGGING`, so `DJANGO_LOG_LEVEL=DEBUG` never prints SQL.

## Reports

`axisence.reports` turns a client report job into its competitor analysis as a PDF, and a prospect job into a teaser, a one-page market preview. `Docs/Domain/DataSources.md` describes what a report shows and the rules behind it.

### Generation

`generation.generate_report` and `generation.generate_teaser` run inside the request of the job page or inside the `generate_report` or `generate_teaser` command; there is no queue or worker.

1. It refuses, with a `ReportRefusedError` whose message is shown to the user, a job whose purpose is not Client report, a Draft or Cancelled job, a job that lists no competitors, a Census figure list that no longer matches the stored labels, and, last, the absence of a succeeded transit run finished by the cut-off. Researching gives a draft and Complete a final report; the kind is stored and never changes, and reports of a reopened job stay.
2. The generation time is the report's cut-off. Research values are the current values as of the cut-off; competitors, merges, providers, operating statuses, and Census figures are read as they are. A repeat report's changes section also computes research values as of its compared report's cut-off, from the same observations.
3. `content.build_report` builds everything the report shows as frozen dataclasses of display-ready text, in a fixed number of queries. The templates receive only these and the report's number, never a model instance, so no personal field can reach a template. Transit access adds three queries whatever the number of competitors and stops: the transit run, the nearby stops with one PostGIS distance per point behind a spatial prefilter, and their lines, proven by `tests/reports/test_content.py`.
4. The number is reserved from the primary-key sequence before rendering, because every page prints it. A failure after the reservation leaves its number unused, so numbers always increase but can skip.
5. The PDF is written as `<prefix>-<number>.pdf` under `REPORT_DIRECTORY`: `report-` for reports, `market-preview-` for teasers. It goes through a `.part` file renamed when complete, and an existing file is never overwritten.
6. The `Report` row records the number, the job, the kind, the generation time, the generating user (none for the command), the compared report of a repeat job's report, the Census vintage, the tabulation-area code, the latest succeeded run of each provider source finished by the cut-off, the latest succeeded transit run finished by the cut-off (none for a teaser), and the file's name, size, and SHA-256. A draft or final report also records its listed competitors, as `ListedCompetitor` rows: its main list, numbered, and its reported-closed list. Both are written in the same savepoint as the row; a teaser records neither.
7. Everything after the checks runs in a savepoint of its own, so a failure never rolls back the job page's own changes saved in the same request. Any failure removes the file, logs only the error's class name and the job number, and raises `ReportGenerationError` with "The report could not be generated.", or "The teaser could not be generated." for a teaser. `generate_report` and `generate_teaser` share this flow in `generation.py`.

### Rendering

1. Django templates under `axisence/reports/templates/reports/` render the HTML with print CSS, the stylesheet inlined into it. WeasyPrint renders the PDF: US Letter, set in Inter, which fontconfig finds in the image.
2. WeasyPrint's URL fetcher refuses every URL of every scheme, `file:` and `data:` included, and stops rendering instead of leaving a resource out, so rendering loads nothing from outside the report's own HTML.
3. The schematic map is inline SVG drawn from the database alone: tabulation-area outlines, the search radius, the target, and numbered competitors, computed with GEOS in EPSG:2263 so the circle stays round and the scale bar true. The SVG holds only coordinates relative to the map, never geographic ones.
4. The PDF's title is "Axisence competitor analysis, report <number>", its language `en-US`, and it has no author, subject, or keywords.

### Pages and permissions

| Page | URL | Holds |
| --- | --- | --- |
| Research job | `/admin/research/researchjob/<id>/change/` | "Generate report" and "Generate teaser" beside the status buttons, and the Reports section listing the job's reports and teasers newest first, with the report each report is compared with. |
| Reports | `/admin/reports/report/` | Every report and teaser with its job, kind, generation time, the report each report is compared with, vintage, and download link, filtered by kind (Draft, Final, or Teaser) or job and searched by job number. |
| Download | `/admin/reports/report/<id>/download/` | The stored PDF as an attachment under its stored file name; a missing file returns to the previous page with "The file of report #N is no longer stored." |
| Transit stops | `/admin/transit/transitstop/` | Every stored subway station and bus stop with its mode, published ID, lines, station group, activity, and first and last runs, filtered by mode or activity and searched by name or exact stop ID. Nobody can add, change, or delete a stop. |

1. `admin.py` unregisters the research app's `ResearchJobAdmin` and registers its subclass `ResearchJobWithReportsAdmin` in place, with a template that fills the research template's empty `job_action_buttons` block, so the research app never imports the reports app. Both buttons post the page, so the job's own changes, such as research statuses, are saved before the report or teaser is generated. `axisence/subscriptions/admin.py` in turn imports `ResearchJobWithReportsAdmin`, unregisters it, and registers its subclass `ResearchJobWithSubscriptionsAdmin` in its place.
2. "Generate report" shows for a job `generation.is_eligible` accepts, to a user who can change the job and holds `reports.generate_report`. "Generate teaser" shows for a job `generation.is_teaser_eligible` accepts, a Prospect job that is not Cancelled and has a confirmed point, to a user who can change the job and holds `reports.generate_teaser`; blank contact settings and an empty search do not hide it. Every post checks the button's permission and the job's view permission again, and the service checks every rule again.
3. `Report` has three permissions: view, `generate_report`, and `generate_teaser`. Downloading needs the view permission.
4. Reports are never changed or deleted: the admin refuses adding, changing, and deleting them for every user; `Report.save` accepts only the first insert; `Report.delete` and the queryset's `update` and `delete` raise `PermanentRecordError`; and every foreign key from a report is `PROTECT`. Listed competitors follow the same guards and have no admin page.

### Logging

1. A generated report logs "Research job <id> report <number> generated (<kind>)"; a failure logs the job number and only the class name of the error. A generated teaser logs "Research job <id> teaser <number> generated", and a failed one "Research job <id> teaser generation failed: <class name>".
2. WeasyPrint's and fontTools' loggers write to a discarding handler in `LOGGING`, because their records can quote the HTML, CSS, or text of a report.

### Teasers

1. `generation.generate_teaser` refuses, in this order: a job whose purpose is not Prospect, a Cancelled job, an unconfirmed point, blank contact settings, no counted competitor, a mismatching Census figure list, and a page that is not exactly one US Letter page. A Draft job qualifies.
2. `teaser_content.build_teaser` reads the job's search candidates, the operating statuses current at the cut-off, the competitors' active providers, the tabulation area, five Census figures, and the provider runs, in a fixed number of queries. It never reads the job's selection or any other research value.
3. `rendering.render_one_letter_page_pdf` lays the HTML out once, checks for exactly one page of 816 by 1056 CSS pixels, and writes the PDF from that layout. Anything else raises `NotOneLetterPageError`, which becomes the refusal "The teaser does not fit on one page."; the reserved number stays unused.
4. `templates/reports/teaser.html`, `teaser.css`, and `teaser_map.html` are the teaser's own. The organization's name and the competitor names are clamped to two lines with `line-clamp`. The footer is two running elements in the page's bottom margin. The map's marks are sized for its narrower print width and add plain dots for the competitors beyond the eighth.
5. The teaser passes its marks' sizes, `schematic_map.MarkSizes`, to `schematic_map.build_report_map`, which then keeps the marks apart:
    1. Numbers are placed nearest competitor first. A number stays inside the map, clear of the target, the scale bar, every other numbered position, and the numbers and lines placed before it; where its own position breaks that, it moves to a free spot on rings around the position, directions away from the target first. The nearest ring leaves 10 map units of line visible.
    2. A moved number's line keeps clear of the numbers placed before it and of every other position, except those so near its own that its line must start beside them. It crosses no other line, and it keeps clear of the target unless it starts beside it, in which case it heads away.
    3. Among the spots that meet all of that, up to one ring beyond the nearest, the number prefers one lying at least 10 map units nearer its own position than any other, then one that leaves every plain dot's center uncovered.
    4. A number with no such spot is crowded. The numbers are then placed again with the crowded ones first, up to eight times, and the layout with the fewest crowded numbers is kept. A crowded number keeps the clearances of item 1 but not those of the lines, and where no spot meets them, it stays on its position. The seeded random markets of `tests/reports/test_schematic_map.py`, at the density around Brooklyn Borough Hall, keep every rule; tight clusters can crowd.
    5. An area code sits at its area's own label point, or else at the nearest spot inside the area, where it lies inside the map clear of the target, the scale bar, the numbers, their lines and moved positions, and the codes already placed. Within three search steps of that spot, it prefers one also clear of the plain dots and the radius circle. Areas with a larger part on the map are placed first, and a code with no such spot is left out. Code sizes use Inter's widest digit and its capital height.
    6. `teaser_map.html` reads the sizes from the map and draws the target over the dots, then the lines and their position dots, then the numbers. The legend's "Position of a moved number" entry explains the position dot and its line.
    7. Without sizes, as for the client report's map, every number sits on its position and every code at its area's own label point.
6. The PDF's title is "Axisence market preview <number>" and its language `en-US`. It has no other metadata field, and its outline holds only the fixed headings.
7. Teasers are `Report` rows of kind `teaser`, numbered in the same sequence as reports and never changed or deleted. They download through the same view.

## Subscriptions and repeat jobs

`axisence.subscriptions` prepares repeat jobs and keeps subscriptions, a client's standing order for repeat reports on a cadence. `Docs/Domain/DataSources.md` describes how a repeat report compares itself with its compared report.

### Pages

| Page | URL | Holds |
| --- | --- | --- |
| Research job | `/admin/research/researchjob/<id>/change/` | The "Repeats" module, with the job a repeat job repeats, the jobs that repeat it with their statuses, and the subscription and due date it serves; and the "Prepare repeat job" button beside the status buttons. |
| Subscriptions | `/admin/subscriptions/subscription/` | Every subscription with its organization, starting job, cadence, start and end dates, next due date, and due state, ordered by next due date with subscriptions without one last, filtered by due state or cadence, and searched by job number. |
| Subscription | `/admin/subscriptions/subscription/<id>/change/` | The starting job, cadence, start date, and end date; the next due date, due state, creator, and creation time, read-only; the due dates with the jobs serving them, their statuses, and their final reports; and the "Prepare repeat job" button. |
| Prepare repeat job | `/admin/subscriptions/subscription/<id>/prepare-repeat-job/` | POST only: prepares the repeat job of the subscription's latest job and opens it. |

1. The job page's button posts the research job change form, so the page's own changes are saved first, and it shows only to users who can also change research jobs. The subscription page's button is a separate small form outside the change form, so researchers, who only view subscriptions, can post it.
2. `admin.py` computes every subscription's progress in a fixed number of queries and annotates the next due date and the due state onto the rows by number, so the list orders and filters by them in SQL while due dates are computed only in Python.

### Preparation

1. One service, `repeats.prepare_repeat_job`, sits behind both buttons. From a subscription's page, `repeats.prepare_repeat_job_for_subscription` first refuses an ended subscription and then calls it for the latest job of the subscription's line. The service refuses, in this order, a job whose purpose is not Client report, a job that is not Complete, a job without a final report with listed competitors, a job that an open repeat job already follows, and a subscription without a due date left.
2. A repeat job copies the organization, the target address and point, the point's source, every lookup field, the search settings, the own competitor resolved through merges, and the assigned researcher. Its point is unconfirmed, it is Draft with the purpose Client report, the preparing user is its creator, and it has no selection, contact, or report. The address is never looked up and no request is sent.
3. A repeat of the latest job of a running subscription's line serves the earliest due date that no serving job that is not Cancelled serves, recorded as a `ServingJob`. A repeat of any other job serves no due date.
4. Everything runs in one transaction, and every refusal comes before anything is written. Job rows are locked before subscription rows, in preparation, in the reopening rule, and in subscription validation, so no two transactions wait on each other in a cycle.

### Repeat rules

1. The partial unique constraint `one_open_repeat_per_research_job` keeps at most one repeat job that is not Cancelled per job.
2. `jobs.change_status` refuses cancelling a job that an open repeat job follows, reopening a Cancelled repeat job while another repeat of its predecessor is open, and reopening a Cancelled repeat job whose predecessor is Cancelled, so a line of repeat jobs only ever shortens from its end.
3. `jobs.change_status` sends `signals.job_status_changing` with the locked job before storing a new status. The subscriptions app's receiver, connected in its `apps.py`, refuses reopening a Cancelled serving job while another serving job that is not Cancelled serves its due date, or while its due date lies after the subscription's end date.

### Due dates

1. Due dates are fixed from the start date: round N is the start date plus N times the cadence's months, on the month's last day when the start date's day does not exist in it. Dates after the end date do not exist.
2. A due date is delivered when a serving job that is not Cancelled serves it and has a final report. The next due date is the earliest due date not delivered.
3. The due state is Overdue when the next due date lies before today and Due soon from today to `DUE_SOON_DAYS` (14) days after. Today is the local date when the page loads.
4. Nothing happens because a date arrives: no command, scheduler, or reminder runs, and nothing is sent.

### Permissions

1. `Subscription` has the permissions add, change, view, and `prepare_repeat_job`. Adding and changing subscriptions stays with the superuser; researchers view them read-only.
2. Preparing needs `subscriptions.prepare_repeat_job`, `research.add_researchjob`, and the research job admin's view permission, checked on every post; the service checks every rule again.
3. The starting job, cadence, and start date become read-only once a serving job of the subscription exists; the end date stays editable within its limits. `lifecycle.validate_subscription` checks every change, from the form's `clean` and again when saving.

### Permanent records

Subscriptions are never deleted: the admin refuses deletion for every user, and `Subscription.delete` and the queryset's `delete` raise `PermanentRecordError`. Serving jobs follow the guards of reports and have no admin page.

### Logging

Preparing logs "Research job <id> prepared as a repeat of research job <id>", or the same followed by "for subscription <id>, due <date>". Saving a subscription logs "Subscription <id> created" or "Subscription <id> changed". The records hold numbers and dates only; refusals are not logged.

### Queries

1. A repeat report's changes section adds at most five queries to its build, whatever the number of competitors, proven by `tests/reports/test_changes.py`.
2. The call sheet's history adds no query: the current values and the history come from one query of observations, proven by `tests/research/test_value_history.py`.
3. The Subscriptions list runs a fixed number of queries, proven by `tests/subscriptions/test_admin.py`.

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
| Load MTA transit | `docker compose run --rm web python manage.py ingest_mta_transit` | container |
| Generate a report | `docker compose run --rm web python manage.py generate_report <job id>` | container |
| Generate a teaser | `docker compose run --rm web python manage.py generate_teaser <job id>` | container |
| Tests | `docker compose run --rm web pytest` | container |
| Type check | `docker compose run --rm web mypy .` | container |
| Lint | `uv run ruff check` | host |
| Format | `uv run ruff format` | host |
| Sync the host environment | `uv sync` | host |

`docker compose run --rm` works whether or not `web` is running, starts `db` when needed, and costs a container start of a few seconds per command. The provider commands also take `--snapshot PATH` and `--allow-mass-disappearance`, and `ingest_mta_transit` takes `--from-latest-snapshots` and `--allow-mass-disappearance`; the README describes when to use them.

In the live folder the same commands apply unchanged, because its `.env` sets `COMPOSE_FILE=compose.live.yaml`. The scripts run on the host, in the folder named. The user runs them; Claude never does.

| Purpose | Command | Folder |
| --- | --- | --- |
| Back up the live copy | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\back-up-data.ps1 -Destination <backup folder>` | live |
| Back up the development copy | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\back-up-data.ps1 -Copy Development -Destination <backup folder>` | development |
| Restore into a fresh live copy | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restore-data.ps1 -Source <backup folder>` | live |
| Release to the live copy | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-live-copy.ps1 -BackupDestination <backup folder>` | live |

## Tests and type checking

1. pytest with pytest-django is configured in `pyproject.toml`, with `DJANGO_SETTINGS_MODULE = "config.settings"` and tests under `tests/`. The suite runs only in the `web` container, against the Compose PostGIS server, where Django creates and destroys its test database. Tests never use SQLite and never touch the network.
2. Tests that need the database are marked `@pytest.mark.django_db`. A test that needs a model no production app defines declares a throwaway model in an isolated app registry (`django.test.utils.isolate_apps`) and creates its table with the schema editor inside the test's transaction, as `tests/test_postgis.py` does.
3. mypy runs in strict mode with the django-stubs plugin, configured in `pyproject.toml`. The plugin imports the settings and models, and with them GDAL, so mypy runs only in the container. Test code may leave function definitions unannotated; their bodies are still checked. Migrations and `scratch/` are excluded. WeasyPrint ships no type information, so an override ignores its missing imports and its names are untyped; no WeasyPrint class is subclassed, which strict mode would refuse.
4. `tests/fixtures/` holds the recorded inputs: provider export rows cut from real snapshots, with every phone number, licensee name, and home name, address, and coordinate replaced by synthetic values; trimmed Census Data API responses; a recorded batch geocoder response; a recorded single-address geocoder response for Brooklyn Borough Hall, a public building; and the seven MTA feeds trimmed to the stations and stops around Borough Hall and one stop of each other borough's feed, `tests/fixtures/mta-gtfs/`, with their real rows unchanged except the calendar end dates, moved far into the future so the fixtures never expire. They hold public or synthetic data only.
5. Autouse fixtures in `tests/conftest.py` keep every test off the network and off the real data: `http_session.build_session` and `http_session.build_lookup_session` are wrapped to mount a fake transport, `tests/ingestion/fake_transport.py`, which answers only the routes a test registers and records each request and its timeout; `RAW_DATA_DIRECTORY` and `REPORT_DIRECTORY` point at temporary directories; `STATIC_ROOT` points at an existing temporary directory, so WhiteNoise never warns about a missing one and `collectstatic` never writes into the repository; both secrets are blank unless a test sets synthetic values; and the three contact settings are blank, while the `contact_settings` fixture of `tests/reports/conftest.py` sets synthetic ones on request. Every WeasyPrint call in tests uses the refusing URL fetcher, so rendering never reaches the network.
6. `tests/config/test_settings.py` executes the settings module with `runpy` under each value of `DJANGO_BEHIND_HTTPS_PROXY`, without touching the configured settings, and serves collected static files through the test client with debug mode off, as the live copy does.
7. `tests/demographics/shapefiles.py` writes a minimal polygon shapefile with a NAD83 projection into a zip, so the boundary tests run GDAL on synthetic areas instead of the 67 MB national file.
8. `tests/transit/gtfs_archives.py` writes GTFS zips from rows written in the tests, either minimal feeds with the required headers or the trimmed fixtures read back and changed, so each feed edge case shows the rows it relies on and no edge-case zip is committed.
9. `tests/admin_forms.py` builds the data an admin change page posts, its main form and inline formsets included, and `tests/research/call_sheet_posts.py` does the same for the call sheet.
10. django-stubs declares `ModelAdmin`, `TabularInline`, and `ModelForm` generic, but those classes cannot be subscripted at runtime. Each `admin.py` therefore defines its parameterized base classes under `if TYPE_CHECKING:` and the plain classes otherwise, which keeps strict typing without a runtime dependency or `type: ignore` comments.
11. Layout inspection renders reports and teasers to check how they look. `tests/reports/test_layout_inspection.py` is marked `layout_inspection`, which `addopts` deselects, so the normal run skips it. It generates four reports on synthetic data in the test database in one test, six teasers in a parametrized test, one transaction per teaser case so no case counts another's competitors, and two repeat reports, `repeat-dense-market` and `repeat-nothing-verified-again`, in a parametrized test with one transaction per case. The four reports share a dense synthetic transit network: six station groups with long names and up to ten lines, and eighteen bus lines; the final report also lists a competitor without a location. The teaser cases are a dense market of 74 competitors with long names, a 255-character organization name, and long contact settings; one competitor; a target outside every tabulation area; no Census vintage; unpublished and incomplete capacities; and an own competitor with home-based kinds. It copies the PDFs into `scratch/report-inspection/`, the teasers as `teaser-<case>.pdf` and the repeat reports as `<case>.pdf`, where they remain:

    ```powershell
    docker compose run --rm web pytest -m layout_inspection tests/reports/test_layout_inspection.py
    ```

    `docker compose run --rm web pdftoppm -png -r 110 scratch/report-inspection/<name>.pdf scratch/report-inspection/<name>` turns each page into an image, `pdffonts` lists the embedded fonts, and `pdfinfo` the metadata and page size. No local data is used.
12. ruff runs on the host and is configured in `pyproject.toml`. Its rule set enforces the `CLAUDE.md` rules that have a linter equivalent, among them timezone-aware datetimes, no `print`, annotated signatures outside tests, no SQL built by string formatting, explicit HTTP timeouts, and no hard-coded secrets. Migrations are excluded from linting and formatting.
