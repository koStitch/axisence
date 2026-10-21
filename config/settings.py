"""Django settings for Axisence.

This is the only settings module. Every environment-specific value is read from an
environment variable through config.environment, and .env.example documents each one.
"""

from pathlib import Path

from config import environment

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = environment.read_string("DJANGO_SECRET_KEY")

DEBUG = environment.read_boolean("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = environment.read_list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "django.contrib.postgres",
    "axisence.accounts",
    "axisence.diagnostics",
    "axisence.ingestion",
    "axisence.providers",
    "axisence.demographics",
    "axisence.research",
    "axisence.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "HOST": environment.read_string("POSTGRES_HOST"),
        "PORT": environment.read_integer("POSTGRES_PORT", default=5432),
        "NAME": environment.read_string("POSTGRES_DB"),
        "USER": environment.read_string("POSTGRES_USER"),
        "PASSWORD": environment.read_string("POSTGRES_PASSWORD"),
    }
}

AUTH_USER_MODEL = "accounts.User"

# Public data pipeline. Both values are optional: a blank token sends no Socrata app
# token, and a blank key stops only the Census commands, before any request.
SOCRATA_APP_TOKEN = environment.read_string("SOCRATA_APP_TOKEN", default="")
CENSUS_API_KEY = environment.read_string("CENSUS_API_KEY", default="")

# Axisence's contact details, printed in every teaser's call to action. All three are
# optional at startup; generating a teaser refuses while any is blank.
AXISENCE_WEBSITE_URL = environment.read_https_url("AXISENCE_WEBSITE_URL", default="")
AXISENCE_CONTACT_EMAIL = environment.read_string("AXISENCE_CONTACT_EMAIL", default="")
AXISENCE_CONTACT_PHONE = environment.read_string("AXISENCE_CONTACT_PHONE", default="")

# Immutable raw snapshots the pipeline writes and parses; git-ignored.
RAW_DATA_DIRECTORY = BASE_DIR / "data" / "raw"

# Generated client reports and teasers: confidential, git-ignored, and never deleted or
# overwritten by the application.
REPORT_DIRECTORY = BASE_DIR / "data" / "private" / "reports"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",  # noqa: E501
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "America/New_York"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # urllib3 logs request URLs, which carry the Census key in the query string, on
    # retries and at DEBUG level. The filter redacts keys from every record. urllib3
    # also logs the single-address geocoder's URL, whose query string holds a research
    # target address, so the second filter redacts it.
    "filters": {
        "redact_secrets": {"()": "axisence.ingestion.redaction.RedactSecretsFilter"},
        "redact_target_addresses": {
            "()": "axisence.research.redaction.RedactTargetAddressesFilter"
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["redact_secrets", "redact_target_addresses"],
        },
        "discard": {"class": "logging.NullHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": environment.read_string("DJANGO_LOG_LEVEL", default="INFO"),
    },
    # Django's default logging gives the django logger its own console handler, which
    # together with the root handler prints each Django record twice. Without handlers
    # or a level of its own, the django logger passes its records to root once, at
    # DJANGO_LOG_LEVEL. In debug mode Django's SQL logger writes every statement with
    # its parameters at DEBUG, and those parameters hold research target addresses and
    # call notes, so it stays at INFO whatever DJANGO_LOG_LEVEL says. WeasyPrint's and
    # fontTools' records can quote the HTML, CSS, or text of a report, which holds the
    # organization's name and, in client reports, research values, so they are
    # discarded.
    "loggers": {
        "django": {"handlers": [], "level": "NOTSET"},
        "django.db.backends": {"handlers": [], "level": "INFO"},
        "weasyprint": {
            "handlers": ["discard"],
            "level": "CRITICAL",
            "propagate": False,
        },
        "fontTools": {
            "handlers": ["discard"],
            "level": "CRITICAL",
            "propagate": False,
        },
    },
}
