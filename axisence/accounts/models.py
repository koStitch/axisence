from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """The project's user model.

    It adds nothing to AbstractUser. It exists so that fields can be added later without
    rewriting the migration history, which switching AUTH_USER_MODEL would require.
    """
