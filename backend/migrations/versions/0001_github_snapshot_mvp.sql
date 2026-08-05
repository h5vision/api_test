BEGIN;


CREATE TABLE IF NOT EXISTS snapshot_mvp_repositories (
    tenant_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'github'),
    provider_repository_id TEXT NOT NULL,
    repository_full_name TEXT NOT NULL,
    repository_url TEXT NOT NULL,
    default_branch TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'public'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, repository_id),
    UNIQUE (tenant_id, provider, provider_repository_id),
    UNIQUE (tenant_id, provider, repository_full_name),
    CHECK (octet_length(tenant_id) BETWEEN 1 AND 255),
    CHECK (octet_length(repository_id) BETWEEN 1 AND 255),
    CHECK (octet_length(repository_full_name) BETWEEN 3 AND 201),
    CHECK (octet_length(repository_url) BETWEEN 1 AND 2048),
    CHECK (octet_length(default_branch) BETWEEN 1 AND 255)
);


CREATE TABLE IF NOT EXISTS snapshot_mvp_snapshots (
    tenant_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    snapshot_type TEXT NOT NULL CHECK (snapshot_type = 'commit'),
    commit_sha TEXT NOT NULL CHECK (
        commit_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'
    ),
    tree_sha TEXT NOT NULL CHECK (
        tree_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'
    ),
    fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    verified_by TEXT NOT NULL CHECK (verified_by = 'github'),
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, snapshot_id),
    UNIQUE (tenant_id, repository_id, commit_sha),
    UNIQUE (tenant_id, repository_id, fingerprint),
    FOREIGN KEY (tenant_id, repository_id)
        REFERENCES snapshot_mvp_repositories (tenant_id, repository_id)
        ON DELETE RESTRICT,
    CHECK (octet_length(snapshot_id) BETWEEN 1 AND 255)
);


CREATE TABLE IF NOT EXISTS snapshot_mvp_locators (
    tenant_id TEXT NOT NULL,
    locator_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'github'),
    access_mode TEXT NOT NULL CHECK (access_mode = 'backend-proxy'),
    availability TEXT NOT NULL CHECK (
        availability IN ('durable', 'unavailable')
    ),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, locator_id),
    UNIQUE (tenant_id, snapshot_id, provider, access_mode),
    FOREIGN KEY (tenant_id, snapshot_id)
        REFERENCES snapshot_mvp_snapshots (tenant_id, snapshot_id)
        ON DELETE CASCADE,
    CHECK (octet_length(locator_id) BETWEEN 1 AND 255),
    CHECK (octet_length(details::text) <= 16384)
);


CREATE INDEX IF NOT EXISTS idx_snapshot_mvp_snapshots_repository_created
    ON snapshot_mvp_snapshots (tenant_id, repository_id, created_at DESC);


CREATE INDEX IF NOT EXISTS idx_snapshot_mvp_locators_snapshot
    ON snapshot_mvp_locators (tenant_id, snapshot_id);


COMMIT;