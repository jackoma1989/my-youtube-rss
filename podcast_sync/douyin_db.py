import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Set, Optional
from datetime import datetime

class DouyinDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path("output/douyin_podcast.db")
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()


    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_episodes (
                    channel_id TEXT NOT NULL,
                    aweme_id TEXT NOT NULL,
                    title TEXT,
                    duration INTEGER,
                    pub_date TEXT,
                    synced_at TEXT,
                    r2_url TEXT,
                    PRIMARY KEY (channel_id, aweme_id)
                )
            """)
            conn.commit()

    def is_seen(self, channel_id: str, aweme_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM seen_episodes WHERE channel_id = ? AND aweme_id = ?",
                (channel_id, aweme_id),
            )
            return cur.fetchone() is not None

    def get_seen_ids(self, channel_id: str) -> Set[str]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT aweme_id FROM seen_episodes WHERE channel_id = ?",
                (channel_id,),
            )
            return {row[0] for row in cur.fetchall()}

    def mark_seen(
        self,
        channel_id: str,
        aweme_id: str,
        title: str,
        duration: int,
        pub_date: str,
        r2_url: str = "",
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO seen_episodes
                (channel_id, aweme_id, title, duration, pub_date, synced_at, r2_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    aweme_id,
                    title,
                    duration,
                    pub_date,
                    datetime.now().isoformat(),
                    r2_url,
                ),
            )
            conn.commit()

    def count_seen(self, channel_id: str) -> int:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM seen_episodes WHERE channel_id = ?",
                (channel_id,),
            )
            return cur.fetchone()[0]

    def sync_from_manifest(self, channel_id: str, episodes) -> int:
        """Seed seen_episodes from existing manifest list."""
        added = 0
        for ep in episodes:
            if not self.is_seen(channel_id, ep.video_id):
                pub_iso = ep.pub_date.isoformat() if hasattr(ep.pub_date, "isoformat") else str(ep.pub_date)
                self.mark_seen(
                    channel_id=channel_id,
                    aweme_id=ep.video_id,
                    title=ep.title,
                    duration=ep.duration_seconds,
                    pub_date=pub_iso,
                    r2_url=ep.audio_url,
                )
                added += 1
        return added

