from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class CommentKey:
    meeting_id: str
    row_number: int
    fsc: str
    profile: str


class CommentStore:
    """Small local SQLite store for Finance comments.

    The key intentionally includes meeting_id + row_number + FSC + profile so comments
    from one bi-weekly workbook do not accidentally flow into another workbook.
    """

    def __init__(self, db_path: str | Path = "app_data/comments.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_comments (
                    meeting_id TEXT NOT NULL,
                    workbook_name TEXT,
                    workbook_hash TEXT,
                    row_number INTEGER NOT NULL,
                    fsc TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    product_name TEXT,
                    finance_comment TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (meeting_id, row_number, fsc, profile)
                )
                """
            )
            conn.commit()

    @staticmethod
    def _clean(value) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def save(
        self,
        *,
        meeting_id: str,
        workbook_name: str,
        workbook_hash: str,
        row_number: int,
        fsc: str,
        profile: str,
        product_name: str,
        finance_comment: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO finance_comments (
                    meeting_id, workbook_name, workbook_hash, row_number, fsc, profile,
                    product_name, finance_comment, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meeting_id, row_number, fsc, profile)
                DO UPDATE SET
                    workbook_name=excluded.workbook_name,
                    workbook_hash=excluded.workbook_hash,
                    product_name=excluded.product_name,
                    finance_comment=excluded.finance_comment,
                    updated_at=excluded.updated_at
                """,
                (
                    meeting_id,
                    workbook_name,
                    workbook_hash,
                    int(row_number),
                    self._clean(fsc),
                    self._clean(profile),
                    self._clean(product_name),
                    finance_comment or "",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

    def get(self, *, meeting_id: str, row_number: int, fsc: str, profile: str) -> str | None:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT finance_comment
                FROM finance_comments
                WHERE meeting_id=? AND row_number=? AND fsc=? AND profile=?
                """,
                (meeting_id, int(row_number), self._clean(fsc), self._clean(profile)),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def list_for_meeting(self, meeting_id: str) -> pd.DataFrame:
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT
                    row_number AS "Original Row Number",
                    fsc AS "FSC",
                    profile AS "Profile #",
                    product_name AS "Product Name",
                    finance_comment AS "Finance Comment",
                    updated_at AS "Updated At"
                FROM finance_comments
                WHERE meeting_id=?
                ORDER BY row_number
                """,
                conn,
                params=(meeting_id,),
            )
        return df

    def count_for_meeting(self, meeting_id: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM finance_comments WHERE meeting_id=? AND TRIM(COALESCE(finance_comment,'')) <> ''",
                (meeting_id,),
            )
            return int(cur.fetchone()[0])

    def delete_for_meeting(self, meeting_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM finance_comments WHERE meeting_id=?",
                (meeting_id,),
            )
            conn.commit()

    def delete_one(self, meeting_id: str, row_number: int, fsc: str, profile: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM finance_comments
                WHERE meeting_id=?
                    AND row_number=?
                    AND fsc=?
                    AND profile=?
                """,
                (meeting_id, row_number, fsc, profile),
            )
            conn.commit()
