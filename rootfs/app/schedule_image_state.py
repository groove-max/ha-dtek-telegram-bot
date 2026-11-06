"""Latest confirmed automatic photo per bot journal, recipient, profile and day."""

import hashlib
import json
import time

from schedule_pages import select_page


def image_content_hash(data, offset, personal):
    """Hash the whole displayed day, excluding moving time and poll timestamps."""
    view = select_page(data, offset, personal, "all")
    return hashlib.sha256(json.dumps({
        "day": view.day, "address": data.get("address", ""), "personal": personal,
        "time_zone": data.get("time_zone"),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def image_binding(config):
    return hashlib.sha256(json.dumps([
        getattr(config, "mode", "building"), config.entity_prefix,
        getattr(config, "account_entity", ""), getattr(config, "selected_group", ""),
    ]).encode()).hexdigest()


class ScheduleImageStore:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS schedule_images (
            chat TEXT, address TEXT, day TEXT, binding TEXT, ids TEXT,
            groups_json TEXT, next_attempt REAL, PRIMARY KEY(chat,address,day))""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(schedule_images)")}
        for name, definition in (("content_hash", "TEXT NOT NULL DEFAULT ''"),
                                 ("retry_after", "REAL NOT NULL DEFAULT 0")):
            if name not in columns:
                db.execute(f"ALTER TABLE schedule_images ADD COLUMN {name} {definition}")
                if name == "retry_after":
                    # Older versions shared one deadline with Telegram backoff.
                    # Preserve it during upgrade rather than violating a 429.
                    db.execute("UPDATE schedule_images SET retry_after=next_attempt")
        db.commit()

    def remember(self, chat, address, day, binding, ids, groups, interval=300, content_hash=""):
        if not ids or len(ids) > 10 or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("Invalid schedule photo IDs")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO schedule_images "
                            "(chat,address,day,binding,ids,groups_json,next_attempt,content_hash,retry_after) "
                            "VALUES(?,?,?,?,?,?,?,?,0)",
                            (str(chat), address, day, binding, json.dumps(ids),
                             json.dumps(groups), time.time() + interval, content_hash))
            # Tracking also runs while the optional updater is off. Bound its
            # history without dropping today's record when tomorrow is sent.
            self.db.execute("DELETE FROM schedule_images WHERE day < "
                            "date((SELECT MAX(day) FROM schedule_images), '-2 days')")

    def latest(self, chat, address, day):
        with self.db:
            self.db.execute("DELETE FROM schedule_images WHERE day < ?", (day,))
        row = self.db.execute("SELECT binding,ids,groups_json,next_attempt,content_hash,retry_after FROM schedule_images "
                              "WHERE chat=? AND address=? AND day=?", (str(chat), address, day)).fetchone()
        if row is None:
            return None
        return dict(chat=str(chat), address=address, day=day, binding=row[0],
                    ids=json.loads(row[1]), groups=json.loads(row[2]), next_attempt=row[3],
                    content_hash=row[4], retry_after=row[5])

    def matches(self, record):
        current = self.latest(record['chat'], record['address'], record['day'])
        return current is not None and all(current[k] == record[k] for k in ('binding', 'ids', 'groups'))

    def defer(self, record, until):
        with self.db:
            self.db.execute("UPDATE schedule_images SET next_attempt=?,retry_after=? WHERE chat=? AND address=? "
                            "AND day=? AND binding=? AND ids=?", (until, until, record['chat'], record['address'],
                            record['day'], record['binding'], json.dumps(record['ids'])))

    def applied(self, record, content_hash, until):
        with self.db:
            self.db.execute("UPDATE schedule_images SET content_hash=?,next_attempt=?,retry_after=0 "
                            "WHERE chat=? AND address=? AND day=? AND binding=? AND ids=?",
                            (content_hash, until, record['chat'], record['address'], record['day'],
                             record['binding'], json.dumps(record['ids'])))

    def resume(self, record):
        """A newer verified snapshot may supersede rendering without a Telegram failure."""
        with self.db:
            self.db.execute("UPDATE schedule_images SET retry_after=0 "
                            "WHERE chat=? AND address=? AND day=? AND binding=? AND ids=?",
                            (record['chat'], record['address'], record['day'], record['binding'], json.dumps(record['ids'])))

    def forget(self, record):
        with self.db:
            self.db.execute("DELETE FROM schedule_images WHERE chat=? AND address=? AND day=? AND binding=? AND ids=?",
                            (record['chat'], record['address'], record['day'], record['binding'], json.dumps(record['ids'])))
