import pytest

from karaoke.config import ConfigError, load_config


def test_defaults():
    c = load_config({})
    assert c.host == "127.0.0.1" and c.port == 8765
    assert "127.0.0.1:8765" in c.allowed_hosts and "http://localhost:8765" in c.allowed_origins


def test_non_loopback_rejected():
    with pytest.raises(ConfigError, match="rede"):
        load_config({"KARAOKE_HOST": "0.0.0.0"})


def test_port_change_updates_allowed_hosts():
    c = load_config({"KARAOKE_PORT": "8766"}).with_port(8770)
    assert c.allowed_hosts == {"127.0.0.1:8770", "localhost:8770", "[::1]:8770"}
