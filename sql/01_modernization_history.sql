-- =============================================================
-- Tabela de persistência da pipeline de modernização
-- Executada automaticamente pelo Docker ao subir o container PostgreSQL
-- =============================================================

CREATE TABLE IF NOT EXISTS modernization_history (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id  VARCHAR(36) NOT NULL UNIQUE,
    source_code TEXT        NOT NULL,
    generated_code TEXT,
    report      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status      VARCHAR(20) NOT NULL DEFAULT 'failure'
                    CHECK (status IN ('success', 'partial', 'failure')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mh_status     ON modernization_history(status);
CREATE INDEX IF NOT EXISTS idx_mh_created_at ON modernization_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mh_request_id ON modernization_history(request_id);

COMMENT ON TABLE modernization_history IS
    'Persiste cada execução da pipeline SQL→Python, independente do desfecho.';
COMMENT ON COLUMN modernization_history.report IS
    'Relatório estruturado das etapas: parsing, analysis, generation, validation.';
COMMENT ON COLUMN modernization_history.status IS
    'success = código gerado e validado; partial = gerado mas com avisos; failure = falhou.';
