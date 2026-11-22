CREATE TABLE demo_quotas (session_id TEXT NOT NULL REFERENCES demo_sessions, bucket BIGINT NOT NULL, requests INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(session_id,bucket));
