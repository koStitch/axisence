# Axisence

Competitor analysis for independent childcare providers. This repository holds the software behind it: Django with GeoDjango on PostgreSQL with PostGIS.

## How it runs

Django, its tests, every `manage.py` command, and mypy run only inside the `web` service of Docker Compose. Its Debian image provides the GDAL, GEOS, and PROJ libraries that GeoDjango loads, and the libraries and the font that WeasyPrint needs to render reports, so none of them is installed on Windows. The Windows host runs only `uv`, for the editor's environment and the dependencies, and ruff.

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
| Load MTA transit | `docker compose run --rm web python manage.py ingest_mta_transit` | container |
| Generate a report | `docker compose run --rm web python manage.py generate_report <job id>` | container |
| Generate a teaser | `docker compose run --rm web python manage.py generate_teaser <job id>` | container |
| Tests | `docker compose run --rm web pytest` | container |
| Type check | `docker compose run --rm web mypy .` | container |
| Lint | `uv run ruff check` | host |
| Format | `uv run ruff format` | host |
| Sync the host environment | `uv sync` | host |

`docker compose run --rm` works whether or not `web` is running, and starts `db` when needed. Starting the services never applies migrations; `migrate` is always run explicitly. After a dependency change, rebuild the image with `docker compose build`.

## Data pipeline

Five commands load public data into the local database: licensed providers from both regulators, Census demographics, tabulation-area boundaries, and MTA transit stops. Each run is recorded in the admin under Ingestion. Every download is saved unchanged under the git-ignored `data/raw/` before anything parses it, and the pipeline never modifies or deletes a saved download. Disk use is about 6 MB per NY State run, 0.6 MB per NYC Health run, 48 MB per Census vintage, 67 MB per boundary download, and about 60 MB on a first transit run, then only the feeds the MTA has republished. The folder is pruned by hand only.

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
| `ingest_mta_transit` | Every subway station and bus stop of New York City, with the lines that stop there on weekday daytime service | Before each batch of client reports, together with the provider commands |

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

5. Load the transit stops:

    ```powershell
    docker compose run --rm web python manage.py ingest_mta_transit
    ```

    ```text
    MTA transit run <run> succeeded.
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_subway.zip (<bytes> bytes)
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_b.zip (<bytes> bytes)
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_bx.zip (<bytes> bytes)
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_m.zip (<bytes> bytes)
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_q.zip (<bytes> bytes)
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_si.zip (<bytes> bytes)
    Snapshot: mta-gtfs/<timestamp>-run-<run>-gtfs_busco.zip (<bytes> bytes)
    Created: <count>
    Updated: 0
    Unchanged: 0
    Marked missing: 0
    Reactivated: 0
    Field warnings: <count>
    Feed gtfs_subway.zip: downloaded
    Feed gtfs_b.zip: downloaded
    Feed gtfs_bx.zip: downloaded
    Feed gtfs_m.zip: downloaded
    Feed gtfs_q.zip: downloaded
    Feed gtfs_si.zip: downloaded
    Feed gtfs_busco.zip: downloaded
    ```

    A first load creates about 14,000 stops, about 500 subway stations and 13,500 bus stops, usually without field warnings. "Marked missing" counts stops the feeds no longer list, which are marked inactive and never deleted; a stop listed again is reactivated.

    A run asks the MTA for each feed only if it changed since the saved download. When none changed, nothing is downloaded and the earlier downloads are read again:

    ```text
    MTA transit run <run> succeeded.
    Snapshot: mta-gtfs/<earlier timestamp>-run-<earlier run>-gtfs_subway.zip (<bytes> bytes)
    ...
    Created: 0
    Updated: 0
    Unchanged: <count>
    Marked missing: 0
    Reactivated: 0
    Field warnings: <count>
    Feed gtfs_subway.zip: unchanged
    ...
    ```

    The `Snapshot:` lines name the earlier downloads, one per feed, and every feed reads `unchanged`. A feed the MTA has republished reads `downloaded` and gets a new snapshot of its own.

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

A re-ingestion also re-reads the metadata saved with that export, so its `Publisher last updated:` line, and every report generated after it, give the date of the data actually loaded, however old the saved download is.

