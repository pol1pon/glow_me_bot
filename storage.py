from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Subscription:
    """Подписка пользователя на отслеживание товара."""

    chat_id: int
    product_uid: str
    product_title: str
    product_url: str
    last_in_stock: bool | None


class Storage:
    """Хранилище подписок и последнего известного статуса наличия в SQLite."""

    def __init__(self, db_path: str | Path = "bot_data.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    chat_id INTEGER NOT NULL,
                    product_uid TEXT NOT NULL,
                    product_title TEXT NOT NULL,
                    product_url TEXT NOT NULL,
                    last_in_stock INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, product_uid)
                );

                CREATE TABLE IF NOT EXISTS watch_all_chats (
                    chat_id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS product_states (
                    product_uid TEXT PRIMARY KEY,
                    product_title TEXT NOT NULL,
                    in_stock INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def add_subscription(
        self,
        chat_id: int,
        product_uid: str,
        product_title: str,
        product_url: str,
        in_stock: bool,
    ) -> bool:
        """Добавляет подписку. Возвращает True, если подписка новая."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM subscriptions WHERE chat_id = ? AND product_uid = ?",
                (chat_id, product_uid),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO subscriptions (chat_id, product_uid, product_title, product_url, last_in_stock)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, product_uid) DO UPDATE SET
                    product_title = excluded.product_title,
                    product_url = excluded.product_url,
                    last_in_stock = excluded.last_in_stock
                """,
                (chat_id, product_uid, product_title, product_url, int(in_stock)),
            )
            return existing is None

    def remove_subscription(self, chat_id: int, product_uid: str) -> bool:
        """Удаляет подписку. Возвращает True, если запись существовала."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM subscriptions WHERE chat_id = ? AND product_uid = ?",
                (chat_id, product_uid),
            )
            return cur.rowcount > 0

    def is_subscribed(self, chat_id: int, product_uid: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscriptions WHERE chat_id = ? AND product_uid = ?",
                (chat_id, product_uid),
            ).fetchone()
            return row is not None

    def list_subscriptions(self, chat_id: int) -> list[Subscription]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, product_uid, product_title, product_url, last_in_stock
                FROM subscriptions
                WHERE chat_id = ?
                ORDER BY created_at DESC
                """,
                (chat_id,),
            ).fetchall()
            return [
                Subscription(
                    chat_id=row["chat_id"],
                    product_uid=row["product_uid"],
                    product_title=row["product_title"],
                    product_url=row["product_url"],
                    last_in_stock=(
                        None if row["last_in_stock"] is None else bool(row["last_in_stock"])
                    ),
                )
                for row in rows
            ]

    def list_all_subscriptions(self) -> list[Subscription]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, product_uid, product_title, product_url, last_in_stock
                FROM subscriptions
                """
            ).fetchall()
            return [
                Subscription(
                    chat_id=row["chat_id"],
                    product_uid=row["product_uid"],
                    product_title=row["product_title"],
                    product_url=row["product_url"],
                    last_in_stock=(
                        None if row["last_in_stock"] is None else bool(row["last_in_stock"])
                    ),
                )
                for row in rows
            ]

    def update_subscription_state(
        self, chat_id: int, product_uid: str, in_stock: bool
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE subscriptions
                SET last_in_stock = ?
                WHERE chat_id = ? AND product_uid = ?
                """,
                (int(in_stock), chat_id, product_uid),
            )

    def set_watch_all(self, chat_id: int, enabled: bool) -> None:
        with self._connect() as conn:
            if enabled:
                conn.execute(
                    "INSERT OR IGNORE INTO watch_all_chats (chat_id) VALUES (?)",
                    (chat_id,),
                )
            else:
                conn.execute(
                    "DELETE FROM watch_all_chats WHERE chat_id = ?",
                    (chat_id,),
                )

    def is_watch_all(self, chat_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM watch_all_chats WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return row is not None

    def list_watch_all_chats(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT chat_id FROM watch_all_chats").fetchall()
            return [row["chat_id"] for row in rows]

    def get_catalog_states(self) -> dict[str, bool]:
        with self._connect() as conn:
            rows = conn.execute("SELECT product_uid, in_stock FROM product_states").fetchall()
            return {row["product_uid"]: bool(row["in_stock"]) for row in rows}

    def save_catalog_states(self, states: list[tuple[str, str, bool]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO product_states (product_uid, product_title, in_stock, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(product_uid) DO UPDATE SET
                    product_title = excluded.product_title,
                    in_stock = excluded.in_stock,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [(uid, title, int(in_stock)) for uid, title, in_stock in states],
            )
