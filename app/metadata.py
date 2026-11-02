"""Extract metadata (series, series index, title, author, description, genres, year, isbn) from EPUB and FB2 files."""
import logging
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

logger = logging.getLogger("uvicorn.error")


def extract_metadata_epub(path: Path) -> dict:
    meta = {
        "series": "", "series_index": 0.0, "title": "", "author": "",
        "description": "", "genres": "", "year": None, "isbn": ""
    }
    try:
        with zipfile.ZipFile(path) as z:
            opf_path = None
            try:
                container = z.read("META-INF/container.xml")
                c_root = ET.fromstring(container)
                elem = c_root.find(".//{*}rootfile")
                if elem is not None:
                    opf_path = elem.get("full-path")
            except Exception:
                pass
            if not opf_path:
                opfs = [name for name in z.namelist() if name.lower().endswith(".opf")]
                opf_path = opfs[0] if opfs else None
            if not opf_path or opf_path not in z.namelist():
                return meta

            root = ET.fromstring(z.read(opf_path))

            # Title & Author
            title_elem = root.find(".//{*}title")
            if title_elem is not None and title_elem.text:
                meta["title"] = title_elem.text.strip()

            creators = [c.text.strip() for c in root.findall(".//{*}creator") if c.text and c.text.strip()]
            if creators:
                meta["author"] = ", ".join(creators)

            # Description (Synopsis)
            desc_elem = root.find(".//{*}description")
            if desc_elem is not None and desc_elem.text:
                clean_desc = re.sub(r'<[^>]+>', ' ', desc_elem.text)
                meta["description"] = " ".join(clean_desc.split()).strip()

            # Genres / Subjects
            subjects = [s.text.strip() for s in root.findall(".//{*}subject") if s.text and s.text.strip()]
            if subjects:
                meta["genres"] = ", ".join(subjects)

            # Year / Date
            date_elem = root.find(".//{*}date")
            if date_elem is not None and date_elem.text:
                ym = re.search(r'\b(19\d\d|20\d\d)\b', date_elem.text)
                if ym:
                    meta["year"] = int(ym.group(1))

            # ISBN
            for ident in root.findall(".//{*}identifier"):
                txt = (ident.text or "").strip()
                scheme = (ident.attrib.get("scheme", "") or ident.attrib.get("id", "")).lower()
                if "isbn" in scheme or "isbn" in txt.lower():
                    clean_isbn = re.sub(r'[^0-9X-]', '', txt)
                    if len(clean_isbn.replace("-", "")) in (10, 13):
                        meta["isbn"] = clean_isbn
                        break

            # Calibre meta: <meta name="calibre:series" content="..."/>
            for m in root.findall(".//{*}meta"):
                name = m.get("name") or ""
                prop = m.get("property") or ""
                if name == "calibre:series":
                    meta["series"] = (m.get("content") or "").strip()
                elif name == "calibre:series_index":
                    try:
                        meta["series_index"] = float(m.get("content") or 0)
                    except ValueError:
                        pass
                # EPUB 3 belongs-to-collection
                elif prop == "belongs-to-collection" and m.text:
                    if not meta["series"]:
                        meta["series"] = m.text.strip()
                elif prop == "group-position" and m.text:
                    if not meta["series_index"]:
                        try:
                            meta["series_index"] = float(m.text.strip())
                        except ValueError:
                            pass
    except Exception as exc:
        logger.debug("Error extracting EPUB metadata from %s: %s", path, exc)
    return meta


