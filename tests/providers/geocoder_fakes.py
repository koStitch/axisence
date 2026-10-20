"""Fake answers of the US Census Geocoder batch endpoint, from the recorded response."""

import csv
import email.parser
import email.policy
import io

from tests.fixture_files import fixture_bytes

RECORDED_RESULT = "geocoder/addressbatch-result.csv"


def uploaded_form_parts(request):
    """Return the multipart form of a geocoder request as {field name: bytes}."""
    head = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        head + request.body
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(
            decode=True
        )
        for part in message.iter_parts()
    }


def uploaded_address_rows(request):
    """Return the uploaded addressFile as CSV rows."""
    address_file = uploaded_form_parts(request)["addressFile"].decode("utf-8")
    return list(csv.reader(io.StringIO(address_file)))


def recorded_result_for(request):
    """Answer with the recorded lines, renumbered to the uploaded identifiers.

    Uploaded rows beyond the recorded ones get a No_Match line.
    """
    recorded = list(
        csv.reader(io.StringIO(fixture_bytes(RECORDED_RESULT).decode("utf-8")))
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    for index, row in enumerate(uploaded_address_rows(request)):
        identifier, *address = row
        if index < len(recorded):
            writer.writerow([identifier, *recorded[index][1:]])
        else:
            writer.writerow([identifier, ", ".join(address), "No_Match"])
    return buffer.getvalue().encode("utf-8")
