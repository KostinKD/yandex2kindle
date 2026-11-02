import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from unittest.mock import MagicMock, patch

from core_client import TestClient
from app import main, smtp_delivery, auth


class SmtpDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.epub_file = self.root / "sample.epub"
        self.epub_file.write_bytes(b"PK\x03\x04sample epub data")
        with main.connect() as db:
            db.execute("INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (1, "Тестовая книга", "Автор Тест", str(self.epub_file), "dummy_sha", "2026-01-01T00:00:00Z"))
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_smtp_status_unconfigured(self):
        with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_FROM": ""}, clear=True):
            response = self.client.get("/api/smtp/status")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["configured"])

    def test_send_to_kindle_unconfigured_smtp_returns_503(self):
        with patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_FROM": ""}, clear=True):
            response = self.client.post("/api/books/1/send-to-kindle", json={"email": "reader@kindle.com"})
            self.assertEqual(response.status_code, 503)
            self.assertIn("SMTP_HOST", response.json()["detail"])

    def test_send_to_kindle_missing_recipient_returns_400(self):
        env = {"SMTP_HOST": "smtp.test.com", "SMTP_FROM": "bot@test.com", "KINDLE_EMAIL": ""}
        with patch.dict(os.environ, env, clear=True):
            response = self.client.post("/api/books/1/send-to-kindle", json={})
            self.assertEqual(response.status_code, 400)
            self.assertIn("Kindle", response.json()["detail"])

    def test_send_to_kindle_invalid_email_returns_422(self):
        env = {"SMTP_HOST": "smtp.test.com", "SMTP_FROM": "bot@test.com"}
        with patch.dict(os.environ, env, clear=True):
            response = self.client.post("/api/books/1/send-to-kindle", json={"email": "not-an-email"})
            self.assertEqual(response.status_code, 422)

    def test_send_to_kindle_missing_book_returns_404(self):
        env = {"SMTP_HOST": "smtp.test.com", "SMTP_FROM": "bot@test.com", "KINDLE_EMAIL": "reader@kindle.com"}
        with patch.dict(os.environ, env, clear=True):
            response = self.client.post("/api/books/999/send-to-kindle")
            self.assertEqual(response.status_code, 404)

    def test_send_to_kindle_success(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "myuser",
            "SMTP_PASSWORD": "mypassword",
            "SMTP_FROM": "sender@example.com",
            "SMTP_TLS": "true",
            "KINDLE_EMAIL": "mydevice@kindle.com",
        }
        mock_smtp_instance = MagicMock()
        with patch.dict(os.environ, env, clear=True):
            with patch("smtplib.SMTP", return_value=mock_smtp_instance) as mock_smtp_cls:
                mock_smtp_instance.__enter__.return_value = mock_smtp_instance
                response = self.client.post("/api/books/1/send-to-kindle", json={"email": "mydevice@kindle.com"})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "sent")
                self.assertEqual(data["recipient"], "mydevice@kindle.com")

                mock_smtp_cls.assert_called_with("smtp.example.com", 587, timeout=60)
                mock_smtp_instance.starttls.assert_called_once()
                mock_smtp_instance.login.assert_called_with("myuser", "mypassword")
                self.assertEqual(mock_smtp_instance.send_message.call_count, 1)

                sent_msg = mock_smtp_instance.send_message.call_args[0][0]
                self.assertEqual(sent_msg["To"], "mydevice@kindle.com")
                self.assertEqual(sent_msg["From"], "sender@example.com")
                self.assertEqual(sent_msg["Subject"], "Тестовая книга")
                # Check attachment
                payloads = sent_msg.get_payload()
                self.assertTrue(len(payloads) >= 2)
                attachment = payloads[1]
                self.assertEqual(attachment.get_content_type(), "application/epub+zip")
                self.assertTrue(attachment.get_filename().endswith(".epub"))

        # SMTP acceptance is not a confirmation that the device has the book.
        with main.connect() as db:
            state = db.execute("SELECT * FROM book_device_state WHERE book_id=1").fetchone()
            self.assertIsNone(state["kindle_confirmed_at"])
            self.assertIsNotNone(state["email_sent_at"])

    def test_send_to_kindle_form_post_redirects(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_FROM": "sender@example.com",
            "KINDLE_EMAIL": "mydevice@kindle.com",
        }
        mock_smtp = MagicMock()
        with patch.dict(os.environ, env, clear=True):
            with patch("smtplib.SMTP", return_value=mock_smtp):
                mock_smtp.__enter__.return_value = mock_smtp
                response = self.client.post("/books/1/send-to-kindle", data={"email": "mydevice@kindle.com", "next_path": "/"}, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/")

    def test_send_to_kindle_file_too_large_returns_413(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_FROM": "sender@example.com",
            "KINDLE_EMAIL": "mydevice@kindle.com",
        }
        fake_path = MagicMock()
        fake_path.stat.return_value.st_size = 55 * 1024 * 1024  # 55 MB
        with patch.dict(os.environ, env, clear=True):
            with patch("app.smtp_delivery.ensure_epub_file", return_value=(fake_path, "large.epub")):
                response = self.client.post("/api/books/1/send-to-kindle")
                self.assertEqual(response.status_code, 413)
                self.assertIn("50", response.json()["detail"])
