"""Explicit, bounded requests to the owner; discovery never grants access."""

import secrets
import time


class ConnectionRequests:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS connection_requests (
            user_id INTEGER PRIMARY KEY, name TEXT, username TEXT,
            status TEXT, created REAL, changed REAL, revision TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS profile_connection_requests (
            user_id INTEGER, name TEXT, username TEXT, status TEXT,
            created REAL, changed REAL, revision TEXT, profile_key TEXT, binding TEXT,
            PRIMARY KEY(user_id,profile_key))""")
        db.commit()

    def request(self, sender, profile_key="", binding=""):
        user = sender["id"]
        if type(user) is not int or not 0 < user < 2**52 or sender.get("is_bot"):
            raise ValueError("Invalid applicant")
        table = "profile_connection_requests" if profile_key else "connection_requests"
        condition = "user_id=?" + (" AND profile_key=?" if profile_key else "")
        args = (user, profile_key) if profile_key else (user,)
        now = time.time()
        with self.db:
            for name in ("connection_requests", "profile_connection_requests"):
                self.db.execute(f"DELETE FROM {name} WHERE changed < ?", (now - 30 * 86400,))
            identity = "binding" if profile_key else "''"
            row = self.db.execute(f"SELECT status,changed,{identity} FROM {table} WHERE {condition}", args).fetchone()
            if row and (not profile_key or row[2] == binding) and (row[0] == "pending" or now - row[1] < 86400):
                return row[0]
            count = sum(self.db.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in ("connection_requests", "profile_connection_requests"))
            if not row and count >= 500:
                return "full"
            name = " ".join(str(sender.get(k) or "") for k in ("first_name", "last_name")).strip()[:160]
            values = (user, name, str(sender.get("username") or "")[:64], "pending", now, now, secrets.token_hex(12))
            if profile_key:
                values += (profile_key, binding)
            self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES({','.join('?' for _ in values)})", values)
        return "pending"

    def pending(self):
        result = []
        for table in ("connection_requests", "profile_connection_requests"):
            extra = "profile_key,binding" if table == "profile_connection_requests" else "'' AS profile_key,'' AS binding"
            for row in self.db.execute(f"SELECT user_id,name,username,created,revision,{extra} FROM {table} WHERE status='pending' AND created>=? ORDER BY created", (time.time() - 30 * 86400,)):
                result.append(dict(zip(("user_id", "name", "username", "created", "revision", "profile_key", "binding"), row)))
        return sorted(result, key=lambda r: r["created"])

    def resolve(self, user, status, revision, profile_key=""):
        if status not in ("accepted", "declined"):
            raise ValueError("Invalid decision")
        table = "profile_connection_requests" if profile_key else "connection_requests"
        extra = " AND profile_key=?" if profile_key else ""
        args = (status, time.time(), user, revision) + ((profile_key,) if profile_key else ())
        with self.db:
            result = self.db.execute(f"UPDATE {table} SET status=?,changed=? WHERE user_id=? AND status='pending' AND revision=?{extra}", args)
        return result.rowcount == 1