def extract_metadata_fb2(path: Path) -> dict:
    meta = {
        "series": "", "series_index": 0.0, "title": "", "author": "",
        "description": "", "genres": "", "year": None, "isbn": ""
    }
    try:
        with open(path, "rb") as f:
            head = f.read(2 * 1024 * 1024)

        # Sequence
        seq_match = re.search(rb'<sequence[^>]*name=["\']([^"\']+)["\'][^>]*>', head, re.IGNORECASE)
        if seq_match:
            meta["series"] = seq_match.group(1).decode("utf-8", errors="ignore").strip()
            num_match = re.search(rb'number=["\']([0-9.]+)["\']', seq_match.group(0), re.IGNORECASE)
            if num_match:
                try:
                    meta["series_index"] = float(num_match.group(1).decode("ascii"))
                except ValueError:
                    pass

        # Title
        title_match = re.search(rb'<book-title>(.*?)</book-title>', head, re.IGNORECASE | re.DOTALL)
        if title_match:
            meta["title"] = title_match.group(1).decode("utf-8", errors="ignore").strip()

        # Author (first-name, last-name)
        author_match = re.search(rb'<author>(.*?)</author>', head, re.IGNORECASE | re.DOTALL)
        if author_match:
            author_xml = author_match.group(1)
            first = re.search(rb'<first-name>(.*?)</first-name>', author_xml, re.IGNORECASE | re.DOTALL)
            last = re.search(rb'<last-name>(.*?)</last-name>', author_xml, re.IGNORECASE | re.DOTALL)
            parts = []
            if first:
                parts.append(first.group(1).decode("utf-8", errors="ignore").strip())
            if last:
                parts.append(last.group(1).decode("utf-8", errors="ignore").strip())
            if parts:
                meta["author"] = " ".join(parts)

        # Annotation (Description)
        annot_match = re.search(rb'<annotation>(.*?)</annotation>', head, re.IGNORECASE | re.DOTALL)
        if annot_match:
            clean_annot = re.sub(rb'<[^>]+>', b' ', annot_match.group(1)).decode("utf-8", errors="ignore")
            meta["description"] = " ".join(clean_annot.split()).strip()

        # Genres
        genre_matches = re.findall(rb'<genre>(.*?)</genre>', head, re.IGNORECASE)
        if genre_matches:
            genres = [g.decode("utf-8", errors="ignore").strip() for g in genre_matches if g.strip()]
            meta["genres"] = ", ".join(genres)

        # Year
        date_match = re.search(rb'<date[^>]*>(.*?)</date>', head, re.IGNORECASE)
        if date_match:
            ym = re.search(r'\b(19\d\d|20\d\d)\b', date_match.group(1).decode("utf-8", errors="ignore"))
            if ym:
                meta["year"] = int(ym.group(1))

        # ISBN
        isbn_match = re.search(rb'<isbn>(.*?)</isbn>', head, re.IGNORECASE)
        if isbn_match:
            meta["isbn"] = isbn_match.group(1).decode("utf-8", errors="ignore").strip()

    except Exception as exc:
        logger.debug("Error extracting FB2 metadata from %s: %s", path, exc)
    return meta


def extract_metadata_audio(path: Path) -> dict:
    meta = {
        "series": "", "series_index": 0.0, "title": "", "author": "",
        "description": "", "genres": "Аудиокнига", "year": None, "isbn": ""
    }
    try:
        with open(path, "rb") as f:
            header = f.read(10)
            if header.startswith(b"ID3"):
                size = ((header[6] & 0x7f) << 21) | ((header[7] & 0x7f) << 14) | ((header[8] & 0x7f) << 7) | (header[9] & 0x7f)
                id3_bytes = f.read(min(size, 256 * 1024))
                for tag, key in [(b"TIT2", "title"), (b"TPE1", "author"), (b"TALB", "series")]:
                    idx = id3_bytes.find(tag)
                    if idx != -1 and idx + 10 < len(id3_bytes):
                        fsize = int.from_bytes(id3_bytes[idx+4:idx+8], "big")
                        frame_data = id3_bytes[idx+10:idx+10+min(fsize, 200)]
                        enc = "utf-8" if frame_data[:1] in (b"\x03", b"\x01") else "latin1"
                        text = frame_data[1:].decode(enc, errors="ignore").replace("\x00", "").strip()
                        if text:
                            meta[key] = text
                for ytag in (b"TYER", b"TDRC"):
                    idx = id3_bytes.find(ytag)
                    if idx != -1:
                        ym = re.search(r'\b(19\d\d|20\d\d)\b', id3_bytes[idx:idx+30].decode("ascii", errors="ignore"))
                        if ym:
                            meta["year"] = int(ym.group(1))
                            break
    except Exception as exc:
        logger.debug("Error extracting audio metadata from %s: %s", path, exc)
    if not meta["title"]:
        stem = path.stem.replace("_", " ").strip()
        if " - " in stem:
            parts = stem.split(" - ", 1)
            meta["author"] = meta["author"] or parts[0].strip()
            meta["title"] = parts[1].strip()
        else:
            meta["title"] = stem
    return meta


def extract_book_metadata(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return extract_metadata_epub(path)
    if suffix == ".fb2":
        return extract_metadata_fb2(path)
    if suffix in (".mp3", ".m4b"):
        return extract_metadata_audio(path)
    return {
        "series": "", "series_index": 0.0, "title": "", "author": "",
        "description": "", "genres": "", "year": None, "isbn": ""
    }