`--from-latest-snapshots` re-ingests every transit feed's latest saved download without asking the MTA for anything:

```powershell
docker compose run --rm web python manage.py ingest_mta_transit --from-latest-snapshots
```

Every feed line then reads `read from the latest snapshot`, followed by `Re-ingested an existing snapshot.` in the summary. A feed without a saved download on disk fails the run with a message naming it.

### When a run fails

1. The command ends with `CommandError: <source> run <run> failed: <reason>` and a non-zero exit status, and the run appears as failed in the admin.
2. A provider run refuses to mark more than 10% of a source's active providers missing. When the publisher really removed them, run the command again with `--allow-mass-disappearance`.
3. A missing Census key fails both Census commands, before any request, with a message naming `CENSUS_API_KEY`.
4. A transit run refuses to mark more than 10% of the active subway stations, or of the active bus stops, inactive, and stores nothing. When the MTA really removed them, run the command again with `--allow-mass-disappearance`.
5. A transit run fails, before storing anything, with `Fetching <feed> failed: <reason>` when a feed cannot be downloaded. Run the command again later.
6. A transit run fails with `The feed <feed> has no weekday service running on or after <date>.` when every weekday service of that feed has ended before the MTA published new ones. Run the command again once the MTA has republished the feed; reports keep using the last succeeded transit run meanwhile.

### Reviewing competitors

1. Review flags appear in the admin under Providers and competitors, Review flags. Select flags and choose one action:
    1. "Same operator: merge the flagged competitor into the proposed competitor".
    2. "Different operators": the pair is never proposed again.
    3. "Provider stays in its competitor", for a changed-keys flag.
2. Editing a provider's competitor on the provider page moves the provider.
3. The provider list action "Move the selected providers to a new competitor of their own" gives each selected provider a competitor of its own.
4. Editing a competitor's "merged into" on the competitor page merges it into the chosen competitor.
5. Every decision is listed under Providers and competitors, Competitor decisions.

## Research

Researchers run research jobs in the admin, under Research. A job looks its target address up, lists the nearest licensed competitors, and collects what each one charges and whether it has room, one contact at a time.

### Researcher accounts

Each researcher has an account of their own.

1. Log in at http://127.0.0.1:8000/admin/ as the administrator.
2. Under Accounts, open Users and choose "Add user".
3. Enter the researcher's username and a password the researcher chooses, then choose "Save". Expected: the user's page opens.
4. Tick "Staff status".
5. Under Groups, move "Researchers" to "Chosen groups", then choose "Save".

Expected: the researcher logs in with that account and sees Research jobs, Job competitors, Contacts, and Observations under Research; the provider, competitor, and review flag pages with their review actions; the demographics pages read-only; Transit stops under Transit, read-only; Reports under Reports, with "Generate report" on the pages of eligible client report jobs and "Generate teaser" on the pages of eligible prospect jobs; and Subscriptions under Subscriptions, read-only, with "Prepare repeat job" on running subscriptions, and "Prepare repeat job" on the pages of Complete client report jobs that can be repeated.

### Running a research job

1. Under Research, open Research jobs and choose "Add research job".
2. Choose the purpose, enter the organization's name and the target address on one line, optionally choose the assigned researcher, and check the search settings: the number of competitors to pre-select, the search radius in miles, and the provider kinds.
3. Choose "Save". Expected: the address is looked up, and the job opens with the lookup's outcome and either its matched address and pin on the map, a list of matches with "Use this match" buttons, or no pin.
4. Check the matched address and the pin. Correct a wrong or missing pin on the map: drag the pin, or choose "Delete all Features" and click the map to place a new one. After correcting the address itself, "Look up the address again" repeats the lookup.
5. Tick "Confirm the target point" and choose "Save and continue editing". Expected: the confirmation shows who confirmed and when, and the ZIP Code Tabulation Area of the point appears.
6. When the target is itself a listed provider, choose it as "Own competitor", so it is never listed as its own competitor.
7. Choose "Search competitors". Expected: the competitors of the chosen kinds within the radius, nearest first, with their straight-line distances and labels. On the first search, the nearest ones without a reported closure are ticked.
8. Tick and untick competitors, add one with "Add a competitor by name" when it lies outside the results, is of another kind, or has no location, or choose "This is the target itself" beside a result. Then choose "Save selection". Expected: "Selection saved: N added, M removed."
9. Back on the job, choose "Start research". Expected: the job is Researching.

