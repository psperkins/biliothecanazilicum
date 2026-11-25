#!/usr/bin/env python3
"""Sync Bibliotheca Nazilicum canon from the Drupal API into AnythingLLM.

Reads the canon endpoints (izizop/songs, izizop/fragments, izizop/albums,
izizop/collections, izizop/headings), turns every song, annotation, text
fragment, album, collection, heading and library section into its own document with a citation header,
and keeps an AnythingLLM workspace in step: new and changed items are
uploaded and embedded, removed items are taken out. A local SQLite file
records what was synced and keeps every earlier version of each document,
so you can see what changed in canon over time.

Usage:
  python3 canon_sync.py              sync changes (default)
  python3 canon_sync.py --dry-run    show what would change, touch nothing
  python3 canon_sync.py --rebuild    remove everything synced and start over
  python3 canon_sync.py status       summary of what is synced
  python3 canon_sync.py changes      recent canon changes recorded locally
  python3 canon_sync.py gc           clear ingested files out of source/ (--dry-run to preview)
  python3 canon_sync.py retract X    take ingested manuscript X out of AnythingLLM

Text files in source/canon/ are synced too, as manuscripts (see README).

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "canon_sync.sqlite"
FOLDER = "bibliotheca-canon"
BATCH = 20
SOURCE = HERE / "source"
STAGES = ("drafts", "pending", "canon", "ledger")
TEXT_SUFFIXES = {".txt", ".md"}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def load_env() -> dict:
    """Read KEY=VALUE pairs from .env next to this script, then the environment."""
    config = {
        "DRUPAL_BASE_URL": "https://cloakofnazili.org",
        "ANYTHINGLLM_URL": "http://localhost:3001",
        "ANYTHINGLLM_API_KEY": "",
        "ANYTHINGLLM_WORKSPACE": "the-librarian",
    }
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip().strip('"').strip("'")
    for key in config:
        if os.environ.get(key):
            config[key] = os.environ[key]
    config["DRUPAL_BASE_URL"] = config["DRUPAL_BASE_URL"].rstrip("/")
    config["ANYTHINGLLM_URL"] = config["ANYTHINGLLM_URL"].rstrip("/")
    return config


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def http_json(url: str, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "canon-sync/1.0")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:300]
        raise SystemExit(f"HTTP {err.code} from {method} {url}: {detail}")
    except urllib.error.URLError as err:
        raise SystemExit(f"Could not reach {url}: {err.reason}")
    return json.loads(raw) if raw else {}


class AnythingLLM:
    def __init__(self, base: str, key: str, workspace: str):
        if not key:
            raise SystemExit("ANYTHINGLLM_API_KEY is not set. Create one in AnythingLLM "
                             "(Settings → Developer API) and put it in tools/canon-sync/.env")
        self.base = base + "/api/v1"
        self.headers = {"Authorization": f"Bearer {key}"}
        self.workspace = workspace

    def call(self, path: str, method: str = "GET", body: dict | None = None):
        return http_json(self.base + path, method, body, self.headers)

    def check(self) -> str:
        self.call("/auth")
        ws = self.call(f"/workspace/{self.workspace}").get("workspace")
        ws = ws[0] if isinstance(ws, list) and ws else ws
        if not ws:
            raise SystemExit(f"Workspace '{self.workspace}' not found. Set ANYTHINGLLM_WORKSPACE "
                             "to the workspace slug (shown in its URL).")
        return ws.get("name", self.workspace)

    def ensure_folder(self):
        try:
            self.call("/document/create-folder", "POST", {"name": FOLDER})
        except SystemExit as err:  # Already exists.
            if "exist" not in str(err).lower():
                raise

    def upload(self, text: str, metadata: dict) -> str:
        res = self.call("/document/raw-text", "POST", {"textContent": text, "metadata": metadata})
        docs = res.get("documents") or []
        if not res.get("success") or not docs:
            raise SystemExit(f"Upload failed for {metadata.get('title')}: {res.get('error')}")
        location = docs[0]["location"]
        target = f"{FOLDER}/{location.split('/')[-1]}"
        self.call("/document/move-files", "POST", {"files": [{"from": location, "to": target}]})
        return target

    def embed(self, adds: list[str], deletes: list[str]):
        for i in range(0, max(len(adds), len(deletes)), BATCH):
            self.call(f"/workspace/{self.workspace}/update-embeddings", "POST",
                      {"adds": adds[i:i + BATCH], "deletes": deletes[i:i + BATCH]})

    def remove_files(self, locations: list[str]):
        for i in range(0, len(locations), BATCH):
            self.call("/system/remove-documents", "DELETE", {"names": locations[i:i + BATCH]})


# --------------------------------------------------------------------------
# Building documents from canon
# --------------------------------------------------------------------------

def header(lines: list[tuple[str, object]]) -> str:
    out = []
    for label, value in lines:
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if v)
        if value not in (None, ""):
            out.append(f"{label}: {value}")
    return "\n".join(out)


def section(title: str, text) -> str:
    text = (text or "").strip()
    return f"\n\n## {title}\n\n{text}" if text else ""


def clean_title(value) -> str:
    return " ".join(str(value or "").split())


def split_list(value) -> list[str]:
    if isinstance(value, list):
        return [clean_title(v) for v in value if clean_title(v)]
    return [clean_title(v) for v in str(value or "").split("|") if clean_title(v)]


def item_line(item: dict, with_collection: str | None = None) -> str:
    kind = clean_title(item.get("type"))
    kind = kind[:-1] if kind.endswith("s") else kind
    extra = [kind] + ([clean_title(item["catalog_number"])] if item.get("catalog_number") else [])
    where = f" — {with_collection}" if with_collection else ""
    return f"- {clean_title(item.get('title'))} ({', '.join(extra)}){where}"


def build_arrangement(collections: list[dict], headings: list[dict], fragments: list[dict],
                      docs: dict[str, dict]) -> dict[str, list[str]]:
    """Add collection, heading and library section documents.

    Returns {item uuid: ["Collection › Heading", …]} so item headers can say
    where each item sits in the Library's arrangement.
    """
    tag_order = {clean_title(h["title"]): i for i, h in enumerate(headings) if h.get("kind") == "tags"}
    placement: dict[str, list[str]] = {}
    by_heading: dict[str, dict[str, list[dict]]] = {}
    shelved_collections: dict[str, list[str]] = {}

    for coll in collections:
        name = clean_title(coll["title"])
        contents = coll.get("contents") or []
        sections = split_list(coll.get("library_section"))
        for sec in sections:
            shelved_collections.setdefault(sec, []).append(name)

        groups: dict[str, list[dict]] = {}
        kinds = {clean_title(i.get("type")) for i in contents}
        for item in contents:
            item_headings = split_list(item.get("headings"))
            for h in item_headings:
                placement.setdefault(item["uuid"], []).append(f"{name} › {h}")
                by_heading.setdefault(h, {}).setdefault(name, []).append(item)
            keys = item_headings or ([clean_title(item.get("type"))] if len(kinds) > 1 else [""])
            for k in keys:
                groups.setdefault(k, []).append(item)
        ordered = sorted(groups, key=lambda k: (k not in tag_order, tag_order.get(k, 0), k))

        text = header([
            ("Title", name),
            ("Type", "Collection (Bibliotheca Nazilicum)"),
            ("Library section", sections),
            ("Holdings", len(contents) or None),
            ("Headings", [k for k in ordered if k in tag_order]),
            ("Source", coll.get("url")),
        ])
        text += section("Description", coll.get("description"))
        if contents:
            body = []
            for k in ordered:
                if k:
                    body.append(f"### {k}")
                body.extend(item_line(i) for i in groups[k])
                body.append("")
            text += section("Contents", "\n".join(body))
        docs[f"collection:{coll['uuid']}"] = {
            "uuid": coll["uuid"], "type": "collection", "title": name,
            "url": coll.get("url"), "changed": coll.get("changed"),
            "revision": None, "text": text,
        }

    shelved_items: dict[str, dict[str, list[str]]] = {}
    for frag in fragments:
        for sec in split_list(frag.get("library_locations")):
            shelved_items.setdefault(sec, {}).setdefault(
                clean_title(frag.get("collection")) or "Uncollected", []).append(clean_title(frag["title"]))

    for h in headings:
        name = clean_title(h["title"])
        if h.get("kind") == "tags":
            holdings = by_heading.get(name, {})
            text = header([
                ("Title", name),
                ("Type", "Heading (a contextual division used to arrange the Library's collections)"),
                ("Used in collections", list(holdings)),
                ("Holdings", sum(len(v) for v in holdings.values()) or None),
                ("Source", h.get("url")),
            ])
            text += section("Description", h.get("description"))
            body = []
            for coll_name, items in holdings.items():
                body.append(f"### {coll_name}")
                body.extend(item_line(i) for i in items)
                body.append("")
            text += section("Holdings", "\n".join(body))
            key, kind = f"heading:{h['uuid']}", "heading"
        else:
            colls = shelved_collections.get(name, [])
            items = shelved_items.get(name, {})
            text = header([
                ("Title", name),
                ("Type", "Library section (a room or wing of the Bibliotheca Nazilicum)"),
                ("Collections shelved here", colls),
                ("Source", h.get("url")),
            ])
            text += section("Description", h.get("description"))
            text += section("Collections shelved here", "\n".join(f"- {c}" for c in colls))
            body = []
            for coll_name, titles in items.items():
                body.append(f"### {coll_name}")
                body.extend(f"- {t}" for t in titles)
                body.append("")
            text += section("Items shelved here", "\n".join(body))
            key, kind = f"section:{h['uuid']}", "section"
        docs[key] = {
            "uuid": h["uuid"], "type": kind, "title": name,
            "url": h.get("url"), "changed": h.get("changed"),
            "revision": None, "text": text,
        }
    return placement


def build_documents(songs: list[dict], fragments: list[dict],
                    albums: list[dict] | None = None,
                    collections: list[dict] | None = None,
                    headings: list[dict] | None = None) -> dict[str, dict]:
    """Return {key: {uuid, type, title, url, changed, revision, text}}."""
    docs: dict[str, dict] = {}
    placement = build_arrangement(collections or [], headings or [], fragments, docs)

    for album in albums or []:
        tracks = album.get("tracks") or []
        text = header([
            ("Title", album["title"]),
            ("Type", "Album (IZIZOP vessel)"),
            ("Collection", album.get("collection")),
            ("Year of release", album.get("year")),
            ("Tracks", len(tracks) or None),
            ("Source", album.get("url")),
            ("Bandcamp", album.get("bandcamp_url")),
        ])
        text += section("Description", album.get("description"))
        text += section("Tracklist", "\n".join(
            f"{t.get('track')}. {(t.get('title') or '').strip()}" for t in tracks))
        text += section("Bandcamp pitch", album.get("bandcamp_pitch"))
        docs[f"album:{album['uuid']}"] = {
            "uuid": album["uuid"], "type": "album", "title": album["title"],
            "url": album.get("url"), "changed": album.get("changed"),
            "revision": None, "text": text,
        }

    for song in songs:
        notes = song.get("notes") or []
        song_label = f"{song['title']} (song {song.get('track')}, {song.get('vessel')})"
        text = header([
            ("Title", song["title"]),
            ("Type", "Song"),
            ("Vessel", song.get("vessel")),
            ("Song number", song.get("track")),
            ("Annotations", [n["title"] for n in notes]),
            ("Source", song.get("url")),
        ])
        text += section("Lyrics", song.get("lyrics"))
        text += section("Addendums", song.get("addendums"))
        text += section("Production notes", song.get("production_notes"))
        docs[f"song:{song['uuid']}"] = {
            "uuid": song["uuid"], "type": "song", "title": song["title"],
            "url": song.get("url"), "changed": song.get("changed"),
            "revision": song.get("revision"), "text": text,
        }

        for note in notes:
            text = header([
                ("Title", note["title"]),
                ("Type", "Scholarly annotation (marginalia)"),
                ("Annotates song", song_label),
                ("Song source", song.get("url")),
                ("Source", note.get("url")),
            ])
            text += "\n\n" + (note.get("note") or "").strip()
            docs[f"annotation:{note['uuid']}"] = {
                "uuid": note["uuid"], "type": "annotation", "title": note["title"],
                "url": note.get("url"), "changed": note.get("changed"),
                "revision": note.get("revision"), "text": text,
            }

    for frag in fragments:
        text = header([
            ("Title", frag["title"]),
            ("Type", "Text fragment"),
            ("Subtitle", frag.get("subtitle")),
            ("Attribution", frag.get("attribution")),
            ("Catalog number", frag.get("catalog_number")),
            ("Collection", frag.get("collection")),
            ("Library location", frag.get("library_locations")),
            ("Tags", frag.get("tags")),
            ("Placement", placement.get(frag["uuid"])),
            ("Source", frag.get("url")),
        ])
        text += "\n\n" + (frag.get("body") or "").strip()
        text += section("Notes", frag.get("notes"))
        docs[f"fragment:{frag['uuid']}"] = {
            "uuid": frag["uuid"], "type": "fragment", "title": frag["title"],
            "url": frag.get("url"), "changed": frag.get("changed"),
            "revision": frag.get("revision"), "text": text,
        }

    for doc in docs.values():
        doc["hash"] = hashlib.sha256(doc["text"].encode()).hexdigest()
    return docs


# --------------------------------------------------------------------------
# Writing archive (source/drafts → pending → canon)
# --------------------------------------------------------------------------

def ensure_stages():
    for stage in STAGES:
        (SOURCE / stage).mkdir(parents=True, exist_ok=True)


def text_files(stage: str) -> list[Path]:
    folder = SOURCE / stage
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in TEXT_SUFFIXES)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def manuscript_doc(path: Path) -> dict:
    """Turn a file in source/canon into a document. A first line 'Title: …' sets the title."""
    body = read_text(path).strip()
    first, _, rest = body.partition("\n")
    if first.lower().startswith("title:"):
        title, body = first.split(":", 1)[1].strip(), rest.strip()
    else:
        title = path.stem.replace("-", " ").replace("_", " ").strip()
        if title.islower():
            title = title.title()
    text = header([
        ("Title", title),
        ("Type", "Manuscript (writing archive)"),
        ("Source file", f"canon/{path.name}"),
    ]) + "\n\n" + body
    changed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    return {"uuid": None, "type": "manuscript", "title": title, "url": None, "changed": changed,
            "revision": None, "text": text, "hash": hashlib.sha256(text.encode()).hexdigest()}


def build_manuscripts(db: sqlite3.Connection) -> dict[str, dict]:
    """Files in source/canon, plus manuscripts already ingested whose files were cleared by gc."""
    docs = {f"manuscript:{p.name}": manuscript_doc(p) for p in text_files("canon")}
    for row in db.execute("SELECT * FROM docs WHERE type = 'manuscript'"):
        docs.setdefault(row["key"], dict(row))
    return docs


def ledger(event: str, name: str, detail: str = ""):
    (SOURCE / "ledger").mkdir(parents=True, exist_ok=True)
    with open(SOURCE / "ledger" / "ingest.log", "a", encoding="utf-8") as log:
        log.write(f"{now()}  {event:9} {name}{'  ' + detail if detail else ''}\n")


# --------------------------------------------------------------------------
# Local state
# --------------------------------------------------------------------------

def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS docs (
          key TEXT PRIMARY KEY, uuid TEXT, type TEXT, title TEXT, url TEXT,
          changed TEXT, revision INTEGER, hash TEXT, location TEXT,
          text TEXT, synced_at TEXT);
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, title TEXT,
          event TEXT, revision INTEGER, changed TEXT, text TEXT, recorded_at TEXT);
    """)
    return db


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def fetch_canon(cfg: dict) -> tuple[dict[str, dict], set[str]]:
    base = cfg["DRUPAL_BASE_URL"]
    print(f"Reading canon from {base} …")
    songs = http_json(f"{base}/izizop/songs?_format=json")
    fragments = http_json(f"{base}/izizop/fragments?_format=json")
    skipped: set[str] = set()

    def optional(path: str, types: set[str]) -> list[dict]:
        try:
            return http_json(f"{base}/izizop/{path}?_format=json")
        except SystemExit as err:
            if "HTTP 404" not in str(err):
                raise
            print(f"  izizop/{path} is not on this site yet; skipping it (nothing synced from it is removed).")
            skipped.update(types)
            return []

    albums = optional("albums", {"album"})
    collections = optional("collections", {"collection"})
    headings = optional("headings", {"heading", "section"})
    docs = build_documents(songs, fragments, albums, collections, headings)
    counts = {}
    for d in docs.values():
        counts[d["type"]] = counts.get(d["type"], 0) + 1
    print("  " + ", ".join(f"{n} {t}s" for t, n in sorted(counts.items())))
    return docs, skipped


