from pathlib import Path

from mabel.platform import db


def test_app_never_enables_bypassrls() -> None:
    source = Path(db.__file__).read_text(encoding="utf-8")
    assert "SET BYPASSRLS" not in source
    assert "BYPASSRLS true" not in source.upper().replace(" ", "")
    assert "mabel_migrator" in source
    assert "mabel_app" in source