### Calls

1. In the job's list of competitors, choose "Call sheet" beside a competitor. Expected: its published data, labels, current values with their dates and methods, the history of its values, previous contacts, and the entry form, with a notice that the contact will be linked to the job.
2. Enter the contact date, the method, the role of the person spoken to, and any notes. Notes never hold the name, email, or phone number of a person.
3. Every pre-filled value starts "Not covered". Choose "Confirmed" when the value still holds, or "New value" and enter the value found. A changed value left "Not covered" or "Confirmed" is refused.
4. Enter new age groups and schedules in their "New" rows, and new prices, availability, discounts, and other charges in the numbered rows of their sections. A new price or availability row can refer to "New age group A" or "New schedule A" of the same contact. Tick "Retire" beside an age group or schedule the provider no longer uses.
5. A new age group or schedule that matches a current one, by its name ignoring case and extra spaces and by its limits or its days and hours, is refused with a message naming the current one. Choose that definition in the row instead, so its prices keep one history.
6. Choose "Save contact". Expected: "Contact #N recorded with M observations."
7. Back on the job, set the competitor's research status to In progress, Done, or Unreachable, and choose "Save and continue editing".
8. Record a closure as the operating status "Temporarily closed" or "Permanently closed". It labels the competitor wherever it is listed; the provider data stays as its regulator publishes it.

### Completing a job

1. When no competitor is To do or In progress, choose "Mark complete". Expected: the job is Complete.
2. "Reopen research" returns a Complete or Cancelled job to Researching, for late additions.
3. "Cancel job" ends a job that is no longer wanted. Jobs are never deleted.

### Correcting mistakes

1. Under Research, open Contacts and then the contact.
2. Choose "Retract this contact", or "Retract" beside one observation, give the reason, and choose "Retract". Expected: "Contact #N retracted." for a contact, or the observation's kind and number followed by "retracted." for one observation, such as "Price #N retracted." A retraction is final.
3. Record the correct value in a new contact.

### History

1. The call sheet's "History", between "Current values" and "Previous contacts", lists every price, availability, discount, and other charge of the competitor and of the competitors merged into it, slots no longer current included.
2. Each row is a run of one stated value, with its first recorded and its last confirmed contact and the number of contacts. A retired age group or schedule shows under its slots. Retracted values are left out.
3. The call sheet's "Full history" link lists every observation, newest contact first, retracted ones included.

## Reports

A report is a client's competitor analysis as a PDF, generated from a research job whose purpose is "Client report". A Researching job gives a draft, marked "Draft" on every page; a Complete job gives the final report. Each report shows the research values as they stood when it was generated, and is kept with its number; nothing in it can be changed or deleted.

### Generating a draft

1. Under Research, open Research jobs and then the Researching client report job.
2. Choose "Generate report" beside the status buttons. Changes made on the page, such as research statuses, are saved first. Expected: "Draft report #N generated.", and the report appears in the job's Reports section, newest first. Every page of the PDF reads "Draft".

### Generating the final report

1. When no competitor is To do or In progress, choose "Mark complete". Expected: the job is Complete.
2. Choose "Generate report", or run the command of "From the command line" below. Expected: "Report #N generated." on the page, or the command's line.

### Downloading

1. Choose "Download" beside a report in the job's Reports section. Alternatively, under Reports, open Reports, filter by kind or job or search by the job's number, and choose "Download" beside the report.
2. Expected: the browser saves `report-<N>.pdf`.

### Report files

1. Reports are stored as `data/private/reports/report-<N>.pdf`, which git ignores. They are confidential client documents: the application never deletes or overwrites them, and nothing backs them up. Back the folder up and prune it by hand.
2. Downloading a report whose file was pruned shows "The file of report #N is no longer stored." and returns to the previous page.
3. After the local database is reset, report numbers start again at 1. Move the old files out of `data/private/reports/` first: a generation whose file name already exists fails with "The report could not be generated.", or "The teaser could not be generated." for a teaser.

