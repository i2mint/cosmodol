"""Tests for cosmodol.errors validators and decorator."""

import pytest

from cosmodol import validate_cosmos_id


def test_id_must_be_string():
    with pytest.raises(ValueError):
        validate_cosmos_id(123)


def test_id_must_be_nonempty():
    with pytest.raises(ValueError):
        validate_cosmos_id("")


def test_id_max_length():
    validate_cosmos_id("x" * 255)  # OK at boundary
    with pytest.raises(ValueError):
        validate_cosmos_id("x" * 256)


@pytest.mark.parametrize("bad_char", ["/", "\\", "?", "#"])
def test_id_forbidden_chars(bad_char):
    with pytest.raises(ValueError):
        validate_cosmos_id(f"prefix{bad_char}suffix")


def test_id_valid():
    validate_cosmos_id("plain-id_with.dot")
    validate_cosmos_id("ABC123")
    validate_cosmos_id("a")