def plan(db: sqlite3.Connection, docs: dict[str, dict], skipped: set[str] = frozenset()):
    stored = {row["key"]: row for row in db.execute("SELECT * FROM docs")}
    new = [k for k in docs if k not in stored]
    changed = [k for k in docs if k in stored and stored[k]["hash"] != docs[k]["hash"]]
    removed = [k for k in stored if k not in docs and stored[k]["type"] not in skipped]
    return stored, new, changed, removed


def cmd_sync(cfg: dict, dry_run: bool, rebuild: bool):
    db = open_db()
    docs, skipped = fetch_canon(cfg)
    manuscripts = build_manuscripts(db)
    waiting = len(text_files("canon"))
    print(f"  {len(manuscripts)} manuscripts ({waiting} file{'s' * (waiting != 1)} in source/canon)")
    docs.update(manuscripts)
    llm = None if dry_run else AnythingLLM(cfg["ANYTHINGLLM_URL"], cfg["ANYTHINGLLM_API_KEY"],
                                           cfg["ANYTHINGLLM_WORKSPACE"])
    if llm:
        print(f"Workspace: {llm.check()}")

    if rebuild:
        old = [r["location"] for r in db.execute("SELECT location FROM docs") if r["location"]]
        print(f"Rebuild: removing {len(old)} previously synced documents.")
        if llm and old:
            llm.embed([], old)
            llm.remove_files(old)
        if not dry_run:
            db.execute("DELETE FROM docs")
            db.commit()

    stored, new, changed, removed = plan(db, docs, skipped)
    print(f"Plan: {len(new)} new, {len(changed)} changed, {len(removed)} removed, "
          f"{len(docs) - len(new) - len(changed)} unchanged.")
    for label, keys, source in (("new", new, docs), ("changed", changed, docs), ("removed", removed, stored)):
        for k in keys[:15]:
            print(f"  {label:8} {source[k]['title']}")
        if len(keys) > 15:
            print(f"  … and {len(keys) - 15} more {label}")
    if dry_run or not (new or changed or removed):
        if not dry_run:
            print("Canon is already up to date.")
        return

    llm.ensure_folder()
    adds, deletes, files_to_remove = [], [], []
    for i, key in enumerate(new + changed, 1):
        d = docs[key]
        meta = {"title": d["title"], "docSource": "Bibliotheca Nazilicum canon",
                "description": d.get("url") or "", "docAuthor": d["type"]}
        location = llm.upload(d["text"], meta)
        adds.append(location)
        if key in stored:
            deletes.append(stored[key]["location"])
            files_to_remove.append(stored[key]["location"])
        print(f"\r  Uploaded {i}/{len(new) + len(changed)}", end="", flush=True)
        d["location"] = location
    if new or changed:
        print()
    for key in removed:
        deletes.append(stored[key]["location"])
        files_to_remove.append(stored[key]["location"])

    print(f"Embedding {len(adds)} and removing {len(deletes)} in the workspace …")
    llm.embed(adds, [d for d in deletes if d])
    if files_to_remove:
        llm.remove_files([f for f in files_to_remove if f])

    stamp = now()
    for key in new + changed:
        d = docs[key]
        if key in stored:
            old = stored[key]
            db.execute("INSERT INTO history (key, title, event, revision, changed, text, recorded_at) "
                       "VALUES (?, ?, 'replaced', ?, ?, ?, ?)",
                       (key, old["title"], old["revision"], old["changed"], old["text"], stamp))
        else:
            db.execute("INSERT INTO history (key, title, event, revision, changed, text, recorded_at) "
                       "VALUES (?, ?, 'added', ?, ?, NULL, ?)",
                       (key, d["title"], d["revision"], d["changed"], stamp))
        db.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (key, d["uuid"], d["type"], d["title"], d["url"], d["changed"],
                    d["revision"], d["hash"], d["location"], d["text"], stamp))
    for key in removed:
        old = stored[key]
        db.execute("INSERT INTO history (key, title, event, revision, changed, text, recorded_at) "
                   "VALUES (?, ?, 'removed', ?, ?, ?, ?)",
                   (key, old["title"], old["revision"], old["changed"], old["text"], stamp))
        db.execute("DELETE FROM docs WHERE key = ?", (key,))
    db.commit()
    for key in new + changed:
        if docs[key]["type"] == "manuscript" and (SOURCE / "canon" / key.split(":", 1)[1]).exists():
            ledger("ingested", f"canon/{key.split(':', 1)[1]}",
                   f"\"{docs[key]['title']}\" sha256:{docs[key]['hash'][:12]}")
    print("Done. Canon is up to date.")
    if any(docs[k]["type"] == "manuscript" for k in new + changed):
        print("Run 'python3 canon_sync.py gc' to clear ingested files from source/.")


