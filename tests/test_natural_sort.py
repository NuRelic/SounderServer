"""Natural (alphanumeric) name sorting in the board UI.

The comparator lives in templates/index.html because the grid sorts client-side.
These tests extract it from the template and exercise it under node, so the
assertions run against the exact source that ships — not a Python re-write of it.

Skipped when node isn't installed; node is a dev-only convenience, not a
runtime or deploy dependency.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "index.html",
)

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


def _extract_comparator():
    """Pull the natKey/natCmp block out of the template verbatim."""
    src = open(TEMPLATE, encoding="utf-8").read()
    m = re.search(
        r"// ---- natural sort ----\n(.*?)// ---- end natural sort ----",
        src, re.S,
    )
    assert m, "natural-sort block not found in index.html"
    return m.group(1)


def nat_sort(names):
    """Sort `names` with the shipped comparator and return the result."""
    script = _extract_comparator() + (
        "\nconst input = JSON.parse(process.argv[1]);"
        "\nprocess.stdout.write(JSON.stringify(input.slice().sort(natCmp)));"
    )
    out = subprocess.run(
        [node, "-e", script, json.dumps(names)],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_numeric_run_compares_as_number_not_text():
    # The live regression: sheik10 sorts before sheik2 under localeCompare.
    assert nat_sort(["sheik10", "sheik2", "sheik9"]) == ["sheik2", "sheik9", "sheik10"]


def test_double_digit_rollover():
    names = [f"e{i}_clip" for i in (1, 2, 9, 10, 11, 21, 33)]
    assert nat_sort(list(reversed(names))) == names


def test_zero_padded_and_bare_numbers_interleave():
    assert nat_sort(["interesting_09", "interesting_1", "interesting_10"]) == \
        ["interesting_1", "interesting_09", "interesting_10"]


def test_pure_text_still_alphabetical():
    assert nat_sort(["banana", "Apple", "cherry"]) == ["Apple", "banana", "cherry"]


def test_case_insensitive_like_the_old_sort():
    # The server pre-sorts on name.lower(); the client must not disagree.
    assert nat_sort(["Boogie2", "boogie1"]) == ["boogie1", "Boogie2"]


def test_names_with_no_digits_against_names_with_digits():
    assert nat_sort(["BF1", "Barb", "BF10", "BF2"]) == ["Barb", "BF1", "BF2", "BF10"]


def test_leading_digits():
    assert nat_sort(["2001_error", "1979", "100ways", "9000"]) == \
        ["100ways", "1979", "2001_error", "9000"]


def test_identical_names_are_stable_not_dropped():
    assert nat_sort(["dup", "dup"]) == ["dup", "dup"]


def test_digits_in_multiple_positions():
    assert nat_sort(["a2b10", "a2b2", "a10b1"]) == ["a2b2", "a2b10", "a10b1"]


def test_empty_and_symbol_names_do_not_throw():
    got = nat_sort(["", "+15127581883 (18 seconds) Voice Mail", "!bang"])
    assert len(got) == 3


def test_real_library_sample():
    # Drawn from the live library: these currently sort wrong.
    got = nat_sort(["sheik2", "sheik10", "sheik3", "sheik8", "sheik9", "sheik5", "sheik6"])
    assert got == ["sheik2", "sheik3", "sheik5", "sheik6", "sheik8", "sheik9", "sheik10"]
