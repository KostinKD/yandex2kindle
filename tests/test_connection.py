import os
import unittest
from unittest.mock import patch

from core_client import TestClient
from app import main


class ConnectionTests(unittest.TestCase):
    def test_configured_host_is_used_instead_of_container(self):
        with patch.dict(os.environ, {"APP_HOST_IP": "192.168.1.25", "APP_HOST_PORT": "8000"}):
            response = TestClient(main.app).get("/api/connection")
        self.assertEqual(response.json(), {"host_ip": "192.168.1.25",
            "opds_url": "http://192.168.1.25:8000/opds", "sync_url": "http://192.168.1.25:8000"})

    def test_missing_host_in_container_does_not_invent_an_address(self):
        with patch.dict(os.environ, {"APP_HOST_IP": "", "APP_HOST_PORT": "8000"}):
            with patch.object(main.ui.Path, "exists", return_value=True):
                result = TestClient(main.app).get("/api/connection").json()
        self.assertIsNone(result["host_ip"])
        self.assertIsNone(result["opds_url"])

    def test_ipv6_host_is_formatted_as_url(self):
        with patch.dict(os.environ, {"APP_HOST_IP": "fd00::25", "APP_HOST_PORT": "8000"}):
            result = TestClient(main.app).get("/api/connection").json()
        self.assertEqual(result["opds_url"], "http://[fd00::25]:8000/opds")
