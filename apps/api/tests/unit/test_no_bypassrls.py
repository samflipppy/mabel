from pathlib import Path

from mabel.platform import db
from mabel.shops import onboard, store


def test_app_never_enables_bypassrls() -> None:
    source = Path(db.__file__).read_text(encoding="utf-8")
    assert "SET BYPASSRLS" not in source
    assert "BYPASSRLS true" not in source.upper().replace(" ", "")
    assert "mabel_migrator" in source
    assert "mabel_app" in source


def test_onboard_never_uses_bypassrls_or_migrator() -> None:
    for module in (onboard, store):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "BYPASSRLS" not in source.upper().replace(" ", "")
        assert "mabel_migrator" not in source
        assert "SECURITY DEFINER" not in source.upper()
