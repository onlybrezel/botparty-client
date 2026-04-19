import os

from botparty_robot.client_ops import ClientOpsMixin


class _DummyOps(ClientOpsMixin):
    pass


def test_env_bool_parsing():
    ops = _DummyOps()

    os.environ["BOTPARTY_TEST_BOOL"] = "true"
    assert ops._env_bool("BOTPARTY_TEST_BOOL", False) is True

    os.environ["BOTPARTY_TEST_BOOL"] = "0"
    assert ops._env_bool("BOTPARTY_TEST_BOOL", True) is False


def test_update_remote_allowlist_default_allows_official_repo():
    ops = _DummyOps()

    os.environ.pop("BOTPARTY_CLIENT_UPDATE_ALLOWED_REMOTES", None)
    assert ops._is_allowed_update_remote("https://github.com/onlybrezel/botparty-client.git") is True
    assert ops._is_allowed_update_remote("https://example.com/other/repo.git") is False
