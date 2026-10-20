"""Build the data an admin change page posts, as a browser would send it."""

from django.contrib.gis.geos import GEOSGeometry


def change_form_data(client, url, **changes):
    """Return the change page's current values with the given changes applied.

    A change form post must carry the main form's values and every inline formset's
    management form and initial values. Geometry values are written as EWKT.
    """
    response = client.get(url)
    data = {}
    main_form = response.context["adminform"].form
    for name in main_form.fields:
        value = main_form[name].value()
        if value is None:
            continue
        if isinstance(value, GEOSGeometry):
            value = value.ewkt
        data[main_form.add_prefix(name)] = value
    for inline_admin_formset in response.context["inline_admin_formsets"]:
        formset = inline_admin_formset.formset
        management_form = formset.management_form
        for name in management_form.fields:
            data[management_form.add_prefix(name)] = management_form[name].value()
        for form in formset.forms:
            for name in form.fields:
                value = form[name].value()
                if value is not None:
                    data[form.add_prefix(name)] = value
    data.update(changes)
    return data
