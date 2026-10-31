"""Fetch a BookWyrm user's shelves, ratings and reviews, and render the reading-room page.

Writes <out>/index.html. With --self-host-fonts the fonts are downloaded into
<out>/fonts/ so the page makes no requests to Google (for the website); without it
the page links Google Fonts.

Usage:
  python3 -I build.py --out build
  python3 -I build.py --out ~/Documents/websites/primbs.ca/public_static/reading-room --self-host-fonts
  add --export <bookwyrm-account-export.tar.gz> to include read books that sit on no shelf
"""
import argparse
import datetime
import html
import json
import os
import re
import tarfile
import time
import urllib.error
import urllib.request

BASE = "https://bookwyrm.social"
USER = "drkprmbs"
SHELVES = ["read", "reading", "to-read", "stopped-reading", "canadiana-158335"]
FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Young+Serif&family=Alegreya+Sans:wght@400;500;700"
             "&family=Alegreya+Sans+SC:wght@500;700&family=Caveat:wght@500;700&display=swap")
# Environment Canada's city page for Toronto; it allows cross-site requests and needs no key
WEATHER_URL = "https://api.weather.gc.ca/collections/citypageweather-realtime/items/on-143?f=json"
# a browser user agent, so Google serves woff2
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


OFFLINE = False  # --offline: read BookWyrm pages from the cache instead of fetching them


def get(url, path=None, accept=None, ua="reading-room/1.0"):
    if OFFLINE and path and os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    headers = {"User-Agent": ua, **({"Accept": accept} if accept else {})}
    for attempt in range(4):  # BookWyrm sometimes answers 500 or times out; try again after a pause
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                data = r.read()
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt == 3 or (isinstance(e, urllib.error.HTTPError) and e.code < 500):
                raise
            time.sleep(3 * (attempt + 1))
    if path:
        write_if_changed(path, data)
    return data


def write_if_changed(path, data):
    """Write only when the bytes differ, so an unchanged file keeps its mtime and
    primbs.ca's publish.sh (lftp mirror, size + time) doesn't upload it again."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return
    with open(path, "wb") as f:
        f.write(data)


def text(s):
    s = re.sub(r"<br\s*/?>|</p>\s*<p>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def norm(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def author_list(names):
    out = []
    for a in names:
        a = re.sub(r"\s*\(duplicate\)", "", html.unescape(a.strip()))
        if a and a not in out and not re.match(r"^\w+\s{2,}\w+$", a):
            out.append(a)
    return out


PARTICLES = {"le", "la", "de", "du", "van", "von", "der", "da", "del", "di", "el", "al", "st.", "st"}


def surname_key(first_author):
    """Sort key for an author: 'Louise Penny' -> 'penny louise', 'McCarthy, Mary' -> 'mccarthy mary'."""
    if not first_author:
        return "~"  # no author: after Z
    if "," in first_author:
        last, _, given = first_author.partition(",")
    else:
        parts = first_author.split()
        i = len(parts) - 1
        while i > 0 and parts[i - 1].lower() in PARTICLES:
            i -= 1
        last, given = " ".join(parts[i:]), " ".join(parts[:i])
    return norm(last) + " " + norm(given)


def with_authors(book, names):
    names = author_list(names)
    book["a"] = ", ".join(names)
    book["k"] = surname_key(names[0] if names else "")
    return book


def shelf(name, d):
    books, page = [], 1
    while True:
        t = get(f"{BASE}/user/{USER}/books/{name}?page={page}", os.path.join(d, f"{name}-{page}.html")).decode()
        rows = re.findall(r'<tr class="book-preview">(.*?)</tr>', t, re.S)
        for row in rows:
            ti = re.search(r'data-title="Title"><a href="([^"]*)"[^>]*>(.*?)</a>', row, re.S)
            if ti:
                books.append(with_authors({"t": html.unescape(ti.group(2).strip()),
                                           "u": ti.group(1), "id": ti.group(1).split("/")[2]},
                                          re.findall(r'itemprop="name"\s*>(.*?)</span>', row, re.S)))
        if not rows or f"page={page + 1}" not in t:
            return books
        page += 1


def outbox(d):
    """Latest rating, review and finish date per book id, from the public ActivityPub outbox.
    Finish notes also carry title and author, for read books that sit on no shelf."""
    out, page = {}, 1
    while True:
        j = json.loads(get(f"{BASE}/user/{USER}/outbox?page={page}", os.path.join(d, f"outbox-{page}.json"),
                           "application/activity+json"))
        for o in j.get("orderedItems", []):  # newest first: keep the first value we see
            book = o.get("inReplyToBook")
            if not book:
                tags = [t for t in o.get("tag", []) if t.get("type") == "Edition"]
                if o.get("type") == "Note" and tags and "finished reading" in (o.get("content") or ""):
                    bid = tags[0]["href"].rstrip("/").split("/")[-1]
                    r = out.setdefault(bid, {})
                    if "f" not in r:
                        title = tags[0].get("name", "").lstrip("@")
                        cover = next((x.get("name", "") for x in o.get("attachment", [])), "")
                        author = cover.split(": " + title)[0] if ": " + title in cover else ""
                        r.update({"f": o["published"][:10], "meta": with_authors(
                            {"t": title, "u": f"/book/{bid}", "id": bid}, [author])})
                continue
            r = out.setdefault(book.rstrip("/").split("/")[-1], {})
            if o.get("rating") and "r" not in r:
                r["r"] = o["rating"]
            if o.get("type") == "Article" and "v" not in r:
                m = re.search(r"\): (.*)$", o.get("name") or "")
                if m and m.group(1) != "None":
                    r["h"] = html.unescape(m.group(1))
                r["v"] = text(o.get("content") or "")
        if not j.get("next"):
            return out
        page += 1


def reading_goal(d):
    """This year's reading goal from the public profile page: {"y": 2026, "n": 41, "of": 50}, or None."""
    t = get(f"{BASE}/user/{USER}", os.path.join(d, "profile.html")).decode()
    m = re.search(r'(\d{4}) Reading Goal</h2>.*?<progress[^>]*value="(\d+)"[^>]*max="(\d+)"', t, re.S)
    return {"y": int(m.group(1)), "n": int(m.group(2)), "of": int(m.group(3))} if m else None


