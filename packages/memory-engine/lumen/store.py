  enhancement_level INTEGER DEFAULT 0,
  pinned INTEGER DEFAULT 0, last_seen TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS edges (
  from_id TEXT, to_id TEXT, type TEXT, confidence REAL, created_at TEXT,
  PRIMARY KEY (from_id, to_id, type)
);
CREATE TABLE IF NOT EXISTS embeddings (
  memory_id TEXT PRIMARY KEY, vector TEXT
);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        # migrate old DBs missing new columns (ALTER appends at end —
        # order varies, so all reads map by NAME, never position)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(memories)")}
        for col, decl in [("chain_id", "TEXT DEFAULT 'default'"),
                          ("template", "TEXT DEFAULT 'general'"),
                          ("enhanced_content", "TEXT"),
                          ("enhancement_level", "INTEGER DEFAULT 0"),
                          ("pinned", "INTEGER DEFAULT 0"),
                          ("last_seen", "TEXT")]:
            if col not in cols:
                self.db.execute(f"ALTER TABLE memories ADD COLUMN {col} {decl}")
        self._cols = [r[1] for r in self.db.execute("PRAGMA table_info(memories)")]
        self.save_chain(Chain())

        # Setup FTS5 full-text search with auto-sync triggers and backfill
        self.has_fts = False
        try:
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
