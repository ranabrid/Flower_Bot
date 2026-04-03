# database.py

import sqlite3
from datetime import datetime
import pytz
from config import DATABASE_NAME, MOSCOW_TIMEZONE

moscow_tz = pytz.timezone(MOSCOW_TIMEZONE)


def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row  # Чтобы получать результаты как словари
    return conn


def save_user_date(chat_id: int, next_remind_date_iso: str):
    """Сохраняет или обновляет дату следующего напоминания для пользователя."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO users (chat_id, next_remind_date)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET next_remind_date=excluded.next_remind_date
        """,
            (chat_id, next_remind_date_iso),
        )
        conn.commit()
        print(f"User {chat_id}: saved next reminder date: {next_remind_date_iso}")
    except sqlite3.Error as e:
        print(f"Database error (save_user_date for {chat_id}): {e}")
    finally:
        conn.close()


def get_user_date(chat_id: int) -> str | None:
    """Получает дату следующего напоминания для пользователя."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT next_remind_date FROM users WHERE chat_id = ?", (chat_id,)
        )
        result = cursor.fetchone()
        if result and result["next_remind_date"]:
            return result["next_remind_date"]
        return None
    except sqlite3.Error as e:
        print(f"Database error (get_user_date for {chat_id}): {e}")
        return None
    finally:
        conn.close()


def delete_user_data(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE users 
            SET next_remind_date = NULL,
                interval_days = NULL
            WHERE chat_id = ?
        """,
            (chat_id,),
        )
        conn.commit()
        print(f"User {chat_id}: data cleared from DB.")
    finally:
        conn.close()


def get_all_users_for_restore() -> list[tuple[int, str]]:
    """Получает все записи пользователей для восстановления напоминаний."""
    conn = get_db_connection()
    cursor = conn.cursor()
    users_data = []
    try:
        cursor.execute(
            "SELECT chat_id, next_remind_date FROM users WHERE next_remind_date IS NOT NULL"
        )
        users_data = cursor.fetchall()
        print(f"Restoring reminders for {len(users_data)} users.")
    except sqlite3.Error as e:
        print(f"Database error (get_all_users_for_restore): {e}")
    finally:
        conn.close()
    return users_data


def save_interval(chat_id: int, interval_days: int):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO users (chat_id, interval_days)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET interval_days=excluded.interval_days
    """,
        (chat_id, interval_days),
    )

    conn.commit()
    conn.close()


def get_interval(chat_id: int) -> int | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT interval_days FROM users WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    return result["interval_days"] if result and result["interval_days"] else None


# --- Инициализация БД ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                next_remind_date TEXT,
                interval_days INTEGER
            )
        """)

        # 👇 ДОБАВЬ ВОТ ЭТО (миграция)
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]

        if "interval_days" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN interval_days INTEGER")
            print("Added interval_days column.")

        conn.commit()
        print("Database initialized successfully.")

    except sqlite3.Error as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()
