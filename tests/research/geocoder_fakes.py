"""Single-address geocoder responses: the recorded one, and synthetic ones."""

import json

RECORDED_RESPONSE = "geocoder/onelineaddress-borough-hall.json"


def one_line_address_body(matches):
    """Return a response body listing (matched address, longitude, latitude) matches."""
    return json.dumps(
        {
            "result": {
                "input": {
                    "address": {"address": "Synthetic input"},
                    "benchmark": {"benchmarkName": "Public_AR_Current"},
                },
                "addressMatches": [
                    {
                        "matchedAddress": matched_address,
                        "coordinates": {"x": longitude, "y": latitude},
                    }
                    for matched_address, longitude, latitude in matches
                ],
            }
        }
    ).encode("utf-8")
