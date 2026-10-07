"""Background-only news networking, parsing, and persistent caching."""
import json
import re
import ssl
from html import escape
import threading
import time
from urllib.parse import urljoin, urlparse

NEWS_TTL = 600
ARTICLE_TTL = 3600
ARTICLE_FORMAT = 2
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_ID = re.compile(r"/news/(\d+)")
_GENERIC = {"learn more", "read more", "view all", "read more stories", "more"}
_client = None
_client_lock = threading.Lock()


def _http_client():
    global _client
    import httpx
    with _client_lock:
        if _client is None:
            try:
                import truststore
                context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except ImportError:
                context = ssl.create_default_context()
            _client = httpx.Client(
                headers=_HEADERS, verify=context, follow_redirects=True,
                timeout=httpx.Timeout(12, connect=5, pool=5),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
    return _client


def is_news_article_link(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return (parsed.scheme in ("http", "https")
            and host in ("worldofwarcraft.blizzard.com", "news.blizzard.com")
            and bool(re.search(r"/(?:news|world-of-warcraft)/(\d+)(?:/|$)", parsed.path)))


def is_forum_link(url):
    host = (urlparse(url).hostname or "").casefold()
    return host == "forums.blizzard.com" or host.endswith(".forums.blizzard.com")


def download_html(url, limit, json_response=False):
    if urlparse(url).scheme not in ("https", "http"):
        raise ValueError("Only HTTP and HTTPS news links are supported")
    headers = {"Accept": "application/json"} if json_response else None
    with _http_client().stream("GET", url, headers=headers) as response:
        if response.status_code == 404:
            raise ValueError("This page is unavailable (404). It may have moved, been removed, or require an account.")
        if response.status_code in (401, 403):
            raise ValueError("This website denied access to the reader. The page may be restricted.")
        if response.status_code == 429:
            raise ValueError("This website is receiving too many requests. Please try again later.")
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        allowed = ("application/json",) if json_response else ("text/html", "application/xhtml")
        if content_type and not any(t in content_type for t in allowed):
            raise ValueError("The website did not return the requested page format")
        chunks, size = [], 0
        for chunk in response.iter_bytes(chunk_size=65536):
            size += len(chunk)
            if size > limit:
                raise ValueError("This page is too large for the news reader")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        try:
            text = b"".join(chunks).decode(encoding, "replace")
        except LookupError:
            text = b"".join(chunks).decode("utf-8", "replace")
        return str(response.url), text


def cached_entry(cache_dir, kind, url):
    """Stale entries are retained for offline use. Cache failures are nonfatal."""
    try:
        from diskcache import Cache
        with Cache(str(cache_dir), timeout=1, size_limit=32 * 1024 * 1024) as cache:
            entry = cache.get((kind, url))
        if isinstance(entry, dict) and isinstance(entry.get("saved"), (int, float)) and "data" in entry:
            if kind == "feed":
                data = entry["data"]
                if not isinstance(data, list):
                    return None
                filtered = [article for article in data if isinstance(article, dict)
                            and is_news_article_link(urljoin(url, str(article.get("url") or "")))]
                if not filtered:
                    return None
                entry = {**entry, "data": filtered}
            return entry
    except Exception:
        pass
    return None


def store_entry(cache_dir, kind, url, data):
    entry = {"saved": time.time(), "data": data}
    if kind == "article":
        entry["format"] = ARTICLE_FORMAT
    try:
        from diskcache import Cache
        with Cache(str(cache_dir), timeout=1, size_limit=32 * 1024 * 1024) as cache:
            cache.set((kind, url), entry)
    except Exception:
        pass
    return entry


def is_fresh(entry, ttl):
    return entry is not None and 0 <= time.time() - entry["saved"] < ttl


def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse_feed(html, base_url):
    soup = _soup(html)
    articles = {}

    def add(title, summary, href, article_id=""):
        title = " ".join(str(title).split())
        href = urljoin(base_url, str(href))
        if not title or title.isdigit() or title.casefold() in _GENERIC or urlparse(href).scheme not in ("http", "https"):
            return
        if not is_news_article_link(href):
            return
        match = _ID.search(href)
        key = str(article_id or (match.group(1) if match else href))
        item = {"id": key, "title": title, "summary": summary, "url": href}
        if key not in articles:
            articles[key] = item
        elif not articles[key]["summary"] and summary:
            articles[key]["summary"] = summary

    for card in soup.select(".ArticleTile, blz-card"):
        link = card.select_one("a[href], blz-button[href]")
        href = card.get("href") or (link.get("href") if link else "")
        title = card.select_one('.ArticleTile-title, [slot="heading"], h2, h3')
        summary = card.select_one('.ArticleTile-subtitle, [slot="description"]')
        if href:
            add(_text(title) or _text(link), _text(summary), href)
    model = soup.find("script", id="model")
    if model:
        try:
            payload = model.get_text().strip()
            payload = re.sub(r"^\s*model\s*=\s*", "", payload).rstrip("; \r\n")
            for blog in json.loads(payload).get("blogList", {}).get("blogs", []):
                if isinstance(blog, dict) and blog.get("url"):
                    add(_text(_soup(blog.get("title") or "")),
                        _text(_soup(blog.get("description") or "")), blog["url"], blog.get("id"))
        except (ValueError, TypeError, AttributeError):
            pass
    for link in soup.select("a[href], blz-button[href]"):
        if _ID.search(link["href"]):
            add(_text(link) or link.get("aria-label", ""), "", link["href"])
    return list(articles.values())


def fetch_feed(url, cache_dir, force=False):
    cached = cached_entry(cache_dir, "feed", url)
    if not force and is_fresh(cached, NEWS_TTL):
        return cached, None
    try:
        resolved, html = download_html(url, 2_000_000)
        articles = parse_feed(html, resolved)
        if not articles:
            raise ValueError("No news articles found on this page")
        return store_entry(cache_dir, "feed", url, articles), None
    except Exception as error:
        return cached, str(error)


def parse_article(html, base_url):
    soup = _soup(html)
    page_title = _text(soup.find("h1")) or _text(soup.title)
    # Some sites publish the article body in structured data rather than visible HTML.
    structured = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            pending = [json.loads(script.get_text())]
            while pending:
                obj = pending.pop()
                if isinstance(obj, list):
                    pending.extend(obj)
                elif isinstance(obj, dict):
                    kind = obj.get("@type", [])
                    kind = [kind] if isinstance(kind, str) else (kind if isinstance(kind, list) else [])
                    if any(t in ("Article", "NewsArticle", "BlogPosting") for t in kind):
                        body = obj.get("articleBody")
                        if isinstance(body, str) and body.strip():
                            structured.append((obj.get("headline") or page_title, body))
                    pending.extend(v for v in obj.values() if isinstance(v, (dict, list)))
        except (ValueError, TypeError):
            pass
    for element in soup.select("script, style, nav, footer, aside, noscript, svg, button, form, header"):
        element.decompose()
    names = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "td", "th", "figcaption"}

    def blocks_from(body):
        blocks = []
        for node in body.find_all(list(names | {"img"})):
            if node.name == "img":
                source = node.get("data-src") or node.get("src") or ""
                srcset = node.get("data-srcset") or node.get("srcset")
                if srcset:
                    candidates = srcset.split(",")[-1].strip().split()
                    if candidates:
                        source = candidates[0]
                if not source or source.startswith("data:"):
                    continue
                source = urljoin(base_url, source)
                if urlparse(source).scheme not in ("http", "https"):
                    continue
                width, height = node.get("width", ""), node.get("height", "")
                if str(width).isdigit() and str(height).isdigit() and int(width) <= 48 and int(height) <= 48:
                    continue
                blocks.append({"kind": "image", "text": node.get("alt", ""), "url": source, "links": []})
                continue
            parent = node.parent
            nested = False
            while parent is not None and parent is not body:
                if parent.name in names:
                    nested = True
                    break
                parent = parent.parent
            if nested:
                continue
            text = _text(node)
            if not text:
                continue
            links = []
            for link in node.select("a[href]"):
                href = urljoin(base_url, link["href"])
                if urlparse(href).scheme in ("http", "https"):
                    links.append(href)
            blocks.append({"kind": "caption" if node.name == "figcaption" else node.name, "text": text, "links": list(dict.fromkeys(links))})
        return blocks

    def finish(title, blocks):
        hero = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
        source = urljoin(base_url, hero.get("content", "")) if hero else ""
        if hero and urlparse(source).scheme in ("http", "https") and not any(
                block.get("url") == source for block in blocks if block["kind"] == "image"):
            image = {"kind": "image", "text": "", "url": source, "links": []}
            index = 1 if blocks and blocks[0]["kind"] == "h1" else 0
            blocks.insert(index, image)
        return base_url, title, blocks

    # Match case and punctuation variants used by Blizzard's different layouts.
    containers = []
    markers = ("articledetailbody", "articledetailcontent", "articlebody", "articlecontent",
               "newscontent", "newsarticlecontent", "blogbody", "postbody", "richtext",
               "articletext", "entrycontent", "blogcontent", "newsbody", "postcontent")
    for node in soup.find_all(["div", "section", "article", "main"]):
        attributes = [node.get("id", ""), *node.get("class", []), node.get("itemprop", "")]
        normalized = [re.sub(r"[^a-z0-9]", "", str(value).casefold()) for value in attributes]
        if any(marker in value for value in normalized for marker in markers):
            containers.append(node)
    explicit = {id(node) for node in containers}
    containers.extend(soup.find_all("article"))
    containers.extend(soup.find_all("main"))
    best = None
    for body in containers:
        blocks = blocks_from(body)
        # A heading alone or empty wrapper should not hide another readable container.
        length = sum(len(block["text"]) for block in blocks if block["kind"] not in ("h1", "h2", "h3", "h4"))
        priority = 3 if id(body) in explicit else (2 if body.name == "article" else 1)
        score = (priority, length)
        if length:
            if best is None or score > best[0]:
                best = (score, body, blocks)
        elif not blocks and _text(body):
            text = _text(body)
            score = (priority, len(text))
            if best is None or score > best[0]:
                best = (score, body, [{"kind": "p", "text": text, "links": []}])
    if best:
        _, body, blocks = best
        title = _text(body.find("h1")) or page_title or "World of Warcraft News"
        return finish(title, blocks)
    if structured:
        title, text = max(structured, key=lambda item: len(item[1]))
        parsed = _soup(text)
        blocks = blocks_from(parsed)
        if not blocks:
            blocks = [{"kind": "p", "text": part.strip(), "links": []}
                      for part in re.split(r"\n\s*\n", _text(parsed)) if part.strip()]
        return finish(str(title or "World of Warcraft News"), blocks)
    # Older layouts have no semantic article wrapper. Use substantial paragraphs,
    # excluding navigation and short cards rather than returning the entire page.
    paragraphs = [node for node in soup.find_all("p") if len(_text(node)) >= 80]
    if len(paragraphs) >= 2:
        blocks = []
        for paragraph in paragraphs:
            blocks.extend(blocks_from(_soup(str(paragraph))))
        return finish(page_title or "World of Warcraft News", blocks)
    raise ValueError("This page did not provide readable article text. It may require JavaScript or browser access. Use OPEN IN BROWSER.")


def forum_topic_endpoint(url):
    """Keep Blizzard's /en/wow mount point when requesting public topic data."""
    parsed = urlparse(url)
    if not is_forum_link(url) or "/t/" not in parsed.path:
        raise ValueError("This is not a Blizzard forum topic link")
    prefix, suffix = parsed.path.split("/t/", 1)
    parts = suffix.strip("/").split("/")
    index = 0 if re.fullmatch(r"\d+(?:\.json)?", parts[0]) else 1
    if len(parts) <= index or not re.fullmatch(r"\d+(?:\.json)?", parts[index]):
        raise ValueError("The forum topic link has no valid topic number")
    topic = parts[index].removesuffix(".json")
    # Retain a post position when following a link farther down a discussion.
    position = parts[index + 1] if len(parts) > index + 1 else ""
    position = position.removesuffix(".json")
    position = "/" + position if position.isdigit() else ""
    return f"{parsed.scheme}://{parsed.netloc}{prefix}/t/{topic}{position}.json"


def parse_forum_topic(payload, url):
    if not isinstance(payload, dict):
        raise ValueError("The forum did not return readable topic data")
    stream = payload.get("post_stream") or {}
    posts = stream.get("posts") or []
    title = str(payload.get("title") or "Blizzard forum discussion")
    blocks = []
    for post in posts:
        if not isinstance(post, dict) or not isinstance(post.get("cooked"), str) or not post["cooked"].strip():
            continue
        author = str(post.get("display_username") or post.get("username") or "Forum member")
        number = post.get("post_number", "")
        try:
            _, _, content = parse_article("<article>" + post["cooked"] + "</article>", url)
        except ValueError:
            continue
        blocks.append({"kind": "h3", "text": f"{author} • Post {number}", "links": []})
        blocks.extend(content)
    if not blocks:
        raise ValueError("This forum topic did not provide readable posts")
    if isinstance(stream.get("stream"), list) and len(stream["stream"]) > len(posts):
        last = max((post.get("post_number", 0) for post in posts if isinstance(post, dict)
                    and isinstance(post.get("post_number"), int)), default=0)
        base = forum_topic_endpoint(url).split("/t/", 1)[0]
        topic = str(payload.get("id") or forum_topic_endpoint(url).split("/t/",1)[1].split("/",1)[0].removesuffix(".json"))
        blocks.append({"kind": "p", "text": "Continue reading the next posts:",
                       "links": [f"{base}/t/{topic}/{last + 1}"]})
    return url, title, blocks


def fetch_forum_topic(url):
    try:
        _, raw = download_html(forum_topic_endpoint(url), 4_000_000, json_response=True)
        return parse_forum_topic(json.loads(raw), url)
    except Exception as api_error:
        # Public JSON availability varies. Try server-rendered or preloaded topic HTML.
        try:
            resolved, html = download_html(url, 4_000_000)
            soup = _soup(html)
            for node in soup.select("[data-preloaded]"):
                try:
                    preloaded = json.loads(node["data-preloaded"])
                    for value in preloaded.values():
                        payload = json.loads(value) if isinstance(value, str) else value
                        if isinstance(payload, dict) and payload.get("post_stream"):
                            return parse_forum_topic(payload, resolved)
                except (ValueError, TypeError, AttributeError):
                    continue
            cooked = soup.select('.cooked, [itemprop="articleBody"], [itemprop="text"]')
            if cooked:
                title = _text(soup.find("h1")) or _text(soup.title)
                body = '<article><h1>' + escape(title) + '</h1>' + ''.join(str(node) for node in cooked) + '</article>'
                return parse_article(body, resolved)
            return parse_article(html, resolved)
        except Exception:
            raise ValueError("The forum topic could not be loaded inside the app. "
                             "It may be unavailable, restricted, or temporarily blocked. "
                             "Try RELOAD later. " + str(api_error)) from api_error


def fetch_article(url, cache_dir, force=False):
    cached = cached_entry(cache_dir, "article", url)
    if not force and is_fresh(cached, ARTICLE_TTL) and cached.get("format") == ARTICLE_FORMAT:
        return cached["data"], None
    try:
        if is_forum_link(url):
            result = fetch_forum_topic(url)
        else:
            resolved, html = download_html(url, 4_000_000)
            result = fetch_forum_topic(resolved) if is_forum_link(resolved) else parse_article(html, resolved)
        resolved = result[0]
        store_entry(cache_dir, "article", url, result)
        if resolved != url:
            store_entry(cache_dir, "article", resolved, result)
        return result, None
    except Exception as error:
        if cached:
            return cached["data"], str(error)
        raise


def fetch_reader_image(url, cache_dir, max_width=900):
    """Download/decode off the Tk thread; bound compressed bytes and pixel count."""
    import io
    from PIL import Image
    cached = cached_entry(cache_dir, "image", url)
    raw = cached["data"] if cached else None
    if not is_fresh(cached, 86400):
        try:
            if urlparse(url).scheme not in ("http", "https"):
                raise ValueError("Unsupported image link")
            with _http_client().stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith("image/"):
                    raise ValueError("Not an image")
                chunks, size = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > 8_000_000:
                        raise ValueError("Image is too large")
                    chunks.append(chunk)
                raw = b"".join(chunks)
        except Exception:
            if raw is None:
                raise
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError("Image dimensions are too large")
        image.thumbnail((max(100, min(900, max_width)), 650), Image.Resampling.LANCZOS)
        result = image.convert("RGBA")
    if cached is None or raw != cached["data"]:
        store_entry(cache_dir, "image", url, raw)
    return result