def cmd_gc(dry_run: bool):
    """Delete ingested files from source/canon, and their earlier versions in drafts/ and pending/."""
    db = open_db()
    ensure_stages()
    stored = {r["key"]: r for r in db.execute("SELECT key, hash FROM docs WHERE type = 'manuscript'")}
    clear, keep = [], []
    for path in text_files("canon"):
        row = stored.get(f"manuscript:{path.name}")
        if row and row["hash"] == manuscript_doc(path)["hash"]:
            clear.append(path)
        else:
            keep.append((path, "changed since it was ingested" if row else "not ingested yet"))
    stems = {Path(k.split(":", 1)[1]).stem.lower() for k in stored}
    for stage in ("pending", "drafts"):
        clear += [p for p in text_files(stage) if p.stem.lower() in stems]

    if not clear and not keep:
        print("source/ is already clear.")
        return
    for path in clear:
        print(f"  {'would clear' if dry_run else 'clear'}  {path.relative_to(SOURCE)}")
    for path, why in keep:
        print(f"  keep         {path.relative_to(SOURCE)}  ({why}; run sync first)")
    if dry_run or not clear:
        return
    for path in clear:
        path.unlink()
        ledger("cleared", str(path.relative_to(SOURCE)))
    print(f"Cleared {len(clear)} file{'s' * (len(clear) != 1)}. Their text stays in AnythingLLM "
          f"and in {DB_PATH.name}.")


