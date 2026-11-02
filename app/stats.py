"""Reading statistics, streaks, and reading session analytics."""
from datetime import datetime, timezone, timedelta
import html
from typing import Optional
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter()


class ReadingSessionCreate(BaseModel):
    book_id: int
    duration_seconds: int = Field(default=300, ge=1, le=86400)
    start_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    end_progress: float = Field(default=0.0, ge=0.0, le=1.0)


def esc(value):
    return html.escape(str(value or ""), quote=True)


def record_reading_activity(db, book_id: int, new_progress: float):
    """Auto-records a reading session when progress increases."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    # Find last recorded session for this book
    last = db.execute(
        "SELECT * FROM reading_sessions WHERE book_id=? ORDER BY id DESC LIMIT 1",
        (book_id,)
    ).fetchone()

    # If last session was within last 30 minutes, update it; otherwise insert new session
    if last:
        try:
            last_dt = datetime.fromisoformat(last["created_at"])
            delta = (now - last_dt).total_seconds()
            if delta < 1800:
                duration = min(7200, last["duration_seconds"] + int(max(30, delta)))
                db.execute(
                    "UPDATE reading_sessions SET duration_seconds=?, end_progress=?, created_at=? WHERE id=?",
                    (duration, new_progress, now_iso, last["id"])
                )
                db.commit()
                return
        except Exception:
            pass

    start_prog = max(0.0, new_progress - 0.05)
    db.execute(
        """INSERT INTO reading_sessions(book_id, duration_seconds, start_progress, end_progress, created_at)
           VALUES(?, ?, ?, ?, ?)""",
        (book_id, 300, start_prog, new_progress, now_iso)
    )
    db.commit()


def reading_zone():
    try:
        return ZoneInfo(os.environ.get("READING_TIMEZONE", "Europe/Moscow"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def calculate_streak(active_dates):
    """Calculates continuous reading streak in days."""
    if not active_dates:
        return 0
    today = datetime.now(reading_zone()).date()
    yesterday = today - timedelta(days=1)
    
    date_set = set(active_dates)
    if today not in date_set and yesterday not in date_set:
        return 0

    current = today if today in date_set else yesterday
    streak = 0
    while current in date_set:
        streak += 1
        current -= timedelta(days=1)
    return streak


@router.post("/api/reading-sessions")
def create_session(payload: ReadingSessionCreate):
    from . import main
    with main.connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (payload.book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Книга не найдена")
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = db.execute(
            """INSERT INTO reading_sessions(book_id, duration_seconds, start_progress, end_progress, created_at)
               VALUES(?, ?, ?, ?, ?)""",
            (payload.book_id, payload.duration_seconds, payload.start_progress, payload.end_progress, now_iso)
        )
        db.commit()
        return {"status": "ok", "id": cursor.lastrowid}


@router.get("/api/stats")
def get_stats():
    from . import main
    with main.connect() as db:
        total_books = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        
        # Reading progress counts
        progress_rows = db.execute("SELECT book_id, progress FROM reading_progress").fetchall()
        finished_count = sum(1 for r in progress_rows if r["progress"] >= 1.0)
        reading_count = sum(1 for r in progress_rows if 0 < r["progress"] < 1.0)
        unread_count = max(0, total_books - finished_count - reading_count)

        # Sessions
        sessions = db.execute("SELECT * FROM reading_sessions ORDER BY created_at DESC").fetchall()
        history = reading_history(db, sessions)
        total_seconds = history["estimated_reading_seconds"] + history["koreader_reading_seconds"]
        total_minutes = round(total_seconds / 60)

        # Dates for streak
        dates = [datetime.fromisoformat(item["date"]).date() for item in history["daily_activity"]]
        streak = calculate_streak(dates)

        return {
            "total_books": total_books,
            "finished_books": finished_count,
            "reading_books": reading_count,
            "unread_books": unread_count,
            "total_reading_minutes": total_minutes,
            "reading_streak_days": streak,
            "total_sessions": len(sessions) + history.pop("koreader_sessions"),
            **history,
        }


def reading_history(db, web_sessions):
    """Keep measured page events separate from estimated web sessions."""
    from .koreader_stats import match_books
    match_books(db)
    zone = reading_zone()
    daily = {}

    def add_time(stamp, seconds, source):
        cursor = datetime.fromtimestamp(stamp, timezone.utc)
        end = stamp + seconds
        while cursor.timestamp() < end:
            local = cursor.astimezone(zone)
            tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
            stop = min(end, tomorrow.timestamp())
            date = local.date().isoformat()
            item = daily.setdefault(date, {"date": date, "koreader_seconds": 0, "estimated_seconds": 0})
            item[source] += int(stop - cursor.timestamp())
            cursor = datetime.fromtimestamp(stop, timezone.utc)

    estimated = 0
    for row in web_sessions:
        try:
            dt = datetime.fromisoformat(row["created_at"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            duration = max(0, row["duration_seconds"])
            # Existing sessions store the last update time, not their start.
            add_time(int(dt.timestamp()) - duration, duration, "estimated_seconds")
            estimated += duration
        except (ValueError, OverflowError):
            continue

    books = {row["identity"]: {"title": row["title"], "author": row["author"], "book_id": row["book_id"],
                              "reading_seconds": 0, "pages_read": 0, "source": "koreader"}
             for row in db.execute("SELECT * FROM koreader_stat_books")}
    pages = {}
    sessions = []
    current = {}
    total = 0
    for row in db.execute("SELECT * FROM koreader_page_stats ORDER BY start_time,identity,page"):
        identity, start, duration = row["identity"], row["start_time"], row["duration"]
        if identity not in books:
            continue
        book = books[identity]
        total += duration
        book["reading_seconds"] += duration
        # Include total_pages: reflowed layouts can reuse page numbers.
        pages.setdefault(identity, set()).add((row["total_pages"], row["page"]))
        add_time(start, duration, "koreader_seconds")
        last = current.get(identity)
        if last is None or start - last["end"] > 1800:
            last = {"title": book["title"], "start": start, "end": start + duration,
                    "duration_seconds": 0, "source": "koreader"}
            current[identity] = last
            sessions.append(last)
        last["end"] = max(last["end"], start + duration)
        last["duration_seconds"] += duration
    for identity, book in books.items():
        book["pages_read"] = len(pages.get(identity, ()))
    imported = db.execute("SELECT updated_at,books,records FROM koreader_stat_import WHERE id=1").fetchone()
    return {
        "timezone": str(zone), "koreader_reading_seconds": total,
        "estimated_reading_seconds": estimated, "koreader_books": len(books),
        "koreader_sessions": len(sessions),
        "daily_activity": [daily[key] for key in sorted(daily)],
        "book_statistics": sorted(books.values(), key=lambda book: book["reading_seconds"], reverse=True),
        "recent_koreader_sessions": sorted(sessions, key=lambda session: session["start"], reverse=True)[:8],
        "koreader_import": dict(imported) if imported else None,
    }


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    from . import main, ui
    data = get_stats()

    with main.connect() as db:
        # Active books with progress
        active_books = db.execute("""
            SELECT b.id, b.title, b.author, b.series, p.progress, p.updated_at
            FROM reading_progress p
            JOIN books b ON b.id = p.book_id
            WHERE p.progress > 0
            ORDER BY p.updated_at DESC LIMIT 10
        """).fetchall()

        # Recent sessions
        recent_sessions = db.execute("""
            SELECT s.*, b.title, b.author
            FROM reading_sessions s
            JOIN books b ON b.id = s.book_id
            ORDER BY s.id DESC LIMIT 8
        """).fetchall()

    streak_badge = f"{data['reading_streak_days']} дн." if data['reading_streak_days'] > 0 else "0 дн."
    hours = data['total_reading_minutes'] // 60
    mins = data['total_reading_minutes'] % 60
    time_str = f"{hours} ч {mins} мин" if hours > 0 else f"{mins} мин"

    active_items = []
    for b in active_books:
        pct = round(b["progress"] * 100, 1)
        active_items.append(f"""
        <div class="stat-book-row">
          <div class="stat-book-info">
            <strong><a href="/reader/{b['id']}" target="_blank">{esc(b['title'])}</a></strong>
            <span class="muted">{esc(b['author'] or 'Неизвестный автор')}</span>
          </div>
          <div class="stat-meter">
            <progress max="100" value="{pct}"></progress>
            <span>{pct}%</span>
          </div>
        </div>
        """)

    session_items = []
    for s in recent_sessions:
        duration_m = max(1, s["duration_seconds"] // 60)
        stamp = datetime.fromisoformat(s["created_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        date_str = stamp.astimezone(reading_zone()).strftime("%d.%m.%Y %H:%M")
        session_items.append((stamp.timestamp(), f"""
        <li class="session-item">
          <div class="session-main">
            <strong>{esc(s['title'])}</strong>
            <span class="muted">≈ {duration_m} мин · {date_str} · веб-читалка</span>
          </div>
          <span class="meta-tag">+{round((s['end_progress'] - s['start_progress']) * 100, 1)}%</span>
        </li>
        """))

    dav_base = ui.connection_info(request).get("sync_url") or str(request.base_url).rstrip("/")
    dav_url = dav_base + "/webdav/"
    from .auth import current_user
    account = current_user.get()
    username = account["username"] if account else "логин сайта"
    import_state = data["koreader_import"]
    import_text = ("Последний импорт: " + esc(datetime.fromisoformat(import_state["updated_at"]).astimezone(reading_zone()).strftime("%d.%m.%Y %H:%M"))
                   + f" · книг в базе: {import_state['books']} · записей: {import_state['records']}") if import_state else "База KOReader ещё не загружена."
    imported_notice = '<p role="status">Статистика KOReader импортирована.</p>' if request.query_params.get("imported") == "1" else ''
    history_items = []
    for book in data["book_statistics"]:
        name = esc(book["title"])
        if book["book_id"]:
            name = f'<a href="/reader/{book["book_id"]}">{name}</a>'
        history_items.append(f'<li class="session-item"><div class="session-main"><strong>{name}</strong><span class="muted">{esc(book["author"])} · {"В библиотеке" if book["book_id"] else "Из истории KOReader"}</span></div><span>{format_time(book["reading_seconds"])}</span></li>')
    for session in data["recent_koreader_sessions"]:
        date = datetime.fromtimestamp(session["start"], reading_zone()).strftime("%d.%m.%Y %H:%M")
        session_items.append((session["start"], f'<li class="session-item"><div class="session-main"><strong>{esc(session["title"])}</strong><span class="muted">{date} · KOReader</span></div><span>{format_time(session["duration_seconds"])}</span></li>'))
    today = datetime.now(reading_zone()).date()
    by_date = {item["date"]: item for item in data["daily_activity"]}
    calendar = []
    for offset in range(41, -1, -1):
        date = (today - timedelta(days=offset)).isoformat()
        item = by_date.get(date, {})
        seconds = item.get("koreader_seconds", 0) + item.get("estimated_seconds", 0)
        calendar.append(f'<li class="reading-day {"has-reading" if seconds else ""}"><time datetime="{date}">{date[5:]}</time><span>{format_time(seconds) if seconds else "—"}</span></li>')

    body = f"""
    <section class="page-header">
      <h2>Статистика и аналитика чтения</h2>
      <p class="section-lead">Отслеживайте читательские привычки, прогресс и время, проведённое за книгами.</p>
    </section>

    <div class="stats-grid">
      <div class="stat-card streak-card">
        <span class="stat-icon">🔥</span>
        <div class="stat-number">{streak_badge}</div>
        <div class="stat-title">Читательский стрик</div>
        <span class="stat-subtitle">Непрерывные дни чтения</span>
      </div>

      <div class="stat-card">
        <span class="stat-icon">⏱</span>
        <div class="stat-number">{time_str}</div>
        <div class="stat-title">Время чтения</div>
        <span class="stat-subtitle">KOReader: {format_time(data['koreader_reading_seconds'])}<br>Оценка веб-читалки: {format_time(data['estimated_reading_seconds'])}</span>
      </div>

      <div class="stat-card">
        <span class="stat-icon">📚</span>
        <div class="stat-number">{data['total_books']}</div>
        <div class="stat-title">Книг в библиотеке</div>
        <span class="stat-subtitle">{data['finished_books']} прочитано · {data['reading_books']} в процессе</span>
      </div>

      <div class="stat-card">
        <span class="stat-icon">📖</span>
        <div class="stat-number">{data['total_sessions']}</div>
        <div class="stat-title">Сессий чтения</div>
        <span class="stat-subtitle">В веб-читалке и KOReader</span>
      </div>
    </div>

    <section class="stat-box stats-import">
      <h3>Статистика с Kindle / KOReader</h3>
      {imported_notice}<p>{import_text}</p>
      <form action="/api/stats/koreader/import" method="post" enctype="multipart/form-data" data-async>
        <label>База statistics.sqlite3 (до 64 МБ)<input type="file" name="file" accept=".sqlite3,.sqlite,.db" required></label>
        <button class="button">Импортировать статистику</button><p class="form-message" role="status"></p>
      </form>
      <details><summary>Подключить отправку с Kindle через WebDAV</summary>
        <ol><li>KOReader → Инструменты → Cloud storage+ → добавить WebDAV.</li>
        <li>Адрес: <code>{esc(dav_url)}</code>. Логин: <strong>{esc(username)}</strong>, пароль от сайта. Начальная папка: <code>/</code>.</li>
        <li>Статистика чтения → Настройки → Cloud sync → выберите это хранилище и корневую папку.</li>
        <li>Нажмите «Синхронизировать сейчас», затем обновите эту страницу.</li></ol>
        <p class="muted">Передаётся база статистики; синхронизация позиции настраивается отдельно. Для ручного импорта файл обычно находится в koreader/settings/statistics.sqlite3. Повторный импорт не дублирует историю.</p>
      </details>
    </section>
    <section class="stat-box reading-calendar"><h3>Активность за 42 дня</h3>
      <p class="muted">Часовой пояс: {esc(data['timezone'])}. Время KOReader измерено устройством; веб-читалка даёт приблизительную оценку.</p>
      <ul class="reading-days">{''.join(calendar)}</ul>
    </section>
    <section class="stat-box"><h3>Время по книгам · KOReader</h3>
      <ul class="session-list">{''.join(history_items) or '<li>Импортируйте базу KOReader, чтобы увидеть историю чтения.</li>'}</ul>
    </section>

    <div class="stats-sections">
      <section class="stat-box">
        <h3>Книги в процессе</h3>
        <div class="stat-book-list">
          {''.join(active_items) if active_items else '<p class="muted">Вы пока не начали читать книги.</p>'}
        </div>
      </section>

      <section class="stat-box">
        <h3>Последняя активность</h3>
        <ul class="session-list">
          {''.join(item[1] for item in sorted(session_items, key=lambda item: item[0], reverse=True)[:8]) if session_items else '<li class="muted">Сессий пока нет. Откройте любую книгу в читалке!</li>'}
        </ul>
      </section>
    </div>
    """

    return HTMLResponse(ui.layout("Статистика чтения", body, active="stats"))


def format_time(seconds):
    minutes = int(seconds) // 60
    return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"
