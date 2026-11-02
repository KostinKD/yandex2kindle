import unittest
from urllib.parse import parse_qs, urlsplit

from app.yandex_auth import CALLBACK_HOST, authorization_url, extract_token


class AuthTests(unittest.TestCase):
    def test_flow(self):
        params = parse_qs(urlsplit(authorization_url("test-state")).query)
        self.assertEqual(params["state"], ["test-state"])
        callback = f"https://{CALLBACK_HOST}/#access_token=y0_example&state=test-state"
        self.assertEqual(extract_token(callback, "test-state"), "y0_example")

    def test_rejects_wrong_origin_state_and_ambiguous_tokens(self):
        base = f"https://{CALLBACK_HOST}/"
        for callback in (
            "https://example.org/#state=s&access_token=x",
            base + "#state=other&access_token=x",
            base + "#state=s&access_token=x&access_token=y",
            base + "?state=s#state=s&access_token=x",
            base + "#state=s&error=access_denied",
            base + "#state=s",
            f"https://user@{CALLBACK_HOST}/#state=s&access_token=x",
            "y0_raw_token",
        ):
            with self.subTest(callback=callback), self.assertRaises(ValueError):
                extract_token(callback, "s")
