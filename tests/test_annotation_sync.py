import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_client import TestClient
from app import main, kosync


class AnnotationSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, 'DB', self.root / 'app.db')
        self.patch.start()
        self.client = TestClient(main.app)
        with main.connect() as db:
            db.execute("INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(1,'Дюна','Фрэнк Герберт','/missing.epub','hash','2026-01-01')")
            db.execute("INSERT INTO book_documents VALUES(1,'binary','document123')")
            salt = '12' * 16
            db.execute('INSERT INTO kosync_users VALUES(?,?,?)', ('reader', salt, kosync.password_hash('key', salt)))
        self.headers = {'x-auth-user': 'reader', 'x-auth-key': 'key'}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def exchange(self, **changes):
        payload = {'document': 'document123', 'annotations': [{'key': 'quote1', 'text': 'Страх убивает разум.',
                   'comment': 'Заметка', 'cfi': 'epubcfi(/6/2!/4,/2/1:0,/2/1:10)', 'progress': 0.3}]}
        return self.client.post('/syncs/highlights', json={**payload, **changes}, headers=self.headers)

    def test_import_is_idempotent_and_updates_note(self):
        response = self.exchange()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['added'], 1)
        self.assertEqual(self.exchange().json()['added'], 0)
        highlights = self.client.get('/api/books/1/highlights').json()
        self.assertEqual(len(highlights), 1)
        self.assertEqual(highlights[0]['comment'], 'Заметка')
        self.assertIn('Страх убивает разум.', self.client.get('/highlights').text)
        self.exchange(annotations=[{'key': 'quote1', 'text': 'Страх убивает разум.', 'comment': 'Правка'}])
        self.assertEqual(self.client.get('/api/books/1/highlights').json()[0]['comment'], 'Правка')

    def test_deleted_quote_is_not_resurrected_by_device(self):
        self.exchange()
        item = self.client.get('/api/books/1/highlights').json()[0]
        self.client.delete(f'/api/highlights/{item["id"]}')
        self.exchange()
        self.assertEqual(self.client.get('/api/books/1/highlights').json(), [])

    def test_exchange_returns_site_quotes_without_deleting_them(self):
        self.client.post('/api/books/1/highlights', json={'text': 'Цитата из браузера'})
        response = self.exchange(annotations=[])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['highlights'][0]['text'], 'Цитата из браузера')

    def test_credentials_and_document_ambiguity_are_checked(self):
        self.assertEqual(self.client.post('/syncs/highlights', json={'document': 'document123', 'annotations': []}).status_code, 401)
        self.assertEqual(self.exchange(document='unknown').status_code, 404)
        with main.connect() as db:
            db.execute("INSERT INTO book_documents VALUES(2,'binary','document123')")
        self.assertEqual(self.exchange().status_code, 409)

    def test_native_kindle_clippings_import_and_repeat(self):
        contents = 'Дюна (Фрэнк Герберт)\n- Ваше выделение на странице 12 | Место 123-124\n\nСтрах убивает разум.\n==========\n'
        for expected in [1, 0]:
            response = self.client.post('/api/highlights/import', files={'file': ('My Clippings.txt', contents.encode())})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['added'], expected)
        self.assertEqual(len(self.client.get('/api/books/1/highlights').json()), 1)

    def test_unknown_clipping_is_reported_and_does_not_attach_to_wrong_book(self):
        contents = 'Другое произведение (Фрэнк Герберт)\n- Your Highlight at location 1-2\n\nЦитата.\n==========\n'
        response = self.client.post('/api/highlights/import', files={'file': ('My Clippings.txt', contents.encode())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['unmatched'], 1)
        self.assertEqual(self.client.get('/api/books/1/highlights').json(), [])