def export_books(path):
    """Books from a BookWyrm account export: id -> book with rating, review and finish date."""
    with tarfile.open(path) as tf:
        archive = json.load(tf.extractfile("archive.json"))
    out = {}
    for b in archive["books"]:
        e = b["edition"]
        bid = e["id"].rstrip("/").split("/")[-1]
        book = with_authors({"t": e["title"], "u": f"/book/{bid}", "id": bid,
                             "shelves": [s["id"].rstrip("/").split("/")[-1] for s in b.get("shelves", [])]},
                            [a.get("name", "") for a in b.get("authors", [])])
        finished = sorted((r.get("finish_date") or "")[:10] for r in b.get("readthroughs", []) if r.get("finish_date"))
        if finished:
            book["f"] = finished[-1]
        reviews = sorted(b.get("reviews", []), key=lambda r: r.get("published", ""))
        if reviews:
            last = reviews[-1]
            if last.get("rating"):
                book["r"] = last["rating"]
            if last.get("name"):
                book["h"] = html.unescape(last["name"])
            if last.get("content"):
                book["v"] = text(last["content"])
        out[bid] = book
    return out


def font_tags(out_dir, self_host):
    if not self_host:
        return ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
                '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
                f'<link href="{FONTS_CSS}" rel="stylesheet">')
    css = get(FONTS_CSS, ua=BROWSER_UA).decode()
    fonts_dir = os.path.join(out_dir, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    keep = []
    # keep the latin and latin-ext faces; that covers English, German and French
    for subset, block in re.findall(r"/\* ([\w-]+) \*/\s*(@font-face\s*{.*?})", css, re.S):
        if subset not in ("latin", "latin-ext"):
            continue
        family = re.search(r"font-family: '([^']+)'", block).group(1)
        weight = re.search(r"font-weight: (\d+)", block).group(1)
        name = f"{family.lower().replace(' ', '-')}-{weight}-{subset}.woff2"
        url = re.search(r"url\((https://[^)]+)\)", block).group(1)
        get(url, os.path.join(fonts_dir, name), ua=BROWSER_UA)
        keep.append(block.replace(url, f"fonts/{name}"))
    return "<style>\n" + "\n".join(keep) + "\n</style>"


def book_record(bid, cache, refresh):
    """A book's ActivityPub record, cached in <cache>/books/; fetched only when missing or on --refresh-descriptions."""
    path = os.path.join(cache, "books", f"{bid}.json")
    if os.path.exists(path) and (OFFLINE or not refresh):
        with open(path) as f:
            return json.load(f)
    if OFFLINE:
        return {}
    try:
        data = json.loads(get(f"{BASE}/book/{bid}", path, "application/activity+json"))
        time.sleep(0.25)  # be gentle with bookwyrm.social
        return data
    except (urllib.error.URLError, ValueError):
        return {}


def description(bid, cache, refresh):
    """BookWyrm's description: the edition's, else the work's, else another edition's."""
    edition = book_record(bid, cache, refresh)
    if (edition.get("description") or "").strip():
        return text(edition["description"])
    work_id = (edition.get("work") or "").rstrip("/").split("/")[-1]
    if not work_id:
        return ""
    work = book_record(work_id, cache, refresh)
    if (work.get("description") or "").strip():
        return text(work["description"])
    for ed in (work.get("editions") or [])[:8]:
        other = book_record(ed.rstrip("/").split("/")[-1], cache, refresh)
        if (other.get("description") or "").strip():
            return text(other["description"])
    return ""


COVER_WIDTH = 240  # pixels; the card shows covers 150px wide


def cover(bid, cache, refresh, out_dir):
    """The book's cover, made small for the card. Returns a path next to the page or None.
    Needs Pillow for resizing; without it there are no covers."""
    edition = book_record(bid, cache, refresh)
    url = (edition.get("cover") or {}).get("url")
    if not url:
        work_id = (edition.get("work") or "").rstrip("/").split("/")[-1]
        url = ((book_record(work_id, cache, refresh) if work_id else {}).get("cover") or {}).get("url")
    if not url:
        return None
    covers = os.path.join(cache, "covers")
    os.makedirs(os.path.join(covers, "card"), exist_ok=True)
    original = os.path.join(covers, bid + os.path.splitext(url.split("?")[0])[1].lower())
    small = os.path.join(covers, "card", bid + ".webp")
    if not os.path.exists(small) or refresh:
        if not os.path.exists(original) or refresh:
            if OFFLINE:
                return None
            try:
                get(url, original)
                time.sleep(0.25)
            except urllib.error.URLError:
                return None
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            with Image.open(original) as im:
                im = im.convert("RGB")
                if im.width > COVER_WIDTH:
                    im = im.resize((COVER_WIDTH, round(im.height * COVER_WIDTH / im.width)), Image.LANCZOS)
                im.save(small, "WEBP", quality=72)
        except OSError:
            return None
    os.makedirs(os.path.join(out_dir, "covers"), exist_ok=True)
    with open(small, "rb") as f:
        write_if_changed(os.path.join(out_dir, "covers", bid + ".webp"), f.read())
    return f"covers/{bid}.webp"


def spine_color(bid, cache):
    """The cover's dominant colour, a little darker so the room stays warm. None without a cover or Pillow."""
    small = os.path.join(cache, "covers", "card", bid + ".webp")
    if not os.path.exists(small):
        return None
    try:
        from PIL import Image
        with Image.open(small) as im:
            pal = im.convert("RGB").resize((40, 60)).quantize(colors=6)
            counts = sorted(pal.getcolors(), reverse=True)
            rgb = pal.getpalette()
    except (ImportError, OSError):
        return None
    def colour(i):
        return rgb[3 * i:3 * i + 3]
    # prefer the commonest colour that isn't paper-white or ink-black (title text, borders)
    pick = next((i for _, i in counts if 40 < sum(colour(i)) / 3 < 235), counts[0][1])
    r, g, b = (round(c * 0.82 + w * 0.18) for c, w in zip(colour(pick), (42, 29, 18)))  # toward the room's brown
    return f"#{r:02x}{g:02x}{b:02x}"


def collect(cache, export, refresh_descriptions=False, out_dir=None):
    s = {n: shelf(n, cache) for n in SHELVES}
    notes = outbox(cache)
    extra = export_books(export) if export else {}
    canadiana = s.pop("canadiana-158335")
    canada = {b["id"] for b in canadiana}  # a topic label only; the live shelf decides

    # A book is read when it is on the Read shelf or has a finish date, unless it is
    # currently on Reading, To read or Stopped. The export adds the undated books that
    # sit on no shelf (Dirk counts every one of those as read).
    placed = {b["id"] for v in s.values() for b in v}
    # other editions of a book already read, being read or dropped; a To read copy doesn't
    # count, because a finished book wins over a stale want-to-read entry
    titles = {norm(b["t"]) for n in ("read", "reading", "stopped-reading") for b in s[n]}
    known = {**extra, **{bid: n["meta"] for bid, n in notes.items() if "meta" in n},
             **{b["id"]: b for b in canadiana}}
    finished = {bid for bid, n in notes.items() if "f" in n} | {bid for bid, b in extra.items() if "f" in b}
    undated = {bid for bid, b in extra.items() if not b["shelves"]}
    for bid in sorted(finished | undated):
        b = known.get(bid)
        if b and bid not in placed and norm(b["t"]) not in titles:
            s["read"].append(dict(b))
            placed.add(bid)
            titles.add(norm(b["t"]))

    done = {norm(b["t"]) for b in s["read"]}
    s["to-read"] = [b for b in s["to-read"] if norm(b["t"]) not in done]

    for v in s.values():
        for b in v:
            old = extra.get(b["id"], {})
            for k in ("f", "r", "h", "v"):
                if k in old:
                    b.setdefault(k, old[k])
            b.update({k: x for k, x in notes.get(b["id"], {}).items()
                      if k != "meta" and (k != "f" or "f" not in b or x > b["f"])})
            b.pop("shelves", None)
            if b["id"] in canada:
                b["cn"] = 1
    # by author surname; each author's books in the order they were finished, undated last
    s["read"].sort(key=lambda b: (b.get("k", "~"), b.get("f") or "9999", norm(b["t"])))
    os.makedirs(os.path.join(cache, "books"), exist_ok=True)
    for v in s.values():
        for b in v:
            d = description(b["id"], cache, refresh_descriptions)
            if d:
                b["d"] = d
            pages = book_record(b["id"], cache, refresh_descriptions).get("pages")
            if pages:
                b["p"] = pages
            c = cover(b["id"], cache, refresh_descriptions, out_dir)
            if c:
                b["c"] = c
                sc = spine_color(b["id"], cache)
                if sc:
                    b["sc"] = sc
    goal = reading_goal(cache)
    if goal:
        s["goal"] = goal
    s["built"] = datetime.date.today().isoformat()
    return s


def render(page, data):
    return page.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))


