# Axisence

Competitor analysis for independent childcare providers. This repository holds the software behind it: Django with GeoDjango on PostgreSQL with PostGIS.

## How it runs

Django, its tests, every `manage.py` command, and mypy run only inside the `web` service of Docker Compose. Its Debian image provides the GDAL, GEOS, and PROJ libraries that GeoDjango loads, so none of them is installed on Windows. The Windows host runs only `uv`, for the editor's environment and the dependencies, and ruff.

The `db` service runs PostgreSQL with PostGIS. Both services publish their ports on 127.0.0.1 only.

Version numbers live only in the project files: `.python-version`, `pyproject.toml`, `uv.lock`, `compose.yaml`, and `Dockerfile`.

## Set up on Windows 11

1. Install the prerequisites:
    - Docker Desktop with the WSL 2 backend. Hardware virtualization must be enabled in the firmware settings.
    - [uv](https://docs.astral.sh/uv/). It also provides Python.
    - Git.
2. Clone the repository:

    ```powershell
    git clone https://github.com/koStitch/axisence.git
    cd axisence
    ```

3. Create `.env` from the template:

    ```powershell
    Copy-Item .env.example .env
    ```

    Replace the placeholder values of `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` with long random values. This command prints one each time it runs:

    ```powershell
    uv run --no-project python -c "import secrets; print(secrets.token_urlsafe(50))"
    ```

    The database takes `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` only on its first start. Changing them later requires resetting the database volume.

4. Create the host environment for the editor and ruff:

    ```powershell
    uv sync
    ```

5. Start the services. The first start builds the `web` image and initializes the database, which takes a few minutes:

    ```powershell
    docker compose up -d
    ```

6. Apply the migrations, run the health check, and run the tests:

    ```powershell
    docker compose run --rm web python manage.py migrate
    docker compose run --rm web python manage.py healthcheck
    docker compose run --rm web pytest
    ```

7. Optionally, create an administrator and log in to the admin at http://127.0.0.1:8000/admin/:

    ```powershell
    docker compose run --rm web python manage.py createsuperuser
    ```

## Everyday commands

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

`docker compose run --rm` works whether or not `web` is running, and starts `db` when needed. Starting the services never applies migrations; `migrate` is always run explicitly. After a dependency change, rebuild the image with `docker compose build`.

## Data pipeline

Four commands load public data into the local database: licensed providers from both regulators, Census demographics, and tabulation-area boundaries. Each run is recorded in the admin under Ingestion. Every download is saved unchanged under the git-ignored `data/raw/` before anything parses it, and the pipeline never modifies or deletes a saved download. Disk use is about 6 MB per NY State run, 0.6 MB per NYC Health run, 48 MB per Census vintage, and 67 MB per boundary download. The folder is pruned by hand only.

### Before the first run

1. Request a Census Data API key at https://api.census.gov/data/key_signup.html. The key arrives by email.
2. Add `CENSUS_API_KEY=<the key>` to `.env`.
3. Optionally add `SOCRATA_APP_TOKEN` to `.env`. It is not needed: without it, requests share Socrata's anonymous throttling pool.
4. Nothing needs a restart: each `docker compose run` command starts a fresh container that reads `.env`.
5. Apply the migrations:

    ```powershell
    docker compose run --rm web python manage.py migrate
    ```

6. Create the admin account, then log in at http://127.0.0.1:8000/admin/:

    ```powershell
    docker compose run --rm web python manage.py createsuperuser
    ```

### Running the commands

| Command | Loads | Cadence |
| --- | --- | --- |
| `ingest_census_boundaries` | The 2020 cartographic boundaries of every NY State tabulation area | Once per decade |
| `ingest_census_demographics --vintage <year>` | The ACS 5-year data profile tables `DP02` to `DP05` of every NY State tabulation area | Once a year, after the Census Bureau's December release of a new 5-year vintage, with `--vintage` set to that year |
| `ingest_ny_state_providers` | Every program of the NY State OCFS Child Care Regulated Programs dataset | Weekly, and before preparing a report |
| `ingest_nyc_health_providers` | Every program of the NYC Health Active Child Care Programs dataset | Weekly, and before preparing a report |

Nothing runs these commands automatically: run each one by hand at its cadence. Run them in this order on a first load. In the output, `<run>` is the run's number, `<timestamp>` the UTC download time, `<bytes>` a file size, and `<count>` a number that varies with the publishers' daily updates.

1. Load the boundaries:

    ```powershell
    docker compose run --rm web python manage.py ingest_census_boundaries
    ```

    ```text
    Census boundaries run <run> succeeded.
    Snapshot: census-boundaries/<timestamp>-run-<run>-acs5-profile-2024-new-york-tabulation-areas.json (<bytes> bytes)
    Snapshot: census-boundaries/<timestamp>-run-<run>-cb_2020_us_zcta520_500k.zip (<bytes> bytes)
    Rows read: 33791
    Created: 1824
    Updated: 0
    Unchanged: 0
    Field warnings: 0
    ```

    A first load reads all 33,791 tabulation areas of the file and stores the 1,824 of NY State.

2. Load the demographics. The command takes a few minutes, because it writes about one million figures:

    ```powershell
    docker compose run --rm web python manage.py ingest_census_demographics --vintage 2024
    ```

    ```text
    Census demographics run <run> succeeded.
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP02.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP02-variables.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP03.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP03-variables.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP04.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP04-variables.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP05.json (<bytes> bytes)
    Snapshot: census-acs-profile/<timestamp>-run-<run>-acs5-profile-2024-DP05-variables.json (<bytes> bytes)
    Vintage: 2024
    Rows read: 7296
    Created: 988608
    Updated: 0
    Unchanged: 0
    Field warnings: 0
    ```

    A first load of vintage 2024 reads 1,824 areas from each of the four tables and creates 988,608 figures.

3. Load the NY State providers:

    ```powershell
    docker compose run --rm web python manage.py ingest_ny_state_providers
    ```

    ```text
    NY State providers run <run> succeeded.
    Snapshot: ny-ocfs/<timestamp>-run-<run>-child-care-regulated-programs-metadata.json (<bytes> bytes)
    Snapshot: ny-ocfs/<timestamp>-run-<run>-child-care-regulated-programs-export.csv (<bytes> bytes)
    Snapshot: census-geocoder/<timestamp>-run-<run>-addressbatch-result-1.csv (<bytes> bytes)
    Publisher last updated: <date> <time> <time zone>
    Rows read: <count>
    Created: <count>
    Updated: <count>
    Unchanged: <count>
    Marked missing: <count>
    Reactivated: <count>
    Skipped rows: <count>
    Field warnings: <count>
    Competitors created: <count>
    Review flags raised: <count>
    Geocoded: <count>
    Geocoding failures: <count>
    ```

    A first load reads and creates about 16,800 providers with about 2 field warnings, creates about 16,700 competitors, raises about 90 review flags, and geocodes about 50 providers, counting `Geocoded` and `Geocoding failures` together.

4. Load the NYC Health providers:

    ```powershell
    docker compose run --rm web python manage.py ingest_nyc_health_providers
    ```

    ```text
    NYC Health providers run <run> succeeded.
    Snapshot: nyc-dohmh/<timestamp>-run-<run>-active-child-care-programs-metadata.json (<bytes> bytes)
    Snapshot: nyc-dohmh/<timestamp>-run-<run>-active-child-care-programs-export.csv (<bytes> bytes)
    Snapshot: census-geocoder/<timestamp>-run-<run>-addressbatch-result-1.csv (<bytes> bytes)
    Publisher last updated: <date> <time> <time zone>
    Rows read: <count>
    Created: <count>
    Updated: <count>
    Unchanged: <count>
    Marked missing: <count>
    Reactivated: <count>
    Skipped rows: <count>
    Field warnings: <count>
    Competitors created: <count>
    Review flags raised: <count>
    Geocoded: <count>
    Geocoding failures: <count>
    ```

    A first load after the NY State one reads and creates about 2,750 providers with about 1 field warning, creates about 2,200 competitors, raises about 110 review flags, and geocodes about 11 providers plus the NY State failures, which every run retries.

A run without geocoding candidates has no `census-geocoder` snapshot line.

### Re-running from a saved download

`--snapshot` re-ingests a saved export without fetching anything. Its path is the export's `Snapshot:` line of the earlier run, prefixed with `data/raw/`:

```powershell
docker compose run --rm web python manage.py ingest_ny_state_providers --snapshot data/raw/ny-ocfs/<timestamp>-run-<run>-child-care-regulated-programs-export.csv
```

```powershell
docker compose run --rm web python manage.py ingest_nyc_health_providers --snapshot data/raw/nyc-dohmh/<timestamp>-run-<run>-active-child-care-programs-export.csv
```

Re-running a provider command on the export it just fetched reports `Created: 0`, `Updated: 0`, and `Marked missing: 0`, followed by `Re-ingested an existing snapshot.`

### When a run fails

1. The command ends with `CommandError: <source> run <run> failed: <reason>` and a non-zero exit status, and the run appears as failed in the admin.
2. A provider run refuses to mark more than 10% of a source's active providers missing. When the publisher really removed them, run the command again with `--allow-mass-disappearance`.
3. A missing Census key fails both Census commands, before any request, with a message naming `CENSUS_API_KEY`.

### Reviewing competitors

1. Review flags appear in the admin under Providers and competitors, Review flags. Select flags and choose one action:
    1. "Same operator: merge the flagged competitor into the proposed competitor".
    2. "Different operators": the pair is never proposed again.
    3. "Provider stays in its competitor", for a changed-keys flag.
2. Editing a provider's competitor on the provider page moves the provider.
3. The provider list action "Move the selected providers to a new competitor of their own" gives each selected provider a competitor of its own.
4. Editing a competitor's "merged into" on the competitor page merges it into the chosen competitor.
5. Every decision is listed under Providers and competitors, Competitor decisions.

## Documentation

[Docs/README.md](Docs/README.md) indexes the project documentation.
