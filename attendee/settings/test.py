import os

from .base import *
from .base import RECORDING_STORAGE_BACKEND, STORAGES

DEBUG = True
SITE_DOMAIN = "localhost:8000"
ALLOWED_HOSTS = []

# Keep tests away from the storage a local .env points at (e.g. local_storage/s3). Django file fields use
# in-memory storage, and direct S3 uploads use the default AWS endpoint, as in CI where tests mock them.
os.environ.pop("AWS_ENDPOINT_URL", None)
RECORDING_STORAGE_BACKEND["OPTIONS"].pop("endpoint_url", None)
STORAGES = {
    **STORAGES,
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "recordings": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "bot_debug_screenshots": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "audio_chunks": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
}

# Use the default metadata rules, even if a local .env relaxes them
REQUIRE_STRING_VALUES_IN_METADATA = True
MAX_METADATA_LENGTH = 1000

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "attendee_test",
        "USER": "attendee_test_user",
        "PASSWORD": "attendee_test_user",
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": "5432",
    }
}


# Log more stuff in development
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
