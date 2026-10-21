"""Bounded relational reads and optimistic transactions for both supported databases."""
from pathlib import Path
from .identity import sqlite_actor_active

TABLE_KEYS = {
    "thread_posts": ("id",), "thread_edges": ("actor_identity_id", "target_identity_id", "kind"),
    "thread_likes": ("post_id", "actor_identity_id"), "thread_notifications": ("id",),
    "thread_reports": ("id",),
}


class ThreadRepository:
    def __init__(self, repository):
        self.repository = repository
        self.sqlite = hasattr(repository, "connection")

    def rows(self, table, filters=None, *, order="id.desc", limit=40):
        assert table in (*TABLE_KEYS, "thread_meta", "thread_post_summary", "users")
        filters = filters or {}
        if not self.sqlite:
            return self.repository.rows(table, {"select": "*", **filters, "order": order, "limit": str(limit)})
        clauses, values = [], []
        for column, expression in filters.items():
            assert column.replace("_", "").isalnum()
            operator, value = expression.split(".", 1)
            if operator == "in":
                items = value.strip("()").split(",")
                clauses.append(f"{column} IN ({','.join('?' for _ in items)})")
                values.extend(items)
            elif operator == "is" and value == "null":
                clauses.append(f"{column} IS NULL")
            elif operator in ("eq", "lt", "gt"):
                clauses.append(f"{column} {dict(eq='=', lt='<', gt='>')[operator]} ?")
                values.append(value)
            else:
                raise ValueError("unsupported query")
        ordering = []
        for part in order.split(","):
            column, direction = part.split(".")
            assert column.replace("_", "").isalnum() and direction in ("asc", "desc")
            ordering.append(f"{column} {direction}")
        query = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY " + ",".join(ordering) + " LIMIT ?"
        with self.repository.connection() as db:
            cursor = db.execute(query, (*values, limit))
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def revision(self):
        return int(self.rows("thread_meta", {"id": "eq.1"}, limit=1)[0]["revision"])

    def review_snapshot(self, *, capacity=10000):
        """Read-only complete public snapshot for an isolated review index.

        Global revision checks bound the paginated read on SQLite and PostgREST.
        Overflow/churn is an error, never an apparently complete partial index.
        """
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            raise ValueError("invalid snapshot capacity")
        revision = self.revision()
        posts, after = [], None
        while True:
            page = self.rows("thread_posts", {"id": f"gt.{after}"} if after else {}, order="id.asc", limit=500)
            posts.extend(page)
            if len(posts) > capacity:
                raise ValueError("thread snapshot exceeds capacity")
            if len(page) < 500:
                break
            after = page[-1]["id"]
        by_id = {post["id"]: post for post in posts}
        documents = []
        for post in posts:
            root = by_id.get(post["root_id"]) if post.get("root_id") else post
            if post["deleted"] or post["visibility"] != "public" or not root or root["deleted"] or root["visibility"] != "public":
                continue
            documents.append(dict(id=post["id"], body=post["body"], title="", scope="public", source_kind="thread",
                                  source_url=None, original_id=None, first_observed_at=None,
                                  published_at=post["created_at"], modified_at=post["edited_at"] or None))
        if self.revision() != revision:
            raise ValueError("threads changed while reading snapshot")
        return revision, documents

    def commit(self, actor_id, revision, mutations):
        if not self.sqlite:
            return self.repository.rpc("colorless_threads_commit", {
                "actor_id": actor_id, "expected_revision": revision, "mutations": mutations,
            })
        with self.repository.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT revision FROM thread_meta WHERE id=1").fetchone()[0] != revision:
                return {"error": "conflict"}
            if not sqlite_actor_active(db, actor_id):
                return {"error": "forbidden"}
            for mutation in mutations:
                table, row = mutation["table"], mutation["row"]
                keys = TABLE_KEYS[table]
                if mutation.get("remove"):
                    db.execute(f"DELETE FROM {table} WHERE " + " AND ".join(f"{key}=?" for key in keys),
                               tuple(row[key] for key in keys))
                else:
                    columns = list(row)
                    assert all(column.replace("_", "").isalnum() for column in columns)
                    updates = ",".join(f"{key}=excluded.{key}" for key in columns if key not in keys)
                    db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                               f"ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}", tuple(row.values()))
            db.execute("UPDATE thread_meta SET revision=revision+1 WHERE id=1")
        return {"revision": revision + 1}


def initialize_threads(repository):
    with repository.connection() as db:
        db.executescript((Path(__file__).parent / "database" / "threads-tables.sql").read_text(encoding="utf-8"))