def cmd_retract(cfg: dict, name: str, dry_run: bool):
    """Take an ingested manuscript out of AnythingLLM (its file may already be cleared)."""
    db = open_db()
    key = f"manuscript:{Path(name).name}"
    row = db.execute("SELECT * FROM docs WHERE key = ?", (key,)).fetchone()
    if not row:
        names = [r["key"].split(":", 1)[1] for r in db.execute(
            "SELECT key FROM docs WHERE type = 'manuscript' ORDER BY key")]
        raise SystemExit(f"No ingested manuscript named '{Path(name).name}'. Ingested: "
                         + (", ".join(names) or "none"))
    if (SOURCE / "canon" / Path(name).name).exists():
        raise SystemExit(f"source/canon/{Path(name).name} still exists, so the next sync would add it "
                         "back. Move or delete it first.")
    print(f"{'Would retract' if dry_run else 'Retracting'} \"{row['title']}\" ({row['location']})")
    if dry_run:
        return
    llm = AnythingLLM(cfg["ANYTHINGLLM_URL"], cfg["ANYTHINGLLM_API_KEY"], cfg["ANYTHINGLLM_WORKSPACE"])
    llm.check()
    if row["location"]:
        llm.embed([], [row["location"]])
        llm.remove_files([row["location"]])
    db.execute("INSERT INTO history (key, title, event, revision, changed, text, recorded_at) "
               "VALUES (?, ?, 'removed', ?, ?, ?, ?)",
               (key, row["title"], row["revision"], row["changed"], row["text"], now()))
    db.execute("DELETE FROM docs WHERE key = ?", (key,))
    db.commit()
    ledger("retracted", f"canon/{Path(name).name}", f"\"{row['title']}\"")
    print("Done.")


