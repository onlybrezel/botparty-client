from botparty_robot.client_ops import ClientOpsMixin


class _DummyOps(ClientOpsMixin):
    pass


def test_legacy_git_updater_is_not_exposed():
    assert not hasattr(_DummyOps, "_build_git_pull_argv")
    assert not hasattr(_DummyOps, "_run_update_command")
