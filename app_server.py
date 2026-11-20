#!/usr/bin/env python3
"""Small standard-library ComChat server for private, self-hosted previews."""

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("COMCHAT_DATABASE", ROOT / "data" / "comchat.sqlite3"))
SESSION_SECONDS = 14 * 24 * 60 * 60
PASSWORD_ITERATIONS = 260_000
MAX_BODY = 24 * 1024
AUTH_RATE_WINDOW = 60
AUTH_RATE_LIMIT = 12
AUTH_ATTEMPTS = {}
AUTH_RATE_LOCK = threading.Lock()
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")
ID_RE = re.compile(r"^[a-f0-9]{32}$")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/chat-state.mjs": ("chat-state.mjs", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def new_id():
    return uuid.uuid4().hex


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)


class ComChat:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.initialize()
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass

    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self):
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    google_sub TEXT UNIQUE,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS communities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_id TEXT NOT NULL REFERENCES users(id),
                    invite_code TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS community_members (
                    community_id TEXT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    joined_at INTEGER NOT NULL,
                    PRIMARY KEY (community_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS spaces (
                    id TEXT PRIMARY KEY,
                    community_id TEXT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind = 'text'),
                    created_at INTEGER NOT NULL,
                    UNIQUE (community_id, name)
                );
                CREATE TABLE IF NOT EXISTS dm_conversations (
                    id TEXT PRIMARY KEY,
                    user_low TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    user_high TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    UNIQUE (user_low, user_high),
                    CHECK (user_low < user_high)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    space_id TEXT REFERENCES spaces(id) ON DELETE CASCADE,
                    dm_id TEXT REFERENCES dm_conversations(id) ON DELETE CASCADE,
                    sender_id TEXT NOT NULL REFERENCES users(id),
                    text TEXT NOT NULL,
                    reply_to INTEGER REFERENCES messages(id),
                    created_at INTEGER NOT NULL,
                    CHECK ((space_id IS NULL) != (dm_id IS NULL))
                );
                CREATE INDEX IF NOT EXISTS messages_space_order ON messages(space_id, id);
                CREATE INDEX IF NOT EXISTS messages_dm_order ON messages(dm_id, id);
                CREATE TABLE IF NOT EXISTS reactions (
                    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    emoji TEXT NOT NULL,
                    PRIMARY KEY (message_id, user_id, emoji)
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "google_sub" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_google_sub ON users(google_sub)")
            db.execute(
                "INSERT OR IGNORE INTO app_settings(key, value) "
                "SELECT 'admin_user_id', id FROM users ORDER BY created_at, rowid LIMIT 1"
            )
            db.execute("DELETE FROM spaces WHERE kind = 'voice'")

    @staticmethod
    def _required_text(data, key, label, maximum):
        value = data.get(key)
        if not isinstance(value, str):
            raise APIError(400, f"{label} is required.")
        value = value.strip()
        if not value or len(value) > maximum:
            raise APIError(400, f"{label} must be between 1 and {maximum} characters.")
        return value

    def _new_session(self, db, user_id):
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        db.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time()) + SESSION_SECONDS),
        )
        return token

    def _user(self, db, user_id):
        row = db.execute(
            "SELECT u.id, u.username, u.display_name, "
            "EXISTS(SELECT 1 FROM app_settings WHERE key = 'admin_user_id' AND value = u.id) AS is_admin "
            "FROM users u WHERE u.id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def _session_user(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute(
                "SELECT u.id, u.username, u.display_name, "
                "EXISTS(SELECT 1 FROM app_settings WHERE key = 'admin_user_id' AND value = u.id) AS is_admin "
                "FROM sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
                (digest, int(time.time())),
            ).fetchone()
            return dict(row) if row else None

    def _require_user(self, token):
        user = self._session_user(token)
        if not user:
            raise APIError(401, "Please sign in to continue.")
        return user

    @staticmethod
    def _require_admin(db, user_id):
        admin = db.execute(
            "SELECT 1 FROM app_settings WHERE key = 'admin_user_id' AND value = ?",
            (user_id,),
        ).fetchone()
        if not admin:
            raise APIError(403, "Only the ComChat server administrator can manage communities.")

    def _require_community(self, db, community_id, user_id):
        row = db.execute(
            "SELECT c.id, c.name, c.owner_id, c.invite_code FROM communities c "
            "JOIN community_members m ON m.community_id = c.id "
            "WHERE c.id = ? AND m.user_id = ?",
            (community_id, user_id),
        ).fetchone()
        if not row:
            raise APIError(404, "Community not found.")
        return row

    def _require_space(self, db, space_id, user_id):
        row = db.execute(
            "SELECT s.id, s.community_id, s.name, s.kind "
            "FROM spaces s JOIN community_members m ON m.community_id = s.community_id "
            "WHERE s.id = ? AND m.user_id = ?",
            (space_id, user_id),
        ).fetchone()
        if not row:
            raise APIError(404, "Space not found.")
        return row

    def _require_dm(self, db, dm_id, user_id):
        row = db.execute(
            "SELECT id, user_low, user_high FROM dm_conversations "
            "WHERE id = ? AND (user_low = ? OR user_high = ?)",
            (dm_id, user_id, user_id),
        ).fetchone()
        if not row:
            raise APIError(404, "Direct message not found.")
        return row

    @staticmethod
    def _cookie(token, secure=False, clear=False):
        age = 0 if clear else SESSION_SECONDS
        value = "" if clear else token
        attributes = f"comchat_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}"
        return attributes + ("; Secure" if secure else "")

    def dispatch(self, method, path, query, data, token):
        try:
            return self._dispatch(method, path, query, data, token)
        except APIError as error:
            return error.status, {"error": str(error)}, {}
        except sqlite3.IntegrityError:
            return 409, {"error": "That name or item is already in use."}, {}

    def _dispatch(self, method, path, query, data, token):
        secure = os.environ.get("COMCHAT_SECURE_COOKIES") == "1"
        if path == "/api/register" and method == "POST":
            username = self._required_text(data, "username", "Username", 24)
            if not USERNAME_RE.fullmatch(username):
                raise APIError(400, "Use 3–24 letters, numbers, or underscores for your username.")
            password = data.get("password")
            if not isinstance(password, str) or not 12 <= len(password) <= 128:
                raise APIError(400, "Password must be 12–128 characters.")
            display_name = self._required_text(data, "display_name", "Display name", 32)
            salt = secrets.token_bytes(16)
            user_id = new_id()
            with self.connect() as db:
                db.execute(
                    "INSERT INTO users(id, username, display_name, password_salt, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, username, display_name, salt, password_digest(password, salt), int(time.time())),
                )
                db.execute(
                    "INSERT OR IGNORE INTO app_settings(key, value) VALUES ('admin_user_id', ?)",
                    (user_id,),
                )
                session = self._new_session(db, user_id)
            return 201, {"user": self._session_user(session)}, {"Set-Cookie": self._cookie(session, secure)}

        if path == "/api/login" and method == "POST":
            username = self._required_text(data, "username", "Username", 24)
            password = data.get("password")
            if not isinstance(password, str) or len(password) > 128:
                raise APIError(401, "Username or password is incorrect.")
            with self.connect() as db:
                row = db.execute(
                    "SELECT id, password_salt, password_hash FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                ).fetchone()
                if not row or not hmac.compare_digest(password_digest(password, row["password_salt"]), row["password_hash"]):
                    raise APIError(401, "Username or password is incorrect.")
                session = self._new_session(db, row["id"])
            return 200, {"user": self._session_user(session)}, {"Set-Cookie": self._cookie(session, secure)}

        if path == "/api/google" and method == "POST":
            credential = data.get("credential")
            if not isinstance(credential, str) or not credential or len(credential) > 8192:
                raise APIError(400, "A Google sign-in credential is required.")
            claims = self._verify_google_credential(credential)
            google_sub = claims["sub"]
            claimed_name = claims.get("name")
            display_name = claimed_name.strip()[:32] if isinstance(claimed_name, str) else ""
            display_name = display_name or "Google user"
            with self.connect() as db:
                row = db.execute("SELECT id FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
                if row:
                    user_id = row["id"]
                else:
                    user_id = new_id()
                    username = f"g{secrets.token_hex(8)}"
                    salt = secrets.token_bytes(16)
                    db.execute(
                        "INSERT INTO users(id, username, display_name, password_salt, password_hash, google_sub, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (user_id, username, display_name, salt, b"", google_sub, int(time.time())),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO app_settings(key, value) VALUES ('admin_user_id', ?)",
                        (user_id,),
                    )
                session = self._new_session(db, user_id)
            return 200, {"user": self._session_user(session)}, {"Set-Cookie": self._cookie(session, secure)}

        if path == "/api/logout" and method == "POST":
            user = self._require_user(token)
            with self.connect() as db:
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return 200, {"ok": True}, {"Set-Cookie": self._cookie("", secure, clear=True)}

        user = self._require_user(token)
        user_id = user["id"]

        if path == "/api/me" and method == "GET":
            return 200, {"user": user}, {}
        if path == "/api/profile" and method == "PATCH":
            display_name = self._required_text(data, "display_name", "Display name", 32)
            with self.connect() as db:
                db.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id))
            return 200, {"user": {**user, "display_name": display_name}}, {}

        if path == "/api/communities" and method == "GET":
            with self.connect() as db:
                rows = db.execute(
                    "SELECT c.id, c.name, c.owner_id FROM communities c "
                    "JOIN community_members m ON m.community_id = c.id "
                    "WHERE m.user_id = ? ORDER BY c.created_at, c.name",
                    (user_id,),
                ).fetchall()
                result = []
                for community in rows:
                    spaces = db.execute(
                        "SELECT id, name, kind FROM spaces WHERE community_id = ? ORDER BY created_at, id",
                        (community["id"],),
                    ).fetchall()
                    members = db.execute(
                        "SELECT u.username, u.display_name FROM community_members m "
                        "JOIN users u ON u.id = m.user_id WHERE m.community_id = ? ORDER BY m.joined_at",
                        (community["id"],),
                    ).fetchall()
                    result.append({
                        "id": community["id"],
                        "name": community["name"],
                        "owner": community["owner_id"] == user_id,
                        "spaces": [dict(row) for row in spaces],
                        "members": [dict(row) for row in members],
                    })
            return 200, {"communities": result}, {}

        if path == "/api/communities" and method == "POST":
            with self.connect() as db:
                self._require_admin(db, user_id)
            name = self._required_text(data, "name", "Community name", 48)
            community_id, general_id = new_id(), new_id()
            invite_code = secrets.token_urlsafe(9)
            now = int(time.time())
            with self.connect() as db:
                db.execute(
                    "INSERT INTO communities(id, name, owner_id, invite_code, created_at) VALUES (?, ?, ?, ?, ?)",
                    (community_id, name, user_id, invite_code, now),
                )
                db.execute(
                    "INSERT INTO community_members(community_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (community_id, user_id, now),
                )
                db.execute(
                    "INSERT INTO spaces(id, community_id, name, kind, created_at) VALUES (?, ?, 'general', 'text', ?)",
                    (general_id, community_id, now),
                )
            return 201, {
                "community": {
                    "id": community_id,
                    "name": name,
                    "owner": True,
                    "invite_code": invite_code,
                    "spaces": [{"id": general_id, "name": "general", "kind": "text"}],
                    "members": [{"username": user["username"], "display_name": user["display_name"]}],
                }
            }, {}

        match = re.fullmatch(r"/api/communities/([a-f0-9]{32})", path)
        if match and method == "PATCH":
            name = self._required_text(data, "name", "Community name", 48)
            with self.connect() as db:
                self._require_admin(db, user_id)
                updated = db.execute("UPDATE communities SET name = ? WHERE id = ?", (name, match.group(1)))
                if updated.rowcount == 0:
                    raise APIError(404, "Community not found.")
            return 200, {"community": {"id": match.group(1), "name": name}}, {}

        if path == "/api/communities/join" and method == "POST":
            code = self._required_text(data, "invite_code", "Invite code", 64)
            with self.connect() as db:
                community = db.execute("SELECT id FROM communities WHERE invite_code = ?", (code,)).fetchone()
                if not community:
                    raise APIError(404, "That invite code was not found.")
                db.execute(
                    "INSERT OR IGNORE INTO community_members(community_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (community["id"], user_id, int(time.time())),
                )
                row = db.execute("SELECT id, name FROM communities WHERE id = ?", (community["id"],)).fetchone()
            return 200, {"community": dict(row)}, {}

        match = re.fullmatch(r"/api/communities/([a-f0-9]{32})/spaces", path)
        if match and method == "POST":
            community_id = match.group(1)
            with self.connect() as db:
                self._require_admin(db, user_id)
            name = self._required_text(data, "name", "Space name", 48).lower()
            kind = data.get("kind", "text")
            if kind != "text":
                raise APIError(400, "Only text channels are supported.")
            space_id = new_id()
            with self.connect() as db:
                self._require_admin(db, user_id)
                if not db.execute("SELECT 1 FROM communities WHERE id = ?", (community_id,)).fetchone():
                    raise APIError(404, "Community not found.")
                db.execute(
                    "INSERT INTO spaces(id, community_id, name, kind, created_at) VALUES (?, ?, ?, ?, ?)",
                    (space_id, community_id, name, kind, int(time.time())),
                )
            return 201, {"space": {"id": space_id, "name": name, "kind": kind}}, {}

        match = re.fullmatch(r"/api/communities/([a-f0-9]{32})/invite", path)
        if match and method == "GET":
            with self.connect() as db:
                self._require_admin(db, user_id)
                community = db.execute(
                    "SELECT invite_code FROM communities WHERE id = ?", (match.group(1),)
                ).fetchone()
                if not community:
                    raise APIError(404, "Community not found.")
                return 200, {"invite_code": community["invite_code"]}, {}

        if path == "/api/dms" and method == "GET":
            with self.connect() as db:
                rows = db.execute(
                    "SELECT d.id, u.username, u.display_name FROM dm_conversations d "
                    "JOIN users u ON u.id = CASE WHEN d.user_low = ? THEN d.user_high ELSE d.user_low END "
                    "WHERE d.user_low = ? OR d.user_high = ? ORDER BY d.created_at",
                    (user_id, user_id, user_id),
                ).fetchall()
            return 200, {"dms": [dict(row) for row in rows]}, {}

        if path == "/api/dms" and method == "POST":
            username = self._required_text(data, "username", "Username", 24)
            with self.connect() as db:
                other = db.execute(
                    "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
                ).fetchone()
                if not other:
                    raise APIError(404, "No account has that username.")
                if other["id"] == user_id:
                    raise APIError(400, "You cannot start a DM with your own account.")
                low, high = sorted((user_id, other["id"]))
                row = db.execute(
                    "SELECT id FROM dm_conversations WHERE user_low = ? AND user_high = ?",
                    (low, high),
                ).fetchone()
                if row:
                    dm_id = row["id"]
                else:
                    dm_id = new_id()
                    db.execute(
                        "INSERT INTO dm_conversations(id, user_low, user_high, created_at) VALUES (?, ?, ?, ?)",
                        (dm_id, low, high, int(time.time())),
                    )
                peer = db.execute("SELECT username, display_name FROM users WHERE id = ?", (other["id"],)).fetchone()
            return 200, {"dm": {"id": dm_id, **dict(peer)}}, {}

        match = re.fullmatch(r"/api/conversations/([a-f0-9]{32})/messages", path)
        if match and method == "GET":
            conversation_id = match.group(1)
            after_raw = query.get("after", ["0"])[0]
            try:
                after = max(0, int(after_raw))
            except (TypeError, ValueError):
                raise APIError(400, "Invalid message cursor.")
            with self.connect() as db:
                conversation = self._conversation(db, conversation_id, user_id)
                column = "space_id" if conversation["kind"] == "space" else "dm_id"
                rows = db.execute(
                    f"SELECT m.id, m.sender_id, m.text, m.reply_to, m.created_at, u.username, u.display_name "
                    f"FROM messages m JOIN users u ON u.id = m.sender_id "
                    f"WHERE m.{column} = ? AND m.id > ? ORDER BY m.id DESC LIMIT 100",
                    (conversation_id, after),
                ).fetchall()
                messages = [self._message_json(db, row, user_id) for row in reversed(rows)]
            return 200, {"messages": messages}, {}

        if match and method == "POST":
            conversation_id = match.group(1)
            text = self._required_text(data, "text", "Message", 2000)
            reply_to = data.get("reply_to")
            with self.connect() as db:
                conversation = self._conversation(db, conversation_id, user_id)
                if reply_to is not None:
                    try:
                        reply_to = int(reply_to)
                    except (TypeError, ValueError):
                        raise APIError(400, "Invalid reply target.")
                    column = "space_id" if conversation["kind"] == "space" else "dm_id"
                    if not db.execute(f"SELECT id FROM messages WHERE id = ? AND {column} = ?", (reply_to, conversation_id)).fetchone():
                        raise APIError(400, "Reply target is not in this conversation.")
                cursor = db.execute(
                    "INSERT INTO messages(space_id, dm_id, sender_id, text, reply_to, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        conversation_id if conversation["kind"] == "space" else None,
                        conversation_id if conversation["kind"] == "dm" else None,
                        user_id,
                        text,
                        reply_to,
                        int(time.time()),
                    ),
                )
                row = db.execute(
                    "SELECT m.id, m.sender_id, m.text, m.reply_to, m.created_at, u.username, u.display_name "
                    "FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                message = self._message_json(db, row, user_id)
            return 201, {"message": message}, {}

        match = re.fullmatch(r"/api/messages/([0-9]+)/reactions", path)
        if match and method == "POST":
            message_id = int(match.group(1))
            emoji = self._required_text(data, "emoji", "Reaction", 16)
            with self.connect() as db:
                row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
                if not row:
                    raise APIError(404, "Message not found.")
                self._conversation_for_message(db, row, user_id)
                existing = db.execute(
                    "SELECT 1 FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                    (message_id, user_id, emoji),
                ).fetchone()
                if existing:
                    db.execute(
                        "DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                        (message_id, user_id, emoji),
                    )
                else:
                    db.execute(
                        "INSERT INTO reactions(message_id, user_id, emoji) VALUES (?, ?, ?)",
                        (message_id, user_id, emoji),
                    )
                reaction_rows = db.execute(
                    "SELECT r.emoji, COUNT(*) AS count, MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) AS reacted "
                    "FROM reactions r WHERE r.message_id = ? GROUP BY r.emoji",
                    (user_id, message_id),
                ).fetchall()
            return 200, {"reactions": [dict(item) for item in reaction_rows]}, {}

        raise APIError(404, "Not found.")

    def _verify_google_credential(self, credential):
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        if not client_id:
            raise APIError(503, "Google sign-in is not configured on this server.")
        url = "https://oauth2.googleapis.com/tokeninfo"
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode({"id_token": credential}).encode("ascii"),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                claims = json.loads(response.read(16 * 1024).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, UnicodeDecodeError):
            raise APIError(401, "Google could not verify this sign-in.")
        if not isinstance(claims, dict):
            raise APIError(401, "Google could not verify this sign-in.")
        try:
            valid = (
                claims.get("aud") == client_id
                and claims.get("iss") in ("accounts.google.com", "https://accounts.google.com")
                and int(claims.get("exp", "0")) > int(time.time())
                and claims.get("email_verified") == "true"
                and isinstance(claims.get("sub"), str)
                and bool(claims["sub"])
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise APIError(401, "Google could not verify this sign-in.")
        return claims

    def _conversation(self, db, conversation_id, user_id):
        space = db.execute("SELECT id, kind FROM spaces WHERE id = ?", (conversation_id,)).fetchone()
        if space:
            self._require_space(db, conversation_id, user_id)
            return {"kind": "space"}
        dm = self._require_dm(db, conversation_id, user_id)
        return {"kind": "dm", "dm": dm}

    def _conversation_for_message(self, db, message, user_id):
        if message["space_id"]:
            self._require_space(db, message["space_id"], user_id)
        else:
            self._require_dm(db, message["dm_id"], user_id)

    def _message_json(self, db, row, user_id):
        reactions = db.execute(
            "SELECT r.emoji, COUNT(*) AS count, "
            "MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) AS reacted "
            "FROM reactions r WHERE r.message_id = ? GROUP BY r.emoji",
            (user_id, row["id"]),
        ).fetchall()
        return {
            "id": row["id"],
            "author": row["display_name"],
            "username": row["username"],
            "text": row["text"],
            "reply_to": row["reply_to"],
            "created_at": row["created_at"],
            "mine": row["sender_id"] == user_id,
            "reactions": [dict(reaction) for reaction in reactions],
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "ComChat"
    sys_version = ""

    def log_message(self, format_string, *args):
        # Avoid writing account names, message bodies, or request details to logs.
        return

    def _send(self, status, body, extra_headers=None, content_type="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://accounts.google.com; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self' https://accounts.google.com; "
            "frame-src https://accounts.google.com; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        morsel = jar.get("comchat_session")
        return morsel.value if morsel else None

    def _validate_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urlsplit(origin)
        host = self.headers.get("Host", "").lower()
        if parsed.netloc.lower() != host or parsed.scheme not in ("http", "https"):
            raise APIError(403, "Request origin was not accepted.")

    def _check_auth_rate(self, path):
        if path not in ("/api/login", "/api/register", "/api/google"):
            return
        now = time.monotonic()
        address = self.client_address[0]
        with AUTH_RATE_LOCK:
            attempts = [attempt for attempt in AUTH_ATTEMPTS.get(address, []) if now - attempt < AUTH_RATE_WINDOW]
            if len(attempts) >= AUTH_RATE_LIMIT:
                raise APIError(429, "Too many account requests. Try again in a minute.")
            attempts.append(now)
            AUTH_ATTEMPTS[address] = attempts

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._send(200, b"ok", content_type="text/plain; charset=utf-8")
            return
        if not parsed.path.startswith("/api/"):
            static = STATIC_FILES.get(parsed.path)
            if not static:
                self._send(404, b"Not found", content_type="text/plain; charset=utf-8")
                return
            try:
                payload = (ROOT / static[0]).read_bytes()
            except OSError:
                self._send(500, b"Unable to read app file", content_type="text/plain; charset=utf-8")
                return
            if parsed.path in ("/", "/index.html"):
                client_id = html.escape(os.environ.get("GOOGLE_CLIENT_ID", "").strip(), quote=True)
                payload = payload.replace(b"__GOOGLE_CLIENT_ID__", client_id.encode("utf-8"))
            self._send(200, payload, content_type=static[1])
            return
        query = parse_qs(parsed.query)
        status, body, headers = self.server.application.dispatch("GET", parsed.path, query, {}, self._token())
        self._send(status, body, headers)

    def _do_api_write(self):
        parsed = urlsplit(self.path)
        try:
            self._validate_origin()
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                raise APIError(413, "Request body is too large.")
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(data, dict):
                raise APIError(400, "Request body must be an object.")
            self._check_auth_rate(parsed.path)
            status, body, headers = self.server.application.dispatch(self.command, parsed.path, {}, data, self._token())
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            status, body, headers = 400, {"error": "Invalid JSON request body."}, {}
        except APIError as error:
            status, body, headers = error.status, {"error": str(error)}, {}
        self._send(status, body, headers)

    do_POST = _do_api_write
    do_PATCH = _do_api_write


def main():
    host = os.environ.get("COMCHAT_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "4173"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.application = ComChat(DATABASE)
    print(f"ComChat server listening on http://{host}:{port}")
    print(f"SQLite database: {DATABASE}")
    print("Use HTTPS through a trusted reverse proxy before exposing this server to the internet.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
