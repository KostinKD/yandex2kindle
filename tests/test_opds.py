import os
import tempfile
from pathlib import Path

# Run with: python -m unittest discover -s tests
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

class Tests(unittest.TestCase):
    def test_empty_feed(self):
        with tempfile.TemporaryDirectory() as temp:
            os.environ['DATABASE_PATH'] = str(Path(temp)/'db.sqlite')
            from app import main
            with patch.object(main, 'DB', Path(temp)/'db.sqlite'):
                from core_client import TestClient
                r = TestClient(main.app).get('/opds')
                self.assertEqual(r.status_code, 200)
                self.assertEqual(ET.fromstring(r.content).tag, '{http://www.w3.org/2005/Atom}feed')

if __name__ == '__main__': unittest.main()
