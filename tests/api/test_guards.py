"""The temporary proxy-chain diagnostic (`guards.ForwardedShape`, ADR 0065): it says the shape
of a request's headers and never writes an address."""

import re

from trialpulse.api.guards import ForwardedShape
from trialpulse.config import ApiConfig

CLIENT, EDGE_A, EDGE_B, RENDER = "203.0.113.5", "198.51.100.7", "198.51.100.8", "10.1.2.3"
ADDRESS = re.compile(r"\d+\.\d+\.\d+\.\d+|[0-9a-f]{0,4}:[0-9a-f:]+")


def _fields(line: str) -> dict[str, str]:
    assert line.startswith("forwarded shape: ")
    return dict(part.split("=", 1) for part in line.removeprefix("forwarded shape: ").split())


def test_the_line_holds_no_address_and_no_hash() -> None:
    shape = ForwardedShape()
    lines = [
        shape.describe(f"{CLIENT}, {EDGE_A}", CLIENT, None, RENDER),
        shape.describe(f"1.2.3.4, {CLIENT}, {EDGE_B}", CLIENT, CLIENT, RENDER),
    ]
    for line in lines:
        for value in (CLIENT, EDGE_A, EDGE_B, RENDER, "1.2.3.4"):
            assert value not in line
        assert not ADDRESS.search(line.replace("forwarded shape: ", ""))
        assert not re.search(r"[0-9a-f]{16,}", line)  # nothing that looks like a hash


def test_the_line_gives_the_position_of_the_cloudflare_header_and_what_stayed_the_same() -> None:
    shape = ForwardedShape()
    first = _fields(shape.describe(f"{CLIENT}, {EDGE_A}", CLIENT, None, RENDER))
    assert first["xff_entries"] == "2"
    assert first["cf_connecting_ip"] == "yes"
    assert first["true_client_ip"] == "no"
    assert first["cf_position_from_end"] == "2"
    assert first["cf_position_same_as_previous"] == "-"  # no request before
    second = _fields(shape.describe(f"1.2.3.4, {CLIENT}, {EDGE_B}", CLIENT, CLIENT, RENDER))
    assert second["xff_entries"] == "3"
    assert second["true_client_ip"] == "yes"
    assert second["cf_position_from_end"] == "2"
    assert second["cf_position_same_as_previous"] == "yes"
    assert second["cf_same_as_previous"] == "yes"
    assert second["peer_same_as_previous"] == "yes"
    # the last entry (the edge) changed, the one before it (the client) did not
    assert second["xff_same_as_previous_from_end"] == "1:no,2:yes,3:-,4:-"


def test_a_missing_cloudflare_header_has_no_position() -> None:
    line = _fields(ForwardedShape().describe(CLIENT, None, None, None))
    assert line["cf_connecting_ip"] == "no"
    assert line["cf_position_from_end"] == "none"


def test_the_diagnostic_is_off_unless_the_project_file_turns_it_on() -> None:
    assert ApiConfig.model_fields["forwarded_diagnostic"].default is False
