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
| Tests | `docker compose run --rm web pytest` | container |
| Type check | `docker compose run --rm web mypy .` | container |
| Lint | `uv run ruff check` | host |
| Format | `uv run ruff format` | host |
| Sync the host environment | `uv sync` | host |

`docker compose run --rm` works whether or not `web` is running, and starts `db` when needed. Starting the services never applies migrations; `migrate` is always run explicitly. After a dependency change, rebuild the image with `docker compose build`.

## Documentation

[Docs/README.md](Docs/README.md) indexes the project documentation.
