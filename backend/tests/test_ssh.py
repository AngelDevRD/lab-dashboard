"""Tests para ssh_client.py — pool y backoff."""

from app.ssh_client import SSHConnection, SSHPool


class TestSSHPool:
    def test_get_or_create(self):
        pool = SSHPool()
        s1 = pool.get({"host": "10.0.0.1", "ssh_port": 22, "ssh_user": "test"})
        s2 = pool.get({"host": "10.0.0.1", "ssh_port": 22, "ssh_user": "test"})
        assert s1 is s2

    def test_close_all(self):
        pool = SSHPool()
        pool.get({"host": "10.0.0.1"})
        pool.get({"host": "10.0.0.2"})
        pool.close_all()
        # No exception = pass


class TestBackoffNeverOverflows:
    def test_backoff_stays_capped_after_thousands_of_failures(self):
        """A host that's been down for days can rack up thousands of
        consecutive failures. 2**failures used to be computed before the
        min() clamp ever ran, which raised OverflowError and broke backoff
        entirely (it kept retrying every cycle instead of backing off) —
        this reproduces that exact scenario."""
        from app import config

        conn = SSHConnection(host="10.0.0.1", port=22, username="test")
        conn._consecutive_failures = 100_000
        assert conn._backoff_seconds() == config.SSH_BACKOFF_MAX  # must not raise OverflowError

    def test_on_failure_caps_the_counter(self):
        conn = SSHConnection(host="10.0.0.1", port=22, username="test")
        conn._consecutive_failures = 10_000
        conn._on_failure(RuntimeError("boom"))
        assert conn._consecutive_failures <= 20
