from app import config
from app.ui import layout


def test_theme_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.theme() == "auto" and config.dark_value("auto") is None
    config.save_local(theme="dark")
    assert config.theme() == "dark" and config.dark_value("dark") is True
    config.save_local(theme="nonsense")
    assert config.theme() == "auto"
    assert config.dark_value("light") is False


def test_head_html_matches_theme():
    assert "color-scheme: dark;" in layout.head_html("dark") and "#0f1117" in layout.head_html("dark")
    assert "color-scheme: light;" in layout.head_html("light") and "#0f1117" not in layout.head_html("light")
    assert "prefers-color-scheme: dark" in layout.head_html("auto")
