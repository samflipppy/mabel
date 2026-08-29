from pathlib import Path

from mabel.leads import persist as leads_persist
from mabel.platform import db
from mabel.shops import onboard, store, update
from mabel.sms import recap_store
from mabel.voice import agents as voice_agents
from mabel.voice import archive, session


def test_app_never_enables_bypassrls() -> None:
    source = Path(db.__file__).read_text(encoding="utf-8")
    assert "SET BYPASSRLS" not in source
    assert "BYPASSRLS true" not in source.upper().replace(" ", "")
    assert "mabel_migrator" in source
    assert "mabel_app" in source


def test_onboard_never_uses_bypassrls_or_migrator() -> None:
    modules = (onboard, store, leads_persist, voice_agents, update, recap_store, archive, session)
    for module in modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "SET BYPASSRLS" not in source.upper()
        assert "BYPASSRLS TRUE" not in source.upper().replace(" ", "")
        assert "mabel_migrator" not in source
        assert "CREATE FUNCTION" not in source.upper()
        assert "SECURITY DEFINER" not in source.upper()
