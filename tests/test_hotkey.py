import pytest

from reporttool.hotkey import parse_hotkey


def test_parse_hotkey():
    assert parse_hotkey("Ctrl+Alt+R") == (0x2 | 0x1, ord("R"))
    assert parse_hotkey("ctrl + shift + space") == (0x2 | 0x4, 0x20)
    assert parse_hotkey("Win+F9") == (0x8, 0x78)


@pytest.mark.parametrize("bad", ["", "R", "Ctrl+Foo", "Hyper+R"])
def test_parse_hotkey_invalid(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)
