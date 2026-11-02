"""Audiobook player and HTTP Range audio streaming for MP3 and M4B files."""
import html
import os
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

router = APIRouter()


def esc(value):
    return html.escape(str(value or ""), quote=True)


def parse_range_header(range_header: str, file_size: int):
    """Parse HTTP Range header: 'bytes=start-end'."""
    if not range_header or not range_header.startswith("bytes="):
        return 0, file_size - 1
    parts = range_header.replace("bytes=", "").split("-")
    start = int(parts[0]) if parts[0].strip() else 0
    end = int(parts[1]) if len(parts) > 1 and parts[1].strip() else file_size - 1
    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))
    return start, end


@router.get("/audiobook/{book_id}/stream")
def stream_audiobook(book_id: int, request: Request, range: str = Header(None)):
    from . import main
    with main.connect() as db:
        book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(404, "Книга не найдена")

    file_path = Path(book["filename"])
    if not file_path.is_file():
        raise HTTPException(404, "Аудиофайл не найден на сервере")

    suffix = file_path.suffix.lower()
    media_type = "audio/mp4" if suffix == ".m4b" else "audio/mpeg"
    file_size = file_path.stat().st_size

    if not range:
        # Full content response
        def full_iter():
            with open(file_path, "rb") as f:
                while chunk := f.read(128 * 1024):
                    yield chunk
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": media_type
        }
        return StreamingResponse(full_iter(), media_type=media_type, headers=headers)

    # 206 Partial Content
    start, end = parse_range_header(range, file_size)
    content_length = end - start + 1

    def range_iter():
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk_size = min(remaining, 128 * 1024)
                data = f.read(chunk_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
        "Content-Type": media_type
    }
    return StreamingResponse(range_iter(), status_code=206, media_type=media_type, headers=headers)


@router.get("/audiobook/{book_id}", response_class=HTMLResponse)
def audiobook_player_view(book_id: int):
    from . import main
    with main.connect() as db:
        book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(404, "Книга не найдена")

    title = esc(book["title"])
    author = esc(book["author"] or "Неизвестный автор")

    page_html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · Аудиоплеер · Yandex2Kindle</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
  <link rel="stylesheet" href="/static/app.css">
  <style>
    body {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      background: var(--surface);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 20px;
      box-sizing: border-box;
    }}
    .player-card {{
      background: var(--bg);
      border: 1px solid var(--control-border);
      border-radius: 16px;
      padding: 32px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 10px 30px rgba(0,0,0,0.08);
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      position: relative;
    }}
    .back-link {{
      align-self: flex-start;
      margin-bottom: 20px;
      color: var(--muted);
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
    }}
    .back-link:hover {{ color: var(--accent); }}
    .player-cover {{
      width: 200px;
      height: 200px;
      border-radius: 12px;
      object-fit: cover;
      box-shadow: 0 6px 20px rgba(0,0,0,0.15);
      margin-bottom: 24px;
      background: #e2e8f0;
    }}
    .player-title {{
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .player-author {{
      font-size: 14px;
      color: var(--muted);
      margin-bottom: 24px;
    }}
    .timeline-container {{
      width: 100%;
      margin-bottom: 20px;
    }}
    .seek-slider {{
      width: 100%;
      cursor: pointer;
      accent-color: var(--accent);
    }}
    .time-labels {{
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--muted);
      margin-top: 4px;
    }}
    .player-controls {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 20px;
      width: 100%;
      margin-bottom: 24px;
    }}
    .btn-play {{
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: var(--accent);
      color: #ffffff;
      border: none;
      font-size: 22px;
      cursor: pointer;
      display: grid;
      place-items: center;
      box-shadow: 0 4px 12px rgba(37,99,235,0.3);
      transition: transform 0.1s ease;
    }}
    .btn-play:active {{ transform: scale(0.95); }}
    .btn-skip {{
      background: transparent;
      border: 1px solid var(--control-border);
      border-radius: 50%;
      width: 42px;
      height: 42px;
      font-size: 12px;
      font-weight: 600;
      color: var(--ink);
      cursor: pointer;
      display: grid;
      place-items: center;
    }}
    .btn-skip:hover {{ background: var(--selected-bg); }}
    .extra-controls {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      border-top: 1px solid var(--line);
      padding-top: 18px;
      font-size: 13px;
    }}
    .extra-select {{
      padding: 4px 8px;
      font-size: 12px;
      border-radius: 6px;
      border: 1px solid var(--control-border);
      background: var(--surface);
      color: var(--ink);
    }}
  </style>
