"""Adopt the pre-P2 Vision PostgreSQL schema as the Alembic baseline.

This revision is intentionally a historical baseline, not the P2 Vector domain.
Existing databases that already match this shape must be verified and stamped;
fresh databases execute this revision normally.

Revision ID: p2a_0001_baseline
Revises: None
"""
from __future__ import annotations

from alembic import op

revision = "p2a_0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    # Alembic creates version_num as VARCHAR(32) by default, while the explicit
    # P2 revision identifiers are longer. Widen it before advancing the chain.
    _sql("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")
    _sql("""
    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        current_snapshot_id TEXT,
        manifest_sha256 TEXT,
        git_commit_sha TEXT,
        git_branch TEXT,
        git_dirty BOOLEAN,
        git_committed_at TIMESTAMPTZ,
        source_modified_at TIMESTAMPTZ,
        index_completed_at TIMESTAMPTZ,
        embedding_model TEXT NOT NULL,
        embedding_model_id TEXT,
        index_version TEXT NOT NULL,
        index_status TEXT NOT NULL DEFAULT 'ready',
        active_generation_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS repository_sources (
        source_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        root_relative_path TEXT NOT NULL,
        repository_url TEXT,
        default_branch TEXT,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        last_revision TEXT,
        last_synced_at TIMESTAMPTZ,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        provider_repository_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_repository_sources_project ON repository_sources (project_id)")
    _sql("""
    CREATE TABLE IF NOT EXISTS project_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        source_id TEXT NOT NULL REFERENCES repository_sources(source_id) ON DELETE RESTRICT,
        revision TEXT,
        git_branch TEXT,
        git_dirty BOOLEAN,
        git_committed_at TIMESTAMPTZ,
        manifest_sha256 TEXT,
        file_count INTEGER NOT NULL DEFAULT 0,
        total_bytes BIGINT NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        snapshot_kind TEXT NOT NULL DEFAULT 'working-tree',
        tree_sha TEXT,
        fingerprint TEXT,
        verified_by TEXT NOT NULL DEFAULT 'local',
        verified_at TIMESTAMPTZ,
        locator JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )
    """)
    # Existing Vision databases may already have the pre-P2 snapshot table.
    # Normalize it before creating indexes that reference P2 columns.
    _sql("ALTER TABLE project_snapshots ALTER COLUMN manifest_sha256 DROP NOT NULL")
    _sql("""
    ALTER TABLE project_snapshots
        ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default',
        ADD COLUMN IF NOT EXISTS snapshot_kind TEXT NOT NULL DEFAULT 'working-tree',
        ADD COLUMN IF NOT EXISTS tree_sha TEXT,
        ADD COLUMN IF NOT EXISTS fingerprint TEXT,
        ADD COLUMN IF NOT EXISTS verified_by TEXT NOT NULL DEFAULT 'local',
        ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS locator JSONB NOT NULL DEFAULT '{}'::jsonb
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_project_snapshots_project ON project_snapshots (project_id, created_at DESC)")
    _sql("CREATE INDEX IF NOT EXISTS idx_project_snapshots_repository ON project_snapshots (source_id, created_at DESC)")
    _sql("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_project_snapshots_fingerprint
    ON project_snapshots (fingerprint)
    WHERE fingerprint IS NOT NULL AND snapshot_kind <> 'upload'
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS snapshot_entries (
        snapshot_id TEXT NOT NULL REFERENCES project_snapshots(snapshot_id) ON DELETE CASCADE,
        relative_path TEXT NOT NULL,
        name TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        language TEXT,
        size_bytes BIGINT NOT NULL DEFAULT 0,
        content_sha256 TEXT,
        content TEXT,
        indexable BOOLEAN NOT NULL DEFAULT FALSE,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        PRIMARY KEY (snapshot_id, relative_path)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_snapshot_entries_path ON snapshot_entries (snapshot_id, relative_path)")
    _sql("""
    CREATE TABLE IF NOT EXISTS index_generations (
        generation_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        snapshot_id TEXT NOT NULL REFERENCES project_snapshots(snapshot_id) ON DELETE CASCADE,
        collection_name TEXT NOT NULL,
        embedding_model TEXT NOT NULL,
        embedding_model_id TEXT,
        index_version TEXT NOT NULL,
        status TEXT NOT NULL,
        file_count INTEGER NOT NULL DEFAULT 0,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        activated_at TIMESTAMPTZ
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_index_generations_project ON index_generations (project_id, created_at DESC)")
    _sql("""
    CREATE TABLE IF NOT EXISTS generation_chunks (
        generation_id TEXT NOT NULL REFERENCES index_generations(generation_id) ON DELETE CASCADE,
        chunk_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        external_point_id UUID NOT NULL,
        content_sha256 TEXT NOT NULL,
        content TEXT NOT NULL,
        line_start INTEGER,
        line_end INTEGER,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        PRIMARY KEY (generation_id, chunk_id)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_generation_chunks_document ON generation_chunks (generation_id, document_id)")
    _sql("""
    CREATE TABLE IF NOT EXISTS repository_index_jobs (
        job_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES repository_sources(source_id) ON DELETE RESTRICT,
        project_id TEXT NOT NULL,
        snapshot_id TEXT,
        generation_id TEXT,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        force_run BOOLEAN NOT NULL DEFAULT FALSE,
        files_total INTEGER NOT NULL DEFAULT 0,
        files_processed INTEGER NOT NULL DEFAULT 0,
        chunks_stored INTEGER NOT NULL DEFAULT 0,
        bytes_total BIGINT NOT NULL DEFAULT 0,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_repository_jobs_source ON repository_index_jobs (source_id, created_at DESC)")
    _sql("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_repository_jobs_one_active_source
    ON repository_index_jobs (source_id)
    WHERE status NOT IN ('completed', 'failed', 'paused')
    """)

    # Legacy document/index registry remains part of the baseline because it
    # contains real MVP-era data. P2 later migrates/retire structures explicitly.
    _sql("""
    CREATE TABLE IF NOT EXISTS document_versions (
        document_version_id UUID PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        document_id TEXT NOT NULL,
        path TEXT,
        language TEXT,
        content_sha256 TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        is_current BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (project_id, document_id, content_sha256)
    )
    """)
    _sql("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_document_versions_one_current
    ON document_versions (project_id, document_id) WHERE is_current
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS document_chunks (
        chunk_id TEXT PRIMARY KEY,
        document_version_id UUID NOT NULL REFERENCES document_versions(document_version_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        content TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        line_start INTEGER,
        line_end INTEGER,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_document_chunks_project_document ON document_chunks (project_id, document_id)")
    _sql("""
    CREATE TABLE IF NOT EXISTS vector_mappings (
        chunk_id TEXT PRIMARY KEY REFERENCES document_chunks(chunk_id) ON DELETE CASCADE,
        external_point_id UUID NOT NULL,
        collection_name TEXT NOT NULL,
        embedding_model TEXT NOT NULL,
        index_version TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS runtime_service_settings (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
        groq_enabled BOOLEAN NOT NULL,
        groq_base_url TEXT NOT NULL,
        groq_model TEXT NOT NULL,
        default_model_id TEXT NOT NULL,
        vector_host TEXT NOT NULL,
        vector_port INTEGER NOT NULL CHECK (vector_port BETWEEN 1 AND 65535),
        vector_collection TEXT NOT NULL,
        embedding_model TEXT NOT NULL,
        index_version TEXT NOT NULL,
        embedding_deployment TEXT,
        embedding_provider TEXT,
        embedding_base_url TEXT,
        embedding_model_id TEXT,
        embedding_dimension INTEGER,
        embedding_batch_size INTEGER,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS runtime_network_settings (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
        frontend_ip INET NOT NULL,
        frontend_port INTEGER NOT NULL CHECK (frontend_port BETWEEN 1 AND 65535),
        backendai_ip INET NOT NULL,
        backendai_port INTEGER NOT NULL CHECK (backendai_port BETWEEN 1 AND 65535),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS ai_provider_configs (
        provider_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        protocol TEXT NOT NULL CHECK (protocol IN ('ollama', 'openai')),
        base_url TEXT NOT NULL,
        auth_type TEXT NOT NULL CHECK (auth_type IN ('none', 'bearer', 'x-api-key')),
        api_key_ciphertext TEXT,
        api_key_hint TEXT,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        deployment_type TEXT NOT NULL CHECK (deployment_type IN ('cloud', 'local', 'remote_server')),
        status TEXT NOT NULL DEFAULT 'unknown',
        error TEXT,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        model_count INTEGER NOT NULL DEFAULT 0,
        last_checked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS ai_provider_models (
        provider_id TEXT NOT NULL REFERENCES ai_provider_configs(provider_id) ON DELETE CASCADE,
        model_name TEXT NOT NULL,
        discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (provider_id, model_name)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_ai_provider_models_provider ON ai_provider_models (provider_id, model_name)")
    _sql("""
    CREATE TABLE IF NOT EXISTS model_access_policies (
        model_id TEXT PRIMARY KEY,
        enabled BOOLEAN NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS client_connections (
        client_id TEXT PRIMARY KEY,
        client_type TEXT NOT NULL,
        project_id TEXT,
        client_version TEXT,
        last_event TEXT NOT NULL,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_client_connections_type_seen ON client_connections (client_type, last_seen_at DESC)")
    _sql("""
    CREATE TABLE IF NOT EXISTS frontend_api_activity (
        client_id TEXT NOT NULL,
        method TEXT NOT NULL,
        path TEXT NOT NULL,
        request_count BIGINT NOT NULL DEFAULT 0,
        success_count BIGINT NOT NULL DEFAULT 0,
        error_count BIGINT NOT NULL DEFAULT 0,
        last_status_code INTEGER,
        last_request_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_response_at TIMESTAMPTZ,
        last_success_at TIMESTAMPTZ,
        last_duration_ms INTEGER,
        last_request_id TEXT,
        PRIMARY KEY (client_id, method, path)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_frontend_api_activity_path_response ON frontend_api_activity (method, path, last_response_at DESC)")
    _sql("""
    CREATE TABLE IF NOT EXISTS communication_events (
        event_id BIGSERIAL PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        request_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        direction TEXT NOT NULL,
        phase TEXT NOT NULL,
        status TEXT NOT NULL,
        method TEXT,
        path TEXT,
        client_id TEXT,
        project_id TEXT,
        status_code INTEGER,
        duration_ms INTEGER,
        provider TEXT,
        model TEXT,
        source_count INTEGER,
        error TEXT,
        details JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_communication_events_occurred ON communication_events (occurred_at DESC, event_id DESC)")
    _sql("CREATE INDEX IF NOT EXISTS idx_communication_events_request ON communication_events (request_id, occurred_at)")
    _sql("""
    CREATE TABLE IF NOT EXISTS chat_audit_logs (
        request_id TEXT PRIMARY KEY,
        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ,
        client_id TEXT,
        project_id TEXT,
        session_id TEXT,
        requested_model_id TEXT,
        message TEXT,
        message_truncated BOOLEAN NOT NULL DEFAULT FALSE,
        history_count INTEGER NOT NULL DEFAULT 0,
        context_chars INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'received',
        status_code INTEGER,
        answer TEXT,
        answer_truncated BOOLEAN NOT NULL DEFAULT FALSE,
        used_model_id TEXT,
        provider TEXT,
        source_count INTEGER,
        duration_ms INTEGER,
        error TEXT
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_chat_audit_logs_received ON chat_audit_logs (received_at DESC)")
    _sql("""
    CREATE TABLE IF NOT EXISTS frontend_registration_events (
        event_id BIGSERIAL PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        request_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        client_id TEXT,
        instance_id TEXT,
        client_name TEXT,
        declared_user TEXT,
        client_version TEXT,
        source_ip TEXT,
        registration_type TEXT,
        identification_method TEXT,
        is_first_connection BOOLEAN NOT NULL DEFAULT FALSE,
        reason TEXT
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_frontend_registration_events_occurred ON frontend_registration_events (occurred_at DESC, event_id DESC)")
    _sql("CREATE INDEX IF NOT EXISTS idx_frontend_registration_events_request ON frontend_registration_events (request_id, occurred_at, event_id)")

    _sql("""
    CREATE TABLE IF NOT EXISTS frontend_clients (
        client_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ip INET NOT NULL,
        port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        instance_id TEXT,
        registration_type TEXT NOT NULL DEFAULT 'admin',
        last_seen_ip INET,
        last_seen_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    # The legacy table does not have instance identity columns. Add them before
    # the partial unique index below is evaluated.
    _sql("""
    ALTER TABLE frontend_clients
        ADD COLUMN IF NOT EXISTS instance_id TEXT,
        ADD COLUMN IF NOT EXISTS registration_type TEXT NOT NULL DEFAULT 'admin',
        ADD COLUMN IF NOT EXISTS last_seen_ip INET,
        ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ
    """)
    _sql("ALTER TABLE frontend_clients DROP CONSTRAINT IF EXISTS frontend_clients_ip_port_key")
    _sql("CREATE INDEX IF NOT EXISTS idx_frontend_clients_enabled_ip ON frontend_clients (enabled, ip)")
    _sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_frontend_clients_instance_id ON frontend_clients (instance_id) WHERE instance_id IS NOT NULL")

    _sql("""
    CREATE TABLE IF NOT EXISTS frontend_metadata (
        metadata_id UUID PRIMARY KEY,
        project_id TEXT NOT NULL,
        session_id TEXT,
        scope TEXT NOT NULL CHECK (scope IN ('project', 'session', 'document', 'custom')),
        entity_id TEXT NOT NULL,
        source TEXT NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (project_id, scope, entity_id)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_frontend_metadata_project_updated ON frontend_metadata (project_id, updated_at DESC)")
    _sql("""
    CREATE TABLE IF NOT EXISTS frontend_documents (
        project_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        path TEXT,
        language TEXT,
        type TEXT NOT NULL,
        string_value TEXT,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (project_id, document_id)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_frontend_documents_project_updated ON frontend_documents (project_id, updated_at DESC)")

    # Normalize only the schema evolutions that pre-P2 runtime DDL used to apply.
    # These operations are idempotent and do not rewrite application history.
    _sql("""
    ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS manifest_sha256 TEXT,
        ADD COLUMN IF NOT EXISTS git_commit_sha TEXT,
        ADD COLUMN IF NOT EXISTS git_branch TEXT,
        ADD COLUMN IF NOT EXISTS git_dirty BOOLEAN,
        ADD COLUMN IF NOT EXISTS git_committed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS source_modified_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS index_completed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS embedding_model_id TEXT,
        ADD COLUMN IF NOT EXISTS active_generation_id TEXT
    """)
    _sql("""
    ALTER TABLE repository_sources
        ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default',
        ADD COLUMN IF NOT EXISTS provider_repository_id TEXT
    """)
    _sql("ALTER TABLE index_generations ADD COLUMN IF NOT EXISTS embedding_model_id TEXT")
    _sql("""
    ALTER TABLE runtime_service_settings
        ADD COLUMN IF NOT EXISTS embedding_deployment TEXT,
        ADD COLUMN IF NOT EXISTS embedding_provider TEXT,
        ADD COLUMN IF NOT EXISTS embedding_base_url TEXT,
        ADD COLUMN IF NOT EXISTS embedding_model_id TEXT,
        ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER,
        ADD COLUMN IF NOT EXISTS embedding_batch_size INTEGER
    """)
    _sql("ALTER TABLE frontend_documents ADD COLUMN IF NOT EXISTS string_value TEXT")
    _sql("ALTER TABLE frontend_documents ALTER COLUMN string_value DROP NOT NULL")
    _sql("ALTER TABLE frontend_documents ADD COLUMN IF NOT EXISTS type TEXT")
    _sql("ALTER TABLE frontend_documents ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb")
    _sql("""
    UPDATE frontend_documents
    SET type = string_value
    WHERE type IS NULL AND string_value IS NOT NULL
    """)


def downgrade() -> None:
    raise RuntimeError(
        "The P2-A baseline is an adoption boundary and cannot be downgraded "
        "without destroying pre-existing Vision data."
    )