### From the command line

The command generates a draft or the final report by the job's status, without recording a user:

```powershell
docker compose run --rm web python manage.py generate_report <job id>
```

```text
Report #<N> generated: report-<N>.pdf
```

While `DJANGO_LOG_LEVEL` is `INFO` or lower, the log record `Research job <job id> report <N> generated (<kind>)` is printed beside it, before or after. A refusal or a failure ends with `CommandError: <message>` and a non-zero exit status.

### When a report is refused

| Message | Meaning and what to do |
| --- | --- |
| Only client report jobs produce a report. | The job's purpose is Prospect. Choose "Generate teaser" for a teaser of the prospect, or change the purpose to Client report when the job is for a client. |
| A Draft job cannot produce a report. | Research has not started. Confirm the target point, select competitors, and choose "Start research". |
| A Cancelled job cannot produce a report. | Choose "Reopen research" when the job is wanted again. |
| This job lists no competitors. | Choose "Search competitors" and save a selection. |
| The Census figure list for vintage `<year>` does not match the stored labels. | The Census Bureau's labels of that vintage differ from the figures a report shows. A developer checks the codes and labels and updates `axisence/reports/figure_lists.py`; every report is refused until then. |
| Transit data has not been loaded: run ingest_mta_transit first. | No transit run has succeeded. Run `ingest_mta_transit` (Data pipeline), then generate again. |
| The report could not be generated. | An unexpected failure. The log names the error's class and the job's number; no report or file is kept. |

### Generating again

A report can be generated again at any time, for example after a late call. Each generation gets a new number and keeps the earlier reports. Numbers always increase but can skip after a failed generation.

## Teasers

A teaser is a one-page preview of the market around a prospect's location, printed as "Childcare market preview", for sales outreach. It is generated from a research job whose purpose is "Prospect". It holds public data only: the competitors of the job's kinds within its search radius, their licensed capacity, a map, five Census figures, and what the full report verifies, without any value. Every teaser is kept with its number; nothing in it can be changed or deleted. The application never sends a teaser: sending one is always the user's own act.

### Before the first teaser

1. Add Axisence's contact details to `.env`; the website must start with `https://`:

    ```text
    AXISENCE_WEBSITE_URL=https://<the website>
    AXISENCE_CONTACT_EMAIL=<the email address for prospects>
    AXISENCE_CONTACT_PHONE=<the phone number, as it should be printed>
    ```

2. Apply them to the running services:

    ```powershell
    docker compose up -d
    ```

    Expected: Compose recreates and starts the `db` and `web` containers, because both read `.env`; the database keeps its data. If the output does not show the `web` container recreated, run `docker compose up -d --force-recreate web`. Commands run with `docker compose run` read `.env` on every run.

3. A website that does not start with `https://` stops the development server and every command with `ImproperlyConfigured: The environment variable AXISENCE_WEBSITE_URL must start with https://.`

### Generating a teaser

1. Under Research, open Research jobs and choose "Add research job".
2. Choose the purpose "Prospect" and enter the prospect's organization name and the target address on one line. Check the search settings: the radius and the provider kinds decide which competitors the teaser counts. Choose "Save". Expected: the job opens with the lookup's outcome.
3. Check the pin on the map and correct it when needed. Tick "Confirm the target point" and choose "Save and continue editing". Expected: the confirmation and the ZIP Code Tabulation Area appear.
4. When the prospect is itself a listed provider, choose it as "Own competitor". Otherwise it appears as its own nearest competitor.
5. Choose "Generate teaser" beside the status buttons. Changes made on the page are saved first. Expected: "Teaser #N generated.", and the teaser appears in the job's Reports section with the kind "Teaser". No competitor search, selection, or research is needed, and the job can stay Draft.

### Downloading

