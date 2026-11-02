import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from app import main, auth


class EasySetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main, name, self.root / path) for name, path in
                        [('DB', 'app.db'), ('BASE', 'calibre'), ('INCOMING', 'incoming')]]
        for p in self.patches:
            p.start()
        (self.root / 'setup.key').write_text('local-bootstrap-key')
        self.client = TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def setup_owner(self):
        return self.client.post('/setup', data={'bootstrap_token': 'local-bootstrap-key', 'username': 'owner',
                                 'password': 'owner-password-123', 'password_confirm': 'owner-password-123'}, follow_redirects=False)

    def test_setup_requires_local_key_and_creates_logged_in_owner_once(self):
        self.assertEqual(self.client.get('/setup').status_code, 200)
        invalid = self.client.post('/setup', data={'bootstrap_token': 'wrong', 'username': 'owner',
                                   'password': 'owner-password-123', 'password_confirm': 'owner-password-123'})
        self.assertEqual(invalid.status_code, 403)
        response = self.setup_owner()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/settings')
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertEqual(self.client.get('/settings').status_code, 200)
        self.assertFalse((self.root / 'setup.key').exists())
        self.assertEqual(self.setup_owner().status_code, 409)
        self.assertEqual(self.client.get('/setup', follow_redirects=False).status_code, 303)

    def test_setup_rejects_cross_site_and_mismatched_passwords(self):
        values = {'bootstrap_token': 'local-bootstrap-key', 'username': 'owner', 'password': 'owner-password-123',
                  'password_confirm': 'different-password-123'}
        self.assertEqual(self.client.post('/setup', data=values).status_code, 422)
        values['password_confirm'] = values['password']
        self.assertEqual(self.client.post('/setup', data=values, headers={'origin': 'https://other.example'}).status_code, 403)

    def test_web_settings_take_effect_without_exposing_saved_secret(self):
        self.setup_owner()
        response = self.client.post('/settings/yandex', data={'YANDEX_BOOK_TOKEN': 'private-web-token', 'YANDEX_SYNC_INTERVAL_SECONDS': '0'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(self.client.get('/api/integrations').json()['yandex_configured'])
        for url in ['/settings', '/api/settings', '/integrations', '/']:
            self.assertNotIn('private-web-token', self.client.get(url).text)
        from app import configuration
        self.assertEqual(configuration.get('YANDEX_BOOK_TOKEN'), 'private-web-token')
        self.client.post('/settings/yandex', data={'YANDEX_BOOK_TOKEN': '', 'YANDEX_SYNC_INTERVAL_SECONDS': '0'})
        self.assertEqual(configuration.get('YANDEX_BOOK_TOKEN'), 'private-web-token')
        with patch.dict(os.environ, {'YANDEX_BOOK_TOKEN': 'legacy-secret'}):
            self.client.post('/settings/yandex/disable')
            self.assertEqual(configuration.get('YANDEX_BOOK_TOKEN'), '')

    def test_only_owner_can_view_or_change_server_configuration(self):
        self.setup_owner()
        auth.create_account('alice', 'alice-password-123')
        other = TestClient(main.app)
        other.post('/login', data={'username': 'alice', 'password': 'alice-password-123'})
        for path in ['/settings', '/api/settings']:
            self.assertEqual(other.get(path).status_code, 403)
        for path in ['/settings/yandex', '/settings/yandex/disable', '/settings/check/yandex']:
            self.assertEqual(other.post(path, data={'YANDEX_BOOK_TOKEN': 'stolen'}).status_code, 403)

    def test_smtp_settings_validate_and_are_used_by_delivery(self):
        self.setup_owner()
        values = {'SMTP_HOST': 'smtp.example.test', 'SMTP_PORT': '587', 'SMTP_USER': 'reader@example.test',
                  'SMTP_PASSWORD': 'private-smtp-secret', 'SMTP_FROM': 'reader@example.test', 'SMTP_SSL': 'false'}
        self.assertEqual(self.client.post('/settings/smtp', data={**values, 'SMTP_PORT': '0'}).status_code, 422)
        self.assertEqual(self.client.post('/settings/smtp', data=values, follow_redirects=False).status_code, 303)
        from app import smtp_delivery
        self.assertTrue(smtp_delivery.is_smtp_configured())
        self.assertNotIn('private-smtp-secret', self.client.get('/api/settings').text)
        self.assertNotIn('private-smtp-secret', self.client.get('/settings').text)

    def test_env_remains_a_fallback(self):
        self.setup_owner()
        from app import configuration
        with patch.dict(os.environ, {'YANDEX_BOOK_TOKEN': 'existing-env-token'}):
            self.assertEqual(configuration.get('YANDEX_BOOK_TOKEN'), 'existing-env-token')
        self.assertEqual(self.client.get('/settings/check/yandex').status_code, 405)

    def test_read_during_first_configuration_file_creation_uses_env_fallback(self):
        self.setup_owner()
        from app import configuration
        configuration.path().touch()
        with patch.dict(os.environ, {'YANDEX_BOOK_TOKEN':'existing-env-token'}):
            self.assertEqual(configuration.get('YANDEX_BOOK_TOKEN'), 'existing-env-token')

    def test_yandex_assisted_signin_validates_state_without_echoing_token(self):
        from urllib.parse import parse_qs, urlsplit
        from app.yandex_auth import CALLBACK_HOST
        from app import configuration
        self.setup_owner()
        response = self.client.get('/settings/yandex/connect', follow_redirects=False)
        state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        callback = f'https://{CALLBACK_HOST}/#access_token=private-oauth-token&state={state}'
        bad = self.client.post('/settings/yandex/finish', data={'callback': callback.replace(state, 'wrong')})
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn('private-oauth-token', bad.text)
        good = self.client.post('/settings/yandex/finish', data={'callback': callback}, follow_redirects=False)
        self.assertEqual(good.status_code, 303)
        self.assertEqual(configuration.get('YANDEX_BOOK_TOKEN'), 'private-oauth-token')
        self.assertEqual(self.client.post('/settings/yandex/finish', data={'callback': callback}).status_code, 422)

    def test_smtp_check_does_not_send_mail_or_expose_authentication_error(self):
        import smtplib
        self.setup_owner()
        self.client.post('/settings/smtp', data={'SMTP_HOST':'smtp.test','SMTP_PORT':'587','SMTP_USER':'reader',
                         'SMTP_PASSWORD':'private-smtp-secret','SMTP_FROM':'reader@example.test','SMTP_SSL':'false'})
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def starttls(self): pass
            def login(self, *args): pass
            def send_message(self, *args): raise AssertionError('A connection check must not send mail')
        with patch('smtplib.SMTP', return_value=Connection()):
            self.assertEqual(self.client.post('/settings/check/smtp', follow_redirects=False).status_code, 303)
        self.assertTrue(self.client.get('/api/settings').json()['checks']['smtp']['ok'])
        with patch('smtplib.SMTP', side_effect=smtplib.SMTPAuthenticationError(535, b'private-smtp-secret')):
            self.client.post('/settings/check/smtp')
        response = self.client.get('/api/settings')
        self.assertFalse(response.json()['checks']['smtp']['ok'])
        self.assertNotIn('private-smtp-secret', response.text)

    def test_enabling_yandex_wakes_previously_disabled_scheduler(self):
        import asyncio
        from app import configuration, integrations
        self.setup_owner()
        async def scenario():
            loop = asyncio.get_running_loop()
            synced = asyncio.Event()
            def sync():
                loop.call_soon_threadsafe(synced.set)
                return {'status':'ok'}
            configuration.save('yandex', {'YANDEX_SYNC_INTERVAL_SECONDS':'0'})
            with patch.object(integrations, 'sync_yandex', side_effect=sync):
                task = asyncio.create_task(integrations.scheduled_sync())
                await asyncio.sleep(0)
                configuration.save('yandex', {'YANDEX_SYNC_INTERVAL_SECONDS':'3600','YANDEX_BOOK_TOKEN':'new-token'})
                try:
                    await asyncio.wait_for(synced.wait(), timeout=2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
        asyncio.run(scenario())