def cmd_status(cfg: dict):
    db = open_db()
    rows = db.execute("SELECT type, COUNT(*) n, MAX(synced_at) last FROM docs GROUP BY type").fetchall()
    if not rows:
        print("Nothing synced yet. Run: python3 canon_sync.py")
        return
    for r in rows:
        print(f"{r['type']:11} {r['n']:4} documents, last synced {r['last']}")
    print(f"State file: {DB_PATH}")


def cmd_changes(limit: int):
    db = open_db()
    rows = db.execute("SELECT * FROM history WHERE event != 'added' OR recorded_at > "
                      "(SELECT MIN(recorded_at) FROM history) ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        print("No canon changes recorded since the first sync.")
        return
    for r in rows:
        print(f"{r['recorded_at']}  {r['event']:8}  {r['title']}")


def main():
    parser = argparse.ArgumentParser(description="Sync Drupal canon into AnythingLLM.")
    parser.add_argument("command", nargs="?", default="sync",
                        choices=["sync", "status", "changes", "gc", "retract"])
    parser.add_argument("name", nargs="?", help="file name for 'retract' (e.g. my-story.txt)")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    parser.add_argument("--rebuild", action="store_true", help="remove all synced documents and re-add them")
    parser.add_argument("--limit", type=int, default=30, help="rows to show for 'changes'")
    args = parser.parse_args()
    cfg = load_env()
    if args.command == "status":
        cmd_status(cfg)
    elif args.command == "changes":
        cmd_changes(args.limit)
    elif args.command == "gc":
        cmd_gc(args.dry_run)
    elif args.command == "retract":
        if not args.name:
            parser.error("retract needs a file name, e.g. retract my-story.txt")
        cmd_retract(cfg, args.name, args.dry_run)
    else:
        cmd_sync(cfg, args.dry_run, args.rebuild)


if __name__ == "__main__":
    main()