1. Choose "Download" beside the teaser in the job's Reports section. Alternatively, under Reports, open Reports, filter by kind "Teaser", and choose "Download". Expected: the browser saves `market-preview-<N>.pdf`.
2. Teasers are stored as `data/private/reports/market-preview-<N>.pdf`, beside the reports, under the same rules as report files. Teaser and report numbers share one sequence.

### From the command line

```powershell
docker compose run --rm web python manage.py generate_teaser <job id>
```

```text
Teaser #<N> generated: market-preview-<N>.pdf
```

While `DJANGO_LOG_LEVEL` is `INFO` or lower, the log record `Research job <job id> teaser <N> generated` is printed beside it, before or after. A refusal or a failure ends with `CommandError: <message>` and a non-zero exit status.

### When a teaser is refused

| Message | Meaning and what to do |
| --- | --- |
| Only prospect jobs produce a teaser. | The job's purpose is Client report. Change the purpose to Prospect when the job is for a prospect. |
| A Cancelled job cannot produce a teaser. | Choose "Reopen research" when the job is wanted again. |
| Confirm the target point first. | Check the pin, tick "Confirm the target point", and save. |
| Axisence's contact details are not set: `<names>`. | Set the named variables in `.env` as "Before the first teaser" describes. |
| No competitors of the job's kinds lie within its search radius. | While the job is Draft, widen the search radius or add provider kinds, and save. |
| The Census figure list for vintage `<year>` does not match the stored labels. | As for reports: a developer checks the codes and labels and updates `axisence/reports/figure_lists.py`. |
| The teaser does not fit on one page. | An input the layout cannot hold. A developer adjusts `axisence/reports/templates/reports/teaser.css`; no teaser or file is kept. |
| The teaser could not be generated. | An unexpected failure. The log names the error's class and the job's number; no teaser or file is kept. |

### Generating again

A teaser can be generated again at any time. Each generation gets a new number and keeps the earlier teasers. A teaser stays as generated when the job changes later.

### Before sending a teaser

1. Open the PDF and check the nearest competitors. A prospect that is itself a listed provider appears as its own nearest competitor unless the job names it as "Own competitor".
2. The teaser names the prospect's organization and, through its map and distances, shows the prospect's location. Send it only to that prospect, from outside the application.

## Repeat reports

A repeat report reports again on the same client and target, and states what changed since the last final report the client received. It comes from a repeat job, prepared from the earlier job. A job and the repeat jobs that follow it form a line: a job is followed by at most one repeat job that is not Cancelled.

### Preparing a repeat job

1. Under Research, open Research jobs and then the Complete client report job.
2. Choose "Prepare repeat job" beside the status buttons. Changes made on the page are saved first. Expected: the new Draft job opens with "Research job #`<new>` prepared as a repeat of research job #`<earlier>`.", or, when the earlier job is the latest job of a running subscription, the same message with "for subscription #`<S>`, due `<YYYY-MM-DD>`" before its full stop, and shows "Repeats: research job #`<earlier>`". The new job copies the organization, the address, the pin, the lookup's outcome, the search settings, the own competitor, and the assigned researcher.
3. Check and confirm the pin: tick "Confirm the target point" and choose "Save and continue editing". While the job is Draft, the target and the search settings can still change, and a changed address is looked up again.
4. Choose "Search competitors". Expected: the earlier report's competitors that are still active and not reported permanently closed are ticked with the usual nearest ones, and those outside the results appear under "Selected competitors outside these results". Choose "Save selection".
5. Run the job as any other, from "Start research" on.

### Generating and reading a repeat report

Choose "Generate report" as usual. Expected: the cover reads "Follows report #`<N>` of `<date>`"; the job's Reports section shows "Report #`<N>`" under "Compared with"; and the section "Changes since report #`<N>`" follows the summary, with:

1. the search settings that changed, if any;
2. the competitors added and gone, each with its reason;
3. each competitor in both reports, with its newest contact since report #`<N>`, a table of its values that changed, are new, or ended, and the counts of values re-confirmed unchanged and not re-checked;
4. notes on how values are compared.

### When preparing or a status change is refused

