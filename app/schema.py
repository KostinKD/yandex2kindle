"""Additive schema shared by optional integrations; preserves existing libraries."""


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS integration_state (
            name TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS external_wishes (
            source TEXT NOT NULL, external_id TEXT NOT NULL,
            wish_id INTEGER NOT NULL, PRIMARY KEY(source, external_id)
        );
        CREATE TABLE IF NOT EXISTS remote_candidates (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
            title TEXT NOT NULL, author TEXT NOT NULL, url TEXT NOT NULL,
            suffix TEXT NOT NULL, expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kosync_users (
            username TEXT PRIMARY KEY, salt TEXT NOT NULL, key_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kosync_progress (
            username TEXT NOT NULL, document TEXT NOT NULL, progress TEXT NOT NULL,
            percentage REAL NOT NULL, device TEXT NOT NULL, device_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL, PRIMARY KEY(username, document)
        );
        CREATE TABLE IF NOT EXISTS book_documents (
            book_id INTEGER NOT NULL, mode TEXT NOT NULL, document TEXT NOT NULL,
            PRIMARY KEY(book_id, mode)
        );
        CREATE INDEX IF NOT EXISTS book_document_lookup ON book_documents(document);
        CREATE TABLE IF NOT EXISTS wishlist_books (
            wish_id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL,
            method TEXT NOT NULL, linked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wishlist_match_exclusions (wish_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS book_device_state (
            book_id INTEGER PRIMARY KEY, kindle_confirmed_at TEXT,
            reader_seen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS external_wish_state (
            source TEXT NOT NULL, external_id TEXT NOT NULL, state TEXT NOT NULL,
            synced_at TEXT NOT NULL, PRIMARY KEY(source, external_id)
        );
        CREATE TABLE IF NOT EXISTS book_highlights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            cfi TEXT NOT NULL DEFAULT '',
            progress REAL NOT NULL DEFAULT 0,
            color TEXT NOT NULL DEFAULT 'yellow',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_highlights_book ON book_highlights(book_id);
        CREATE TABLE IF NOT EXISTS highlight_imports (
            book_id INTEGER NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
            highlight_id INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(book_id, source, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_highlight_import_id ON highlight_imports(highlight_id);
        CREATE TABLE IF NOT EXISTS reading_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            start_progress REAL NOT NULL DEFAULT 0,
            end_progress REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON reading_sessions(created_at);
        CREATE TABLE IF NOT EXISTS koreader_stat_books (
            identity TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL,
            document TEXT NOT NULL, book_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS koreader_page_stats (
            identity TEXT NOT NULL, page INTEGER NOT NULL, start_time INTEGER NOT NULL,
            duration INTEGER NOT NULL, total_pages INTEGER NOT NULL,
            PRIMARY KEY(identity, page, start_time)
        );
        CREATE INDEX IF NOT EXISTS koreader_stats_time ON koreader_page_stats(start_time);
        CREATE TABLE IF NOT EXISTS koreader_stat_snapshot (
            id INTEGER PRIMARY KEY CHECK(id=1), content BLOB NOT NULL,
            etag TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS koreader_stat_import (
            id INTEGER PRIMARY KEY CHECK(id=1), updated_at TEXT NOT NULL,
            books INTEGER NOT NULL, records INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shelves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            color TEXT NOT NULL DEFAULT '#2563eb',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS book_shelf_links (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
            PRIMARY KEY(book_id, shelf_id)
        );
        CREATE INDEX IF NOT EXISTS idx_shelf_links_book ON book_shelf_links(book_id);
    """)
    device_cols = {r[1] for r in db.execute('PRAGMA table_info(book_device_state)')}
    if 'email_sent_at' not in device_cols:
        db.execute('ALTER TABLE book_device_state ADD COLUMN email_sent_at TEXT')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='books'").fetchone():
        cols = [r[1] for r in db.execute("PRAGMA table_info(books)").fetchall()]
        if "series" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN series TEXT NOT NULL DEFAULT ''")
        if "series_index" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN series_index REAL NOT NULL DEFAULT 0")
        if "description" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "genres" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN genres TEXT NOT NULL DEFAULT ''")
        if "year" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN year INTEGER")
        if "isbn" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN isbn TEXT NOT NULL DEFAULT ''")
        if "rating" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN rating INTEGER NOT NULL DEFAULT 0")
        if "review" not in cols:
            db.execute("ALTER TABLE books ADD COLUMN review TEXT NOT NULL DEFAULT ''")
