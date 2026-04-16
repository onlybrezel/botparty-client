"""Unit tests for hardware adapter utility functions."""

import pytest
from botparty_robot.hardware.common import (
    command_matches,
    get_float,
    get_int,
    get_pin_list,
    get_str,
    normalize_command,
)


# ---------- normalize_command ----------


def test_normalize_command_lowercase():
    assert normalize_command("Forward") == "forward"


def test_normalize_command_strips_whitespace():
    assert normalize_command("  stop  ") == "stop"


def test_normalize_command_hyphen_to_underscore():
    assert normalize_command("head-up") == "head_up"


def test_normalize_command_space_to_underscore():
    assert normalize_command("head up") == "head_up"


# ---------- command_matches ----------


def test_command_matches_exact():
    assert command_matches("forward", "forward") is True


def test_command_matches_alias_f():
    assert command_matches("f", "forward") is True


def test_command_matches_alias_reverse_for_backward():
    assert command_matches("reverse", "backward") is True


def test_command_matches_stop_alias_x():
    assert command_matches("x", "stop") is True


def test_command_matches_case_insensitive():
    assert command_matches("STOP", "stop") is True


def test_command_matches_no_match():
    assert command_matches("banana", "forward", "stop") is False


def test_command_matches_multiple_names_second_matches():
    assert command_matches("left", "forward", "left") is True


# ---------- get_int ----------


def test_get_int_from_int():
    assert get_int(5, 0) == 5


def test_get_int_from_string():
    assert get_int("42", 0) == 42


def test_get_int_invalid_falls_back():
    assert get_int("nope", 7) == 7


def test_get_int_none_falls_back():
    assert get_int(None, 3) == 3


# ---------- get_float ----------


def test_get_float_from_float():
    assert get_float(1.5, 0.0) == pytest.approx(1.5)


def test_get_float_from_string():
    assert get_float("3.14", 0.0) == pytest.approx(3.14)


def test_get_float_invalid_falls_back():
    assert get_float("bad", 99.9) == pytest.approx(99.9)


def test_get_float_none_falls_back():
    assert get_float(None, 2.0) == pytest.approx(2.0)


# ---------- get_str ----------


def test_get_str_from_string():
    assert get_str("hello", "default") == "hello"


def test_get_str_from_int():
    # get_str returns default for non-string values
    assert get_str(42, "default") == "default"


def test_get_str_none_falls_back():
    assert get_str(None, "default") == "default"


def test_get_str_empty_falls_back():
    assert get_str("", "default") == "default"


# ---------- get_pin_list ----------


def test_get_pin_list_from_list():
    assert get_pin_list([17, 18, 27]) == [17, 18, 27]


def test_get_pin_list_from_string_csv():
    assert get_pin_list("17,18,27") == [17, 18, 27]


def test_get_pin_list_from_int():
    assert get_pin_list(17) == [17]


def test_get_pin_list_none_returns_empty():
    assert get_pin_list(None) == []


def test_get_pin_list_invalid_string_returns_empty():
    # non-numeric parts are silently skipped (via ValueError handling in csv path)
    assert get_pin_list("abc") == []
