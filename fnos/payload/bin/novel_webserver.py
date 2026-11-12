#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novelmgt NAS 静态服务器 + 用户状态 API（fnOS 应用内置）。

- /            → Flutter web 构建产物（ui/ 目录）
- /db/<文件名> → 指定的单文件 SQLite 书库，HTTP Range（206）按需只读
- /api/state/* → 用户状态（进度/书签/笔记）读写，存独立的状态 SQLite
                （--state-db 指定；NAS 部署时按设备本地存放，不按浏览器隔离）

浏览器端书架/正文直读书库文件；阅读状态走这个极小的 JSON API。
仅用标准库，Python 3.6+ 可运行。

用法：
    python3 novel_webserver.py --web-dir <ui目录> --db <novelmgt.db路径> \
        [--state-db <novelmgt_state.db路径>] [--port 12702]
"""

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bookId INTEGER UNIQUE NOT NULL,
  chapterId INTEGER NOT NULL,
  scrollPosition INTEGER DEFAULT 0,
  lastReadAt TEXT,
  createdAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookmarks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bookId INTEGER NOT NULL,
  chapterId INTEGER NOT NULL,
  position INTEGER DEFAULT 0,
  title TEXT,
  note TEXT,
  createdAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bookId INTEGER NOT NULL,
  chapterId INTEGER NOT NULL,
  position INTEGER DEFAULT 0,
  content TEXT NOT NULL DEFAULT '',
  createdAt TEXT NOT NULL,
  updatedAt TEXT
);
"""


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class StateStore(object):
    """用户状态库（独立于只读书库；单进程多线程，每请求独立连接）"""

    def __init__(self, path):
        self.path = path
        con = sqlite3.connect(path)
        con.executescript(STATE_SCHEMA)
        con.commit()
        con.close()

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row_to_dict(row):
        return dict(row) if row is not None else None

    # ---- 进度 ----
    def get_progress(self, book_id):
        with self._con() as con:
            if book_id:
                row = con.execute(
                    "SELECT * FROM reading_progress WHERE bookId=?", (book_id,)
                ).fetchone()
                return self._row_to_dict(row)
            rows = con.execute(
                "SELECT * FROM reading_progress ORDER BY lastReadAt DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_progress(self, book_id, chapter_id, scroll_position):
        now = now_iso()
        with self._con() as con:
            existing = con.execute(
                "SELECT id, createdAt FROM reading_progress WHERE bookId=?",
                (book_id,),
            ).fetchone()
            if existing:
                con.execute(
                    "UPDATE reading_progress SET chapterId=?, scrollPosition=?,"
                    " lastReadAt=? WHERE bookId=?",
                    (chapter_id, scroll_position, now, book_id),
                )
                created = existing["createdAt"]
            else:
                con.execute(
                    "INSERT INTO reading_progress"
                    " (bookId, chapterId, scrollPosition, lastReadAt, createdAt)"
                    " VALUES (?,?,?,?,?)",
                    (book_id, chapter_id, scroll_position, now, now),
                )
                created = now
            row = con.execute(
                "SELECT * FROM reading_progress WHERE bookId=?", (book_id,)
            ).fetchone()
            row = self._row_to_dict(row)
            row["createdAt"] = created
            return row

    # ---- 书签 ----
    def get_bookmarks(self, book_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM bookmarks WHERE bookId=? ORDER BY createdAt DESC",
                (book_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def add_bookmark(self, b):
        now = now_iso()
        with self._con() as con:
            cur = con.execute(
                "INSERT INTO bookmarks"
                " (bookId, chapterId, position, title, note, createdAt)"
                " VALUES (?,?,?,?,?,?)",
                (b.get("bookId"), b.get("chapterId"), b.get("position", 0),
                 b.get("title"), b.get("note"), now),
            )
            row = con.execute(
                "SELECT * FROM bookmarks WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return self._row_to_dict(row)

    def delete_bookmark(self, bookmark_id):
        with self._con() as con:
            cur = con.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
            return cur.rowcount > 0

    # ---- 笔记 ----
    def get_notes(self, book_id=None, chapter_id=None):
        with self._con() as con:
            if book_id is not None:
                rows = con.execute(
                    "SELECT * FROM notes WHERE bookId=? ORDER BY createdAt DESC",
                    (book_id,),
                ).fetchall()
            elif chapter_id is not None:
                rows = con.execute(
                    "SELECT * FROM notes WHERE chapterId=? ORDER BY position ASC",
                    (chapter_id,),
                ).fetchall()
            else:
                rows = []
            return [dict(r) for r in rows]

    def add_note(self, n):
        now = now_iso()
        with self._con() as con:
            cur = con.execute(
                "INSERT INTO notes"
                " (bookId, chapterId, position, content, createdAt, updatedAt)"
                " VALUES (?,?,?,?,?,?)",
                (n.get("bookId"), n.get("chapterId"), n.get("position", 0),
                 n.get("content", ""), now, now),
            )
            row = con.execute(
                "SELECT * FROM notes WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return self._row_to_dict(row)

    def update_note(self, note_id, n):
        now = now_iso()
        with self._con() as con:
            con.execute(
                "UPDATE notes SET content=?, position=?, updatedAt=? WHERE id=?",
                (n.get("content", ""), n.get("position", 0), now, note_id),
            )
            row = con.execute(
                "SELECT * FROM notes WHERE id=?", (note_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def delete_note(self, note_id):
        with self._con() as con:
            cur = con.execute("DELETE FROM notes WHERE id=?", (note_id,))
            return cur.rowcount > 0


class NovelHandler(SimpleHTTPRequestHandler):
    db_path = None      # 只读书库（由 main() 注入）
    state = None        # StateStore（main() 注入；None 时 /api/state 返回 503）

    # ================= 状态 API =================

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _handle_state_get(self, path, query):
        st = self.state
        if st is None:
            return self._send_json({"error": "state db not configured"}, 503)
        if path == "/api/state/progress":
            book_id = query.get("bookId", [None])[0]
            return self._send_json(
                {"row": st.get_progress(int(book_id)) if book_id else None,
                 "rows": [] if book_id else st.get_progress(None)})
        if path == "/api/state/bookmarks":
            book_id = query.get("bookId", [None])[0]
            if not book_id:
                return self._send_json({"error": "bookId required"}, 400)
            return self._send_json({"rows": st.get_bookmarks(int(book_id))})
        if path == "/api/state/notes":
            book_id = query.get("bookId", [None])[0]
            chapter_id = query.get("chapterId", [None])[0]
            return self._send_json({"rows": st.get_notes(
                int(book_id) if book_id else None,
                int(chapter_id) if chapter_id else None)})
        return self._send_json({"error": "not found"}, 404)

    def _handle_state_post(self, path):
        st = self.state
        if st is None:
            return self._send_json({"error": "state db not configured"}, 503)
        body = self._read_body_json()
        if path == "/api/state/progress":
            row = st.upsert_progress(int(body["bookId"]),
                                     int(body["chapterId"]),
                                     int(body.get("scrollPosition", 0)))
            return self._send_json({"row": row})
        if path == "/api/state/bookmarks":
            row = st.add_bookmark(body)
            return self._send_json({"row": row})
        if path == "/api/state/bookmarks/delete":
            return self._send_json({"ok": st.delete_bookmark(int(body["id"]))})
        if path == "/api/state/notes":
            row = st.add_note(body)
            return self._send_json({"row": row})
        if path == "/api/state/notes/update":
            row = st.update_note(int(body["id"]), body)
            return self._send_json({"row": row})
        if path == "/api/state/notes/delete":
            return self._send_json({"ok": st.delete_note(int(body["id"]))})
        return self._send_json({"error": "not found"}, 404)

    # ================= 路由 =================

    def _is_db_request(self):
        return self.db_path and self.path.split("?")[0].startswith("/db/")

    def _is_state_api(self):
        return self.path.split("?")[0].startswith("/api/state/")

    def do_GET(self):
        if self._is_state_api():
            self._handle_state_get(
                self.path.split("?")[0], parse_qs(urlparse(self.path).query))
        elif self._is_db_request():
            self._serve_db(include_body=True)
        else:
            super().do_GET()

    def do_HEAD(self):
        if self._is_state_api():
            self._handle_state_get(
                self.path.split("?")[0], parse_qs(urlparse(self.path).query))
        elif self._is_db_request():
            self._serve_db(include_body=False)
        else:
            super().do_HEAD()

    def do_POST(self):
        if self._is_state_api():
            self._handle_state_post(self.path.split("?")[0])
        else:
            self.send_error(405)

    # ================= 只读书库 =================

    def _serve_db(self, include_body):
        name = unquote(self.path.split("?")[0].split("/db/", 1)[1])
        if "/" in name or "\\" in name or not name:
            self.send_error(404)
            return
        if not os.path.isfile(self.db_path):
            self.send_error(404, "db not found")
            return
        size = os.path.getsize(self.db_path)

        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = RANGE_RE.match(rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                status = 206
            else:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
        end = min(end, size - 1)
        length = end - start + 1

        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if not include_body:
            return
        with open(self.db_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionAbortedError, BrokenPipeError):
                    return
                remaining -= len(chunk)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("[novelmgt] %s - %s\n" % (self.address_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-dir", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--state-db", default=None,
                    help="用户状态库路径（进度/书签/笔记）；缺省时 /api/state 返回 503")
    ap.add_argument("--port", type=int, default=12702)
    args = ap.parse_args()

    if not os.path.isdir(args.web_dir):
        print("web 目录不存在: %s" % args.web_dir)
        return 1
    if not os.path.isfile(args.db):
        print("书库文件不存在: %s" % args.db)
        return 1

    if args.state_db:
        state_dir = os.path.dirname(os.path.abspath(args.state_db))
        if state_dir and not os.path.isdir(state_dir):
            os.makedirs(state_dir, exist_ok=True)
        NovelHandler.state = StateStore(args.state_db)
        sys.stderr.write("[novelmgt] state db: %s\n" % args.state_db)

    handler = partial(NovelHandler, directory=args.web_dir)
    NovelHandler.db_path = os.path.abspath(args.db)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    sys.stderr.write("[novelmgt] serving %s + %s on :%d\n"
                     % (args.web_dir, args.db, args.port))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
