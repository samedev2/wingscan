-- ============================================================================
-- Wingscam Supabase Schema
-- Espelha o panel_events do SQLite local + tabela de controle de sync
-- ============================================================================

-- ============================================================================
-- Tabela principal: espelha panel_events do SQLite
-- ============================================================================
CREATE TABLE IF NOT EXISTS panel_events (
    id          BIGSERIAL PRIMARY KEY,
    -- source_id eh o id do SQLite local (para idempotencia)
    source_id   INTEGER NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'sqlite',
    -- celeiro_id do wingscan (multi-camera no futuro)
    celeiro_id  TEXT,
    -- timestamp ISO 8601 vindo do SQLite
    ts          TIMESTAMPTZ NOT NULL,
    kind        TEXT NOT NULL,
    -- payload JSON (mesmo shape do SQLite)
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- quando foi sincronizado pro Supabase
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- quando foi gerado localmente (pode != synced_at)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- chave unica: source_kind + source_id garante idempotencia
    UNIQUE(source_kind, source_id)
);

CREATE INDEX IF NOT EXISTS idx_panel_events_ts ON panel_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_panel_events_kind ON panel_events (kind);
CREATE INDEX IF NOT EXISTS idx_panel_events_cel ON panel_events (celeiro_id);
CREATE INDEX IF NOT EXISTS idx_panel_events_payload_gin
    ON panel_events USING GIN (payload jsonb_path_ops);

-- ============================================================================
-- Identidades persistentes (espelha identities do SQLite ReID v2)
-- ============================================================================
CREATE TABLE IF NOT EXISTS identities (
    id              BIGSERIAL PRIMARY KEY,
    -- celeiro_id (multi-camera)
    celeiro_id      TEXT,
    -- tipo de ave: pinto / galinha / galo
    tipo            TEXT NOT NULL CHECK (tipo IN ('pinto', 'galinha', 'galo')),
    -- cor identificada
    cor             TEXT,
    -- nome humano (galinha-7, pinto-12, ...)
    nome            TEXT NOT NULL,
    -- primeira/ultima visualizacao
    first_seen      TIMESTAMPTZ NOT NULL,
    last_seen       TIMESTAMPTZ NOT NULL,
    -- contadores
    total_frames    INTEGER DEFAULT 0,
    total_seconds   REAL DEFAULT 0.0,
    -- sincronizado
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    -- chave unica local: wingscan:<celeiro>:<nome>
    source_kind     TEXT NOT NULL DEFAULT 'wingscan',
    local_name      TEXT NOT NULL,
    UNIQUE(source_kind, celeiro_id, local_name)
);

CREATE INDEX IF NOT EXISTS idx_identities_tipo ON identities (tipo);
CREATE INDEX IF NOT EXISTS idx_identities_cor ON identities (cor);
CREATE INDEX IF NOT EXISTS idx_identities_last ON identities (last_seen DESC);

-- ============================================================================
-- Sync state: controla o que ja foi sincronizado pro Supabase
-- ============================================================================
CREATE TABLE IF NOT EXISTS sync_state (
    source_kind     TEXT NOT NULL,             -- 'sqlite' | 'wingscan'
    source_id       INTEGER NOT NULL,
    target_table    TEXT NOT NULL,             -- 'panel_events' | 'identities'
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- dedup
    UNIQUE(source_kind, source_id, target_table)
);

-- ============================================================================
-- Views uteis para dashboard
-- ============================================================================

-- Contagem de identificacoes por tipo nas ultimas 24h
CREATE OR REPLACE VIEW v_recent_counts_by_type AS
SELECT
    tipo,
    COUNT(*) AS identificacoes,
    COUNT(DISTINCT local_name) AS individuos_unicos,
    MAX(last_seen) AS ultima_deteccao
FROM identities
WHERE last_seen > NOW() - INTERVAL '24 hours'
GROUP BY tipo;

-- Galinhas mais agitadas (ultimas 24h)
CREATE OR REPLACE VIEW v_top_agitated AS
SELECT
    i.local_name,
    i.tipo,
    i.cor,
    COUNT(pe.id) AS total_eventos,
    SUM(CASE WHEN pe.kind = 'bird_lost' THEN 1 ELSE 0 END) AS eventos_lost,
    ROUND(
        100.0 * SUM(CASE WHEN pe.kind = 'bird_lost' THEN 1 ELSE 0 END) /
        NULLIF(COUNT(pe.id), 0), 1
    ) AS pct_lost
FROM identities i
JOIN panel_events pe
    ON pe.payload->>'track_id' = SPLIT_PART(i.local_name, '-', 2)
WHERE pe.ts > NOW() - INTERVAL '24 hours'
GROUP BY i.id, i.local_name, i.tipo, i.cor
ORDER BY pct_lost DESC
LIMIT 20;

-- ============================================================================
-- RLS (Row Level Security) - habilite se quiser controle de acesso por usuario
-- Por enquanto, anon key tem acesso total (cuidado em produção!)
-- ============================================================================

-- Para DESABILITAR RLS (acesso publico via anon key):
ALTER TABLE panel_events DISABLE ROW LEVEL SECURITY;
ALTER TABLE identities DISABLE ROW LEVEL SECURITY;
ALTER TABLE sync_state DISABLE ROW LEVEL SECURITY;

-- Para HABILITAR RLS (cada usuario só ve seus celeiros):
-- ALTER TABLE panel_events ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE identities ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sync_state ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "anon_read" ON panel_events FOR SELECT TO anon USING (true);
-- CREATE POLICY "anon_insert" ON panel_events FOR INSERT TO anon WITH CHECK (true);
-- CREATE POLICY "anon_read" ON identities FOR SELECT TO anon USING (true);
-- CREATE POLICY "anon_insert" ON identities FOR INSERT TO anon WITH CHECK (true);

-- ============================================================================
-- Politica de retencao: limpa eventos com mais de 90 dias (rodar como cron)
-- ============================================================================
-- DELETE FROM panel_events WHERE synced_at < NOW() - INTERVAL '90 days';

-- ============================================================================
-- Compat: tabelas criadas antes do DEFAULT NOW() em created_at
-- Esta ALTER eh idempotente (SET DEFAULT pode rodar quantas vezes quiser)
-- ============================================================================
ALTER TABLE panel_events ALTER COLUMN created_at SET DEFAULT NOW();