| Message | Meaning and what to do |
| --- | --- |
| Only client report jobs can be repeated. | A prospect job gets teasers, not repeats. |
| Only a Complete job can be repeated. | Finish the job and choose "Mark complete". |
| The job has no final report to compare against. | Choose "Generate report" on the Complete job. This also covers a job whose final reports predate the recording of listed competitors. |
| Research job #`<N>` already repeats this job. | Continue research job #`<N>`, or cancel it first. |
| Subscription #`<S>` has no due date left. | Move or clear the subscription's end date, or add a new subscription once it has ended. |
| Subscription #`<S>` has ended. | Add a new subscription starting from the latest job of the line. |
| Research job #`<N>` repeats this job. Cancel that job first. | Cancel research job #`<N>` first. |
| Research job #`<N>` already repeats research job #`<P>`. | Continue research job #`<N>`, or cancel it first. |
| Research job #`<P>` is Cancelled. Reopen that job first. | Reopen research job #`<P>` first. |
| Research job #`<N>` serves due date `<date>` of subscription #`<S>`. | Continue research job #`<N>`. |
| Due date `<date>` lies after the end date of subscription #`<S>`. | Move the end date to that due date or later first. |

## Subscriptions

A subscription records a client's standing order for a repeat report every quarter, twice a year, or every year, starting from one job. Its due dates are fixed from its start date. Nothing happens because a date arrives: the Subscriptions list is the reminder, and preparing each repeat job stays the user's act.

### Adding a subscription

1. Log in at http://127.0.0.1:8000/admin/ as the administrator. Under Subscriptions, open Subscriptions and choose "Add subscription".
2. Choose the starting job: a Complete client report job with a final report, the latest job of its line. Choose the cadence, the start date, usually the day the starting job's final report reached the client, and an optional end date.
3. Choose "Save and continue editing". Expected: the subscription's page opens with "The subscription “Subscription #`<S>`” was added successfully. You may edit it again below.", and shows its next due date.

| Message | Meaning and what to do |
| --- | --- |
| The starting job must be a Complete client report job with a final report to compare against. | Choose a Complete client report job. When it has no final report since listed competitors were recorded, choose "Generate report" on it first. |
| Research job #`<N>` repeats this job. Start the subscription from the latest job of its line. | Start from the latest job of the line, such as research job #`<N>`. |
| This job already belongs to subscription #`<S>`. | Set an end date on subscription #`<S>`, and add the new subscription once that date has passed. |
| The end date cannot be before the start date. | Correct either date. |
| The end date cannot be before `<date>`, the latest due date served by a job that is not Cancelled. | Set the end date on or after that date. |
| A job of this subscription belongs to subscription #`<S>`. | Keep this subscription ended, or end subscription #`<S>` first. |

### What can change later

1. The starting job, the cadence, and the start date change only until the first repeat job is prepared for a due date. Afterwards the page shows them read-only.
2. The end date changes at any time, but never to a date before the latest due date that a job that is not Cancelled serves, and never so that an ended subscription runs again while another running subscription covers one of its jobs.
3. To pause a subscription or change its cadence, set an end date. Once it has passed, add a new subscription from the latest job of the line.

### Due soon and overdue

1. The Subscriptions list orders subscriptions by their next due date, the earliest due date not yet delivered, with subscriptions without one last. A due date is delivered once a repeat job serving it, and not Cancelled, has a final report.
2. The "Due state" filter's "Overdue" lists the subscriptions whose next due date has passed, and "Due soon" those due today or within the next 14 days.
3. A subscription's page lists its due dates up to today, those a job serves, and the next one, each with its research job and status and its final report, or "Not prepared".

### Preparing a subscription's repeat job

1. On the subscription's page, choose "Prepare repeat job". Expected: the new Draft job opens with "Research job #`<new>` prepared as a repeat of research job #`<latest>` for subscription #`<S>`, due `<YYYY-MM-DD>`.".
2. Continue as in "Preparing a repeat job" from step 3.
3. Cancelling a repeat job frees its due date for the next preparation.

### Sending a report

The application never sends a report or a reminder. Sending the final report to the client is always the user's own act, from outside the application.

## Documentation

[Docs/README.md](Docs/README.md) indexes the project documentation.
