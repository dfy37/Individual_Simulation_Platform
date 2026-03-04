import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "demo_data", "demo.db")


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            questionnaire TEXT,
            interviewee TEXT,
            topic TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            turn_idx INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            event_type TEXT,
            internal_note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            satisfaction INTEGER,
            correctness INTEGER,
            comments TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS message_trace (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            turn_idx INTEGER NOT NULL,
            message_id INTEGER,
            state_json TEXT NOT NULL,
            action TEXT,
            policy_applied TEXT,
            event_type TEXT,
            followup_decision_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
        """
    )
    conn.commit()


def create_session(
    conn: sqlite3.Connection,
    mode: str,
    questionnaire: Optional[str] = None,
    interviewee: Optional[str] = None,
    topic: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sessions(mode, questionnaire, interviewee, topic, metadata_json, created_at)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            mode,
            questionnaire,
            interviewee,
            topic,
            json.dumps(metadata or {}, ensure_ascii=False),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_message(
    conn: sqlite3.Connection,
    session_id: int,
    turn_idx: Optional[int],
    role: str,
    content: str,
    event_type: Optional[str] = None,
    internal_note: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO messages(session_id, turn_idx, role, content, event_type, internal_note, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, turn_idx, role, content, event_type, internal_note, datetime.utcnow().isoformat()),
    )
    conn.commit()


def bulk_insert_messages(conn: sqlite3.Connection, rows: Iterable[tuple]) -> None:
    conn.executemany(
        """
        INSERT INTO messages(session_id, turn_idx, role, content, event_type, internal_note, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def upsert_report(conn: sqlite3.Connection, session_id: int, report: Dict[str, Any]) -> None:
    cur = conn.cursor()
    cur.execute("SELECT id FROM reports WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE reports SET report_json = ?, created_at = ? WHERE session_id = ?",
            (json.dumps(report, ensure_ascii=False), datetime.utcnow().isoformat(), session_id),
        )
    else:
        cur.execute(
            "INSERT INTO reports(session_id, report_json, created_at) VALUES(?, ?, ?)",
            (session_id, json.dumps(report, ensure_ascii=False), datetime.utcnow().isoformat()),
        )
    conn.commit()


def insert_feedback(
    conn: sqlite3.Connection,
    session_id: int,
    satisfaction: int,
    correctness: int,
    comments: str,
) -> None:
    conn.execute(
        """
        INSERT INTO feedback(session_id, satisfaction, correctness, comments, created_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (session_id, satisfaction, correctness, comments, datetime.utcnow().isoformat()),
    )
    conn.commit()


def insert_message_trace(
    conn: sqlite3.Connection,
    session_id: int,
    turn_idx: int,
    state: Dict[str, Any],
    action: str,
    policy_applied: str,
    event_type: str,
    followup_decision: Dict[str, Any],
    message_id: Optional[int] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO message_trace(
            session_id, turn_idx, message_id, state_json, action, policy_applied, event_type, followup_decision_json, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            int(turn_idx),
            message_id,
            json.dumps(state or {}, ensure_ascii=False),
            action,
            policy_applied,
            event_type,
            json.dumps(followup_decision or {}, ensure_ascii=False),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
