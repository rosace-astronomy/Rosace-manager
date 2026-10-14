"""
Tests for the file-name rule (file_names.py).
Run: pytest test_file_names.py -v
"""

import pytest

from rosace.file_names import safe_name


@pytest.mark.parametrize("name, expected", [
    ("*   8 Lac A", "8_Lac_A"),
    ("* bet Mon A", "bet_Mon_A"),
    ("HD  45995", "HD_45995"),
    ("V* SS Cyg", "V_SS_Cyg"),
    ("V/380 Cyg", "V380_Cyg"),
    ("alf Lyr / Vega", "alf_Lyr_Vega"),
    ("16 Peg", "16_Peg"),
    ("Vega", "Vega"),
    ("St-Pancrasse", "St-Pancrasse"),
    ("already_safe-name", "already_safe-name"),
    ("  padded  ", "padded"),
])
def test_safe_name(name, expected):
    assert safe_name(name) == expected


@pytest.mark.parametrize("name", ["***", "", "   ", "* / *"])
def test_nothing_usable_left(name):
    assert safe_name(name) == ""
