CREATE TABLE session_creation_limits (
    client_hash TEXT NOT NULL,
    window_seconds INTEGER NOT NULL,
    bucket BIGINT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(client_hash,window_seconds,bucket)
);
