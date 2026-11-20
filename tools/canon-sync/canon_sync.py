#!/usr/bin/env python3
"""Sync Bibliotheca Nazilicum canon from the Drupal API into AnythingLLM.

Reads the canon endpoints (izizop/songs, izizop/fragments), turns every song,
annotation and text fragment into its own document with a citation header,
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


def build_documents(songs: list[dict], fragments: list[dict]) -> dict[str, dict]:
    """Return {key: {uuid, type, title, url, changed, revision, text}}."""
    docs: dict[str, dict] = {}

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

def fetch_canon(cfg: dict) -> dict[str, dict]:
    base = cfg["DRUPAL_BASE_URL"]
    print(f"Reading canon from {base} …")
    songs = http_json(f"{base}/izizop/songs?_format=json")
    fragments = http_json(f"{base}/izizop/fragments?_format=json")
    docs = build_documents(songs, fragments)
    counts = {}
    for d in docs.values():
        counts[d["type"]] = counts.get(d["type"], 0) + 1
    print("  " + ", ".join(f"{n} {t}s" for t, n in sorted(counts.items())))
    return docs


def plan(db: sqlite3.Connection, docs: dict[str, dict]):
    stored = {row["key"]: row for row in db.execute("SELECT * FROM docs")}
    new = [k for k in docs if k not in stored]
    changed = [k for k in docs if k in stored and stored[k]["hash"] != docs[k]["hash"]]
    removed = [k for k in stored if k not in docs]
    return stored, new, changed, removed


def cmd_sync(cfg: dict, dry_run: bool, rebuild: bool):
    db = open_db()
    docs = fetch_canon(cfg)
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

    stored, new, changed, removed = plan(db, docs)
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
    print("Done. Canon is up to date.")


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
    parser.add_argument("command", nargs="?", default="sync", choices=["sync", "status", "changes"])
    parser.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    parser.add_argument("--rebuild", action="store_true", help="remove all synced documents and re-add them")
    parser.add_argument("--limit", type=int, default=30, help="rows to show for 'changes'")
    args = parser.parse_args()
    cfg = load_env()
    if args.command == "status":
        cmd_status(cfg)
    elif args.command == "changes":
        cmd_changes(args.limit)
    else:
        cmd_sync(cfg, args.dry_run, args.rebuild)


if __name__ == "__main__":
    main()
