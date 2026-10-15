# Project Architecture

How Axisence runs: what runs on the Windows host and what runs in containers, the Compose services, the package map, settings, commands, and the test and type-checking setup.

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
| `tests` | The test suite, mirroring the packages above. |

Every Django app is a subpackage of `axisence`, for example `axisence.providers`, and is listed in `INSTALLED_APPS` in `config/settings.py`. The admin at `/admin/` is the only URL.

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
| `DJANGO_LOG_LEVEL` | no | `INFO` | Console logging level for every logger, Django's own included |
| `POSTGRES_HOST` | yes | none | Database host; `db` under Compose |
| `POSTGRES_PORT` | no | `5432` | Database port |
| `POSTGRES_DB` | yes | none | Database name; also read by the PostGIS image |
| `POSTGRES_USER` | yes | none | Database user; also read by the PostGIS image |
| `POSTGRES_PASSWORD` | yes | none | Database password; also read by the PostGIS image |

Values that are not environment-specific are fixed in code: the PostGIS database engine, `USE_TZ = True`, and `TIME_ZONE = "America/New_York"`.

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
| Tests | `docker compose run --rm web pytest` | container |
| Type check | `docker compose run --rm web mypy .` | container |
| Lint | `uv run ruff check` | host |
| Format | `uv run ruff format` | host |
| Sync the host environment | `uv sync` | host |

`docker compose run --rm` works whether or not `web` is running, starts `db` when needed, and costs a container start of a few seconds per command.

## Tests and type checking

1. pytest with pytest-django is configured in `pyproject.toml`, with `DJANGO_SETTINGS_MODULE = "config.settings"` and tests under `tests/`. The suite runs only in the `web` container, against the Compose PostGIS server, where Django creates and destroys its test database. Tests never use SQLite and never touch the network.
2. Tests that need the database are marked `@pytest.mark.django_db`. A test that needs a model no production app defines declares a throwaway model in an isolated app registry (`django.test.utils.isolate_apps`) and creates its table with the schema editor inside the test's transaction, as `tests/test_postgis.py` does.
3. mypy runs in strict mode with the django-stubs plugin, configured in `pyproject.toml`. The plugin imports the settings and models, and with them GDAL, so mypy runs only in the container. Test code may leave function definitions unannotated; their bodies are still checked. Migrations and `scratch/` are excluded.
4. ruff runs on the host and is configured in `pyproject.toml`. Its rule set enforces the `CLAUDE.md` rules that have a linter equivalent, among them timezone-aware datetimes, no `print`, annotated signatures outside tests, no SQL built by string formatting, explicit HTTP timeouts, and no hard-coded secrets. Migrations are excluded from linting and formatting.