</head>
<body>
  <div class="player-card">
    <a href="/" class="back-link">← В библиотеку</a>
    <img src="/covers/{book_id}" alt="Обложка" class="player-cover" onerror="this.src='/static/favicon.svg'">
    <h1 class="player-title">{title}</h1>
    <div class="player-author">{author}</div>

    <audio id="audio" src="/audiobook/{book_id}/stream" preload="metadata"></audio>

    <div class="timeline-container">
      <input type="range" id="seek-bar" class="seek-slider" min="0" max="100" value="0">
      <div class="time-labels">
        <span id="current-time">0:00</span>
        <span id="duration-time">0:00</span>
      </div>
    </div>

    <div class="player-controls">
      <button type="button" id="btn-back" class="btn-skip" title="-15 сек">−15</button>
      <button type="button" id="btn-play" class="btn-play" title="Воспроизведение">▶</button>
      <button type="button" id="btn-fwd" class="btn-skip" title="+30 сек">+30</button>
    </div>

    <div class="extra-controls">
      <label>Скорость:
        <select id="speed-select" class="extra-select">
          <option value="0.75">0.75x</option>
          <option value="1.0" selected>1.0x</option>
          <option value="1.25">1.25x</option>
          <option value="1.5">1.5x</option>
          <option value="2.0">2.0x</option>
        </select>
      </label>
      <label>Таймер сна:
        <select id="sleep-select" class="extra-select">
          <option value="0">Выкл</option>
          <option value="15">15 мин</option>
          <option value="30">30 мин</option>
          <option value="45">45 мин</option>
          <option value="60">60 мин</option>
        </select>
      </label>
    </div>
  </div>

  <script>
    (function() {{
      const bookId = {book_id};
      const audio = document.getElementById('audio');
      const btnPlay = document.getElementById('btn-play');
      const btnBack = document.getElementById('btn-back');
      const btnFwd = document.getElementById('btn-fwd');
      const seekBar = document.getElementById('seek-bar');
      const currentTimeLabel = document.getElementById('current-time');
      const durationTimeLabel = document.getElementById('duration-time');
      const speedSelect = document.getElementById('speed-select');
      const sleepSelect = document.getElementById('sleep-select');

      let sleepTimer = null;

      function fmtTime(sec) {{
        if (!sec || isNaN(sec)) return "0:00";
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = Math.floor(sec % 60);
        if (h > 0) {{
          return `${{h}}:${{m < 10 ? '0' : ''}}${{m}}:${{s < 10 ? '0' : ''}}${{s}}`;
        }}
        return `${{m}}:${{s < 10 ? '0' : ''}}${{s}}`;
      }}

      // Restore position
      fetch(`/api/books/${{bookId}}/progress`)
        .then(r => r.json())
        .then(data => {{
          if (data && data.position) {{
            const sec = parseFloat(data.position);
            if (!isNaN(sec) && sec > 0) {{
              audio.currentTime = sec;
            }}
          }}
        }}).catch(() => {{}});

      btnPlay.addEventListener('click', () => {{
        if (audio.paused) {{
          audio.play();
          btnPlay.textContent = '❚❚';
        }} else {{
          audio.pause();
          btnPlay.textContent = '▶';
        }}
      }});

      btnBack.addEventListener('click', () => {{
        audio.currentTime = Math.max(0, audio.currentTime - 15);
      }});

      btnFwd.addEventListener('click', () => {{
        audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 30);
      }});

      speedSelect.addEventListener('change', () => {{
        audio.playbackRate = parseFloat(speedSelect.value);
      }});

      sleepSelect.addEventListener('change', () => {{
        if (sleepTimer) clearTimeout(sleepTimer);
        const mins = parseInt(sleepSelect.value, 10);
        if (mins > 0) {{
          sleepTimer = setTimeout(() => {{
            audio.pause();
            btnPlay.textContent = '▶';
            alert("Таймер сна: воспроизведение остановлено.");
          }}, mins * 60 * 1000);
        }}
      }});

      audio.addEventListener('loadedmetadata', () => {{
        seekBar.max = audio.duration;
        durationTimeLabel.textContent = fmtTime(audio.duration);
      }});

      audio.addEventListener('timeupdate', () => {{
        seekBar.value = audio.currentTime;
        currentTimeLabel.textContent = fmtTime(audio.currentTime);
      }});

      seekBar.addEventListener('input', () => {{
        audio.currentTime = seekBar.value;
      }});

      // Auto-save progress
      setInterval(() => {{
        if (!audio.paused && audio.duration) {{
          const fraction = Math.max(0, Math.min(1, audio.currentTime / audio.duration));
          fetch(`/api/books/${{bookId}}/progress`, {{
            method: 'PUT',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              progress: fraction,
              position: String(Math.floor(audio.currentTime))
            }})
          }}).catch(() => {{}});
        }}
      }}, 5000);
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(page_html)
