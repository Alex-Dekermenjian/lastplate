"""Keeps the Web unit tests independent of whichever services happen to be running.

Inside Docker the real Marketplace is reachable, so a test that forgot to mock one
service call would quietly talk to it and could pass or fail depending on what is
running. Here every outbound HTTP connection fails as if the services were down, so
the suite gives the same result everywhere. Real integration checks are separate:
scripts/sprint1_flow_check.py.
"""

import socket
from unittest import mock

from django.test.runner import DiscoverRunner


def refuse_connection(address, *args, **kwargs):
    raise ConnectionRefusedError(f"Unit tests may not contact live services: {address}")


class NoLiveServicesRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # urllib connects through socket.create_connection. The PostgreSQL driver
        # does not use Python sockets, so the test database is unaffected.
        self._connection_block = mock.patch.object(socket, "create_connection", refuse_connection)
        self._connection_block.start()

    def teardown_test_environment(self, **kwargs):
        self._connection_block.stop()
        super().teardown_test_environment(**kwargs)
