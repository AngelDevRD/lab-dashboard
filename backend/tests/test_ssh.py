"""Tests para ssh_client.py — pool y backoff."""

from app.ssh_client import SSHPool


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
