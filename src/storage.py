import hashlib
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path


def app_data_dir():
    override = os.environ.get("SIMBRIDGE_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SIMBridge"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "SIMBridge"
    return Path.home() / ".local" / "share" / "simbridge"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def message_fingerprint(message):
    if message.get("archiveKey"):
        return hashlib.sha256(str(message["archiveKey"]).encode("utf-8")).hexdigest()
    decoded = message.get("decoded") or {}
    pdu = str(message.get("pdu") or "").strip().upper()
    if pdu:
        source = f"pdu:{pdu}"
    else:
        source = "\x1f".join(
            [
                str(decoded.get("kind") or message.get("direction") or "unknown"),
                str(decoded.get("peer") or ""),
                str(decoded.get("timestamp") or ""),
                str(decoded.get("text") or ""),
            ]
        )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class MessageArchive:
    def __init__(self, path=None):
        self.path = Path(path) if path else app_data_dir() / "messages.db"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    modem_index INTEGER,
                    status INTEGER,
                    direction TEXT NOT NULL,
                    peer TEXT NOT NULL DEFAULT '',
                    message_time TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    dcs TEXT NOT NULL DEFAULT '',
                    pdu TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    starred INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_time_idx
                    ON messages(message_time DESC, created_at DESC);
                CREATE INDEX IF NOT EXISTS messages_direction_idx
                    ON messages(direction, status);
                CREATE TABLE IF NOT EXISTS tombstones (
                    id TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _direction(message):
        decoded = message.get("decoded") or {}
        kind = decoded.get("kind") or message.get("direction")
        if kind in ("deliver", "inbox", "received") or message.get("status") in (0, 1):
            return "inbox"
        if kind in ("submit", "sent") or message.get("status") == 3:
            return "sent"
        return "unknown"

    def ingest(self, messages):
        stamp = now_iso()
        stored = 0
        with self._lock, self._connect() as connection:
            tombstones = {row[0] for row in connection.execute("SELECT id FROM tombstones")}
            for message in messages or []:
                message_id = message_fingerprint(message)
                if message_id in tombstones:
                    continue
                decoded = message.get("decoded") or {}
                modem_index = message.get("index")
                if modem_index is not None:
                    connection.execute(
                        "UPDATE messages SET modem_index = NULL WHERE modem_index = ? AND id != ?",
                        (modem_index, message_id),
                    )
                connection.execute(
                    """
                    INSERT INTO messages (
                        id, modem_index, status, direction, peer, message_time,
                        text, dcs, pdu, created_at, updated_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        modem_index=excluded.modem_index,
                        status=excluded.status,
                        direction=excluded.direction,
                        peer=excluded.peer,
                        message_time=excluded.message_time,
                        text=excluded.text,
                        dcs=excluded.dcs,
                        pdu=excluded.pdu,
                        updated_at=excluded.updated_at,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        message_id,
                        modem_index,
                        message.get("status"),
                        self._direction(message),
                        str(decoded.get("peer") or ""),
                        str(decoded.get("timestamp") or ""),
                        str(decoded.get("text") or decoded.get("error") or ""),
                        str(decoded.get("dcs") or ""),
                        str(message.get("pdu") or ""),
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                stored += 1
        return stored

    def record_sent(self, peer, text, pdu=""):
        message = {
            "archiveKey": f"sent:{uuid.uuid4().hex}",
            "index": None,
            "status": 3,
            "pdu": pdu,
            "decoded": {
                "kind": "submit",
                "peer": peer,
                "timestamp": now_iso().replace("T", " "),
                "text": text,
                "dcs": "0x08",
            },
        }
        self.ingest([message])
        return message_fingerprint(message)

    def list(self, box="all", query="", limit=500, offset=0):
        where = []
        values = []
        if box == "inbox":
            where.append("direction = 'inbox'")
        elif box == "sent":
            where.append("direction = 'sent'")
        elif box == "unread":
            where.append("status = 0")
        elif box == "starred":
            where.append("starred = 1")
        elif box != "all":
            raise ValueError(f"未知短信筛选：{box}")
        query = str(query or "").strip()
        if query:
            where.append("(peer LIKE ? OR text LIKE ? OR note LIKE ?)")
            like = f"%{query}%"
            values.extend([like, like, like])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        limit = min(max(int(limit), 1), 1000)
        offset = max(int(offset), 0)
        sql = f"""
            SELECT * FROM messages
            {clause}
            ORDER BY
                CASE WHEN message_time = '' THEN created_at ELSE message_time END DESC,
                created_at DESC
            LIMIT ? OFFSET ?
        """
        count_sql = f"SELECT COUNT(*) FROM messages {clause}"
        with self._lock, self._connect() as connection:
            total = connection.execute(count_sql, values).fetchone()[0]
            rows = connection.execute(sql, [*values, limit, offset]).fetchall()
        return {
            "ok": True,
            "box": box,
            "total": total,
            "messages": [self._serialize(row) for row in rows],
        }

    @staticmethod
    def _serialize(row):
        return {
            "archiveId": row["id"],
            "index": row["modem_index"],
            "status": row["status"],
            "pdu": row["pdu"],
            "note": row["note"],
            "starred": bool(row["starred"]),
            "archivedAt": row["created_at"],
            "decoded": {
                "kind": "deliver" if row["direction"] == "inbox" else "submit",
                "peer": row["peer"],
                "timestamp": row["message_time"],
                "text": row["text"],
                "dcs": row["dcs"],
            },
        }

    def update(self, message_id, note=None, starred=None):
        changes = []
        values = []
        if note is not None:
            changes.append("note = ?")
            values.append(str(note).strip()[:1000])
        if starred is not None:
            changes.append("starred = ?")
            values.append(1 if starred else 0)
        if not changes:
            raise ValueError("没有需要修改的内容")
        changes.append("updated_at = ?")
        values.extend([now_iso(), message_id])
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE messages SET {', '.join(changes)} WHERE id = ?", values
            )
            if cursor.rowcount != 1:
                raise KeyError("短信存档不存在")
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return {"ok": True, "message": self._serialize(row)}

    def delete(self, message_id):
        stamp = now_iso()
        with self._lock, self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone()
            if not exists:
                raise KeyError("短信存档不存在")
            connection.execute(
                "INSERT OR REPLACE INTO tombstones (id, deleted_at) VALUES (?, ?)",
                (message_id, stamp),
            )
            connection.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        return {"ok": True, "deleted": message_id}

    def detach_modem_indices(self, indices):
        clean = [int(value) for value in indices]
        if not clean:
            return
        placeholders = ",".join("?" for _ in clean)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE messages SET modem_index = NULL, updated_at = ? WHERE modem_index IN ({placeholders})",
                [now_iso(), *clean],
            )

    def stats(self):
        with self._lock, self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            inbox = connection.execute("SELECT COUNT(*) FROM messages WHERE direction = 'inbox'").fetchone()[0]
            sent = connection.execute("SELECT COUNT(*) FROM messages WHERE direction = 'sent'").fetchone()[0]
        return {"ok": True, "total": total, "inbox": inbox, "sent": sent, "path": str(self.path)}


class ArchiveSyncService:
    def __init__(self, archive, source, poll_interval=15):
        self.archive = archive
        self.source = source
        self.poll_interval = max(10, int(poll_interval))
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_synced_at = ""
        self.last_error = ""

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sms-archive", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            self.sync_once()
            self._stop.wait(self.poll_interval)

    def sync_once(self, wait=False):
        if not self._lock.acquire(blocking=wait):
            return 0
        try:
            result = self.source("all")
            count = self.archive.ingest(result.get("messages") or [])
            self.last_synced_at = now_iso()
            self.last_error = ""
            return count
        except Exception as exc:
            self.last_error = str(exc)
            return 0
        finally:
            self._lock.release()

    def status(self):
        return {
            **self.archive.stats(),
            "lastSyncedAt": self.last_synced_at,
            "lastError": self.last_error,
        }
