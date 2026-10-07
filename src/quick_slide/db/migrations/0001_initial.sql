-- 0001_initial: Quick Slide v2 の最初の形（P1: 土台）。
-- ブランド・スタイル（改訂は不変）、デッキ（モードつき）、タグ、資産、起動、出来事、ごみ箱。
-- 台本・描いた回（scripts / runs / run_pages / page_ids）は P2 の移行で足す。
-- このファイルは適用後に変更しない（schema_migrations の sha256 と不一致になると起動を拒否する）。
CREATE TABLE assets (
 id TEXT PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE,
 media_type TEXT NOT NULL, sha256 TEXT NOT NULL,
 bytes INTEGER NOT NULL CHECK(bytes>=0), width INTEGER, height INTEGER,
 state TEXT NOT NULL CHECK(state IN ('ready','quarantined','trashed','missing')),
 created_at TEXT NOT NULL
);
CREATE TABLE brands (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, current_revision_id TEXT,
 kind TEXT NOT NULL DEFAULT 'user' CHECK(kind IN ('preset','user')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
 CHECK(kind='user' OR deleted_at IS NULL),
 FOREIGN KEY(id,current_revision_id) REFERENCES brand_revisions(brand_id,id)
 DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE brand_revisions (
 id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
 revision INTEGER NOT NULL CHECK(revision>0), config_json TEXT NOT NULL CHECK(json_valid(config_json)),
 created_at TEXT NOT NULL, UNIQUE(brand_id,revision), UNIQUE(brand_id,id)
);
CREATE TABLE brand_assets (
 revision_id TEXT NOT NULL REFERENCES brand_revisions(id),
 asset_id TEXT NOT NULL REFERENCES assets(id),
 role TEXT NOT NULL CHECK(role IN ('logo_normal','logo_white','logo_transparent','logo_symbol','optional','source')),
 label TEXT NOT NULL DEFAULT '', PRIMARY KEY(revision_id,asset_id,role)
);
CREATE UNIQUE INDEX one_logo_role ON brand_assets(revision_id,role)
 WHERE role IN ('logo_normal','logo_white','logo_transparent','logo_symbol');
CREATE TABLE styles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, current_revision_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('preset','user')),
 origin_style_id TEXT, origin_revision INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
 FOREIGN KEY(id,current_revision_id) REFERENCES style_revisions(style_id,id)
 DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE style_revisions (
 id TEXT PRIMARY KEY, style_id TEXT NOT NULL REFERENCES styles(id),
 revision INTEGER NOT NULL CHECK(revision>0), config_json TEXT NOT NULL CHECK(json_valid(config_json)),
 created_at TEXT NOT NULL, UNIQUE(style_id,revision), UNIQUE(style_id,id)
);
CREATE TABLE decks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '',
 purpose TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
 mode TEXT NOT NULL CHECK(mode IN ('presentation','pitch')),
 brand_revision_id TEXT NOT NULL REFERENCES brand_revisions(id),
 style_revision_id TEXT NOT NULL REFERENCES style_revisions(id),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','final')),
 pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)), archived_at TEXT, deleted_at TEXT,
 origin_deck_id TEXT REFERENCES decks(id),
 lock_version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE deck_tags (
 deck_id TEXT NOT NULL REFERENCES decks(id), tag_id TEXT NOT NULL REFERENCES tags(id),
 PRIMARY KEY(deck_id,tag_id)
);
CREATE TABLE events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, deck_id TEXT REFERENCES decks(id),
 kind TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), created_at TEXT NOT NULL
);
CREATE TABLE deletion_tasks (
 id TEXT PRIMARY KEY, deck_id TEXT NOT NULL REFERENCES decks(id), state TEXT NOT NULL,
 manifest_path TEXT NOT NULL, created_at TEXT NOT NULL, restored_at TEXT
);
CREATE TABLE launches (
 id TEXT PRIMARY KEY, deck_id TEXT REFERENCES decks(id),
 token_hash TEXT NOT NULL UNIQUE,
 requested_via TEXT NOT NULL CHECK(requested_via IN ('start','open','ensure_external')),
 state TEXT NOT NULL CHECK(state IN ('pending','attached','expired','cancelled')),
 requested_at TEXT NOT NULL, expires_at TEXT NOT NULL, attached_at TEXT,
 attached_via TEXT CHECK(attached_via IS NULL OR attached_via IN ('integrated','external','unknown')),
 client_json TEXT CHECK(client_json IS NULL OR json_valid(client_json)),
 CHECK(state!='attached' OR attached_at IS NOT NULL)
);
CREATE INDEX deck_recent ON decks(updated_at DESC,id) WHERE deleted_at IS NULL AND archived_at IS NULL;
CREATE INDEX event_deck ON events(deck_id,seq);
CREATE INDEX launch_pending ON launches(state,expires_at);
