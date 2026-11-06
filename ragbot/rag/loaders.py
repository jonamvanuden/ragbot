import io
import re
import time
from collections import deque
from pathlib import Path
from typing import Iterator
from urllib.parse import urldefrag, urljoin, urlparse

import lxml.html
import requests
import trafilatura
from pypdf import PdfReader

from ragbot import config
from ragbot.rag.schema import Document

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-planer-ragbot/0.1)"}
SKIP = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|ico|zip|mp4|mp3|css|js|xml|woff2?)(\?|$)"
    r"|/wp-(admin|json|login|content/plugins)|/feed/?$|/xmlrpc|[?&](replytocom|s|p)=",
    re.I,
)


def _norm(url: str) -> str:
    return urldefrag(url)[0].rstrip("/")


def pdf_text(data: bytes) -> str:
    try:
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return ""

def docx_text(path: Path) -> str:
    """Word documents: all paragraphs, then all tables (one line per table row)."""
    from docx import Document as WordDocument      # alias: our own class is also called Document
    try:
        doc = WordDocument(str(path))
    except Exception:
        return ""
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def crawl_site(start: str = config.START_URL, max_pages: int = config.MAX_PAGES,
               delay: float = 0.3) -> Iterator[Document]:
    """Breadth-first crawl of one domain. Yields HTML pages and linked PDFs as Documents."""
    host = urlparse(start).netloc.removeprefix("www.")
    same_site = lambda u: urlparse(u).netloc.removeprefix("www.") == host
    seen, queue = set(), deque([_norm(start)])
    session = requests.Session()
    session.headers.update(HEADERS)

    while queue and len(seen) < max_pages:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        try:
            r = session.get(url, timeout=20)
        except requests.RequestException as e:
            print(f"  ! {url}: {e}")
            continue
        if r.status_code != 200:
            continue
        ctype = r.headers.get("content-type", "")

        # --- linked PDFs (brochures, manuals, data sheets) ---
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            text = pdf_text(r.content)
            if text.strip():
                yield Document(url, Path(urlparse(url).path).stem.replace("-", " "), text)
            continue
        if "html" not in ctype:
            continue

        # --- HTML: main content only (no menu, footer, cookie banner) ---
        text = trafilatura.extract(r.text, url=url, output_format="markdown",
                                   include_tables=True, include_links=False, favor_recall=True)
        try:
            tree = lxml.html.fromstring(r.content)
        except Exception:
            continue
        title = (tree.findtext(".//title") or url).split(" • ")[0].strip()
        if text and len(text) > 200:
            yield Document(url, title, text)

        for href in tree.xpath("//a/@href"):
            link = _norm(urljoin(url, href))
            if link.startswith("http") and same_site(link) and link not in seen and not SKIP.search(link):
                queue.append(link)
        time.sleep(delay)   # be polite to the web server


def load_files(folder: Path = config.DOCS_DIR) -> Iterator[Document]:
    """Internal documents: .pdf, .md, .txt (recursively)."""
    for p in sorted(folder.rglob("*")):
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            text = pdf_text(p.read_bytes())
        elif suffix == ".docx":
            text = docx_text(p)
        elif suffix in {".md", ".txt"}:
            text = p.read_text(encoding="utf-8", errors="ignore")
        else:
            continue
        if text.strip():
            yield Document(str(p.relative_to(config.ROOT)), p.stem, text)