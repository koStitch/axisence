"""Tests for the permissions of subscriptions; tests/research/test_permissions.py proves
the Researchers group's grant.
"""

import pytest
from django.contrib.auth.models import Permission

pytestmark = pytest.mark.django_db


def test_the_subscription_model_has_add_change_view_and_prepare_permissions():
    assert set(
        Permission.objects.filter(content_type__app_label="subscriptions").values_list(
            "codename", flat=True
        )
    ) == {
        "add_subscription",
        "change_subscription",
        "view_subscription",
        "prepare_repeat_job",
    }
