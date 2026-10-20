"""Build the data the call sheet posts, as a browser would send it."""


def call_sheet_data(client, url, **changes):
    """Return the call sheet's current values with the given changes applied.

    Every pre-filled slot keeps its initial Not covered. Unticked checkboxes are left
    out, as a browser leaves them out; a change of None removes a value.
    """
    sheet = client.get(url).context["sheet"]
    forms = [sheet.contact_form, sheet.retirement_form]
    for definition_section in sheet.definition_sections:
        forms.extend(definition_section.new_forms)
    for value_section in sheet.value_sections:
        forms.extend(value_section.slot_forms)
        forms.extend(value_section.new_forms)
    data = {}
    for form in forms:
        for name in form.fields:
            value = form[name].value()
            if value not in (None, False, []):
                data[form.add_prefix(name)] = value
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}
