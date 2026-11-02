import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from core_client import TestClient
from app import main


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.urls = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.urls.append(dict(attrs).get('href', ''))


class LibraryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main, name, self.root / path) for name, path in
                        (("DB", "app.db"), ("BASE", "calibre"), ("INCOMING", "incoming"))]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        with main.connect() as db:
            for book_id, title, author, suffix in [(1, 'Дюна', 'Герберт', 'epub'),
                                                   (2, 'Азбука', 'Автор', 'pdf'),
                                                   (3, 'Солярис', 'Лем', 'epub')]:
                db.execute('INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(?,?,?,?,?,?)',
                           (book_id, title, author, str(self.root / f'{book_id}.{suffix}'), str(book_id), '2026-01-01'))
            db.execute("INSERT INTO reading_progress VALUES(1,0.4,'epubcfi(/6/2)','2026-01-01')")
            db.execute("INSERT INTO reading_progress VALUES(3,1,'','2026-01-01')")

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_catalog_combines_filters_and_uses_allowlisted_sort(self):
        rows = self.client.get('/api/books?format=EPUB&status=reading&sort=title').json()
        self.assertEqual([r['id'] for r in rows], [1])
        rows = self.client.get('/api/books?sort=title').json()
        self.assertEqual([r['title'] for r in rows], ['Азбука', 'Дюна', 'Солярис'])
        self.assertEqual(self.client.get('/api/books?sort=title;DROP TABLE books').status_code, 422)
        self.assertEqual(self.client.get('/api/books?status=finished').json()[0]['id'], 3)
        self.assertEqual([r['id'] for r in self.client.get('/api/books?author=Лем').json()], [3])

    def test_pagination_preserves_shelf_filters_and_list_view(self):
        with main.connect() as db:
            db.execute("INSERT INTO shelves VALUES(1,'Избранное','#a64a31','2026-01-01')")
            for i in range(4, 60):
                db.execute('INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(?,?,?,?,?,?)',
                           (i, f'Роман {i}', 'Автор', f'{i}.epub', str(i), '2026-01-01'))
                db.execute('INSERT INTO book_shelf_links VALUES(?,1)', (i,))
        page = self.client.get('/?shelf=1&format=EPUB&status=unread&sort=title&view=list').text
        next_links = [parse_qs(urlsplit(url).query) for url in Links(page).urls
                      if parse_qs(urlsplit(url).query).get('page') == ['2']]
        self.assertEqual(len(next_links), 1)
        self.assertEqual(next_links[0], {'shelf': ['1'], 'format': ['EPUB'], 'status': ['unread'],
                                        'sort': ['title'], 'view': ['list'], 'page': ['2']})

    def test_metadata_edit_normalizes_validates_and_preserves_original(self):
        response = self.client.patch('/api/books/1', json={'title': '  Пустыня  ', 'series': 'Дюна',
                                                         'series_index': 2, 'description': '<script>unsafe</script>'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['title'], 'Пустыня')
        self.assertEqual(response.json()['author'], 'Герберт')
        self.assertEqual(response.json()['sha256'], '1')
        self.assertEqual(self.client.patch('/api/books/1', json={'title': '  '}).status_code, 422)
        self.assertEqual(self.client.patch('/api/books/1', json={'filename': '/etc/passwd'}).status_code, 422)
        self.assertEqual(self.client.patch('/api/books/999', json={'title': 'Нет'}).status_code, 404)
        page = self.client.get('/books/1')
        self.assertEqual(page.status_code, 200)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;', page.text)
        self.assertNotIn('<script>unsafe</script>', page.text)
        self.assertEqual(self.client.get('/books/999').status_code, 404)

    def test_first_shelf_and_membership_are_reachable(self):
        page = self.client.get('/').text
        self.assertIn('action="/shelves/create"', page)
        shelf = self.client.post('/api/shelves', json={'name': 'Избранное'}).json()['id']
        page = self.client.get('/books/1').text
        self.assertIn('action="/books/1/shelf"', page)
        self.client.post('/books/1/shelf', data={'shelf_id': shelf, 'action': 'add'})
        rows = self.client.get(f'/api/books?shelf={shelf}').json()
        self.assertEqual([r['id'] for r in rows], [1])

    def test_dashboard_and_wishlist_are_separate_workspaces(self):
        self.client.post('/wishlist', data={'title': 'Хочу найти'})
        dashboard = self.client.get('/dashboard')
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('Дюна', dashboard.text)
        self.assertIn('Хочу найти', self.client.get('/wishlist').text)
        self.assertNotIn('Хочу найти', self.client.get('/').text)
        self.assertEqual(self.client.get('/devices').status_code, 200)

    def test_metadata_form_redirects_to_book(self):
        response = self.client.post('/books/1/metadata', data={'title': 'Новое название', 'author': 'Герберт'},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/books/1')
        self.assertEqual(self.client.get('/api/books?q=Новое').json()[0]['id'], 1)

    def test_web_and_audio_positions_do_not_overwrite_koreader_position(self):
        from app import kosync
        with main.connect() as db:
            db.execute("INSERT INTO kosync_users VALUES('reader','salt','hash')")
            db.execute("INSERT INTO book_documents VALUES(1,'binary','document1')")
            db.execute("INSERT INTO kosync_progress VALUES('reader','document1','/body/p[7]',0.4,'Kindle','device',1)")
        for position in ['epubcfi(/6/4)', '123', 'chapter:3']:
            self.client.put('/api/books/1/progress', json={'progress': 0.5, 'position': position})
            with main.connect() as db:
                remote = db.execute('SELECT progress FROM kosync_progress').fetchone()[0]
                self.assertEqual(remote, '/body/p[7]')
        self.client.put('/api/books/1/progress', json={'progress': 0.6, 'position': '/body/p[8]'})
        with main.connect() as db:
            self.assertEqual(db.execute('SELECT progress FROM kosync_progress').fetchone()[0], '/body/p[8]')

    def test_year_can_be_cleared_without_changing_other_metadata(self):
        self.client.patch('/api/books/1', json={'year': 1965})
        response = self.client.patch('/api/books/1', json={'year': None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['year'])

    def test_metadata_change_rechecks_automatic_wishlist_links_and_preserves_manual(self):
        self.client.post('/wishlist', data={'title': 'Дюна', 'author': 'Герберт'})
        self.client.post('/wishlist', data={'title': 'Особое издание', 'author': 'Герберт'})
        wishes = self.client.get('/api/wishlist').json()
        manual = next(w for w in wishes if w['title'] == 'Особое издание')
        self.client.put(f'/api/wishlist/{manual["id"]}/book', json={'book_id': 1})
        self.client.patch('/api/books/1', json={'title': 'Другое произведение'})
        wishes = self.client.get('/api/wishlist').json()
        self.assertIsNone(next(w for w in wishes if w['title'] == 'Дюна')['book_id'])
        self.assertEqual(next(w for w in wishes if w['id'] == manual['id'])['book_id'], 1)

    def test_empty_shelf_name_is_rejected(self):
        self.assertEqual(self.client.post('/api/shelves', json={'name': '  '}).status_code, 422)
        self.assertEqual(self.client.post('/shelves/create', data={'name': '  '}).status_code, 422)

    def test_shelf_delete_is_reachable_and_keeps_books_and_progress(self):
        shelf = self.client.post('/api/shelves', json={'name': 'Удаляемая полка'}).json()['id']
        self.client.post(f'/api/shelves/{shelf}/books/1')
        page = self.client.get(f'/?shelf={shelf}').text
        self.assertIn(f'action="/shelves/{shelf}/delete"', page)
        response = self.client.post(f'/shelves/{shelf}/delete', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get('/api/shelves').json(), [])
        self.assertEqual(len(self.client.get('/api/books').json()), 3)
        self.assertEqual(self.client.get('/api/books/1/progress').json()['progress'], 0.4)