def main():
    p = argparse.ArgumentParser(description="Build the reading-room page from BookWyrm.")
    p.add_argument("--out", required=True, help="output folder; index.html is written there")
    p.add_argument("--template", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html"))
    p.add_argument("--cache", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads"),
                   help="where fetched pages are kept (default: downloads/)")
    p.add_argument("--export", help="BookWyrm account export (.tar.gz) for read books that sit on no shelf")
    p.add_argument("--self-host-fonts", action="store_true", help="download fonts next to the page instead of linking Google Fonts")
    p.add_argument("--no-weather", action="store_true",
                   help="don't ask Environment Canada for Toronto's weather; the window shows only the time of day")
    p.add_argument("--preview-controls", action="store_true",
                   help="add the test-only panel for previewing dates, time of day and weather")
    p.add_argument("--refresh-descriptions", action="store_true",
                   help="fetch every book's record and cover again instead of using the cached ones")
    p.add_argument("--offline", action="store_true", help="use the BookWyrm pages already in the cache (no fetching)")
    a = p.parse_args()
    global OFFLINE
    OFFLINE = a.offline

    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)
    os.makedirs(a.cache, exist_ok=True)
    data = collect(a.cache, a.export and os.path.expanduser(a.export), a.refresh_descriptions, out)
    with open(a.template) as f:
        page = f.read()
    page = page.replace("__FONTS__", font_tags(out, a.self_host_fonts))
    preview = ""
    if a.preview_controls:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview-controls.html")) as f:
            preview = f.read()
    page = page.replace("__PREVIEW__", preview)
    page = page.replace("__WEATHER_URL__", "null" if a.no_weather else json.dumps(WEATHER_URL))
    index = os.path.join(out, "index.html")
    # the footer date says when the shelves last changed, not when the script last ran, so
    # a rebuild without news leaves index.html untouched and a deploy has nothing to upload
    if os.path.exists(index):
        with open(index, encoding="utf-8") as f:
            current = f.read()
        before = re.search(r'"built": "(\d{4}-\d\d-\d\d)"', current)
        if before and render(page, {**data, "built": before.group(1)}) == current:
            data["built"] = before.group(1)
    write_if_changed(index, render(page, data).encode())
    books = [b for v in data.values() if isinstance(v, list) for b in v]
    print({k: len(v) for k, v in data.items() if isinstance(v, list)},
          "rated:", sum(1 for b in books if b.get("r")), "reviewed:", sum(1 for b in books if b.get("v")),
          "dated:", sum(1 for b in data["read"] if b.get("f")), "described:", sum(1 for b in books if b.get("d")),
          "covers:", sum(1 for b in books if b.get("c")), "pages:", sum(1 for b in books if b.get("p")),
          "->", os.path.join(out, "index.html"))


if __name__ == "__main__":
    main()
