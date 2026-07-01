-- Token Deployments table for Darkroom
-- Stores all token deployment records across chains

CREATE TABLE IF NOT EXISTS token_deployments (
    deployment_id TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decimals INTEGER NOT NULL DEFAULT 18,
    total_supply TEXT NOT NULL,
    contract_address TEXT,
    deployer_address TEXT NOT NULL,
    tx_hash TEXT,
    block_number INTEGER,
    status TEXT NOT NULL DEFAULT 'deployed',
    owner_address TEXT,
    mintable BOOLEAN DEFAULT TRUE,
    burnable BOOLEAN DEFAULT TRUE,
    pausable BOOLEAN DEFAULT FALSE,
    blacklist_enabled BOOLEAN DEFAULT TRUE,
    max_wallet_limit TEXT,
    max_tx_limit TEXT,
    trading_enabled BOOLEAN DEFAULT TRUE,
    anti_bot_delay INTEGER DEFAULT 0,
    max_supply TEXT,
    metadata_uri TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    extra JSONB DEFAULT '{}'
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_token_deployments_chain ON token_deployments(chain);
CREATE INDEX IF NOT EXISTS idx_token_deployments_status ON token_deployments(status);
CREATE INDEX IF NOT EXISTS idx_token_deployments_contract ON token_deployments(contract_address);
CREATE INDEX IF NOT EXISTS idx_token_deployments_symbol ON token_deployments(symbol);

-- Enable RLS (admin only)
ALTER TABLE token_deployments ENABLE ROW LEVEL SECURITY;

-- Admin can do everything
CREATE POLICY token_deployments_admin_all ON token_deployments
    FOR ALL
    TO authenticated
    USING (auth.jwt() ->> 'role' = 'admin')
    WITH CHECK (auth.jwt() ->> 'role' = 'admin');

-- Public can read deployed tokens (for verification)
CREATE POLICY token_deployments_public_read ON token_deployments
    FOR SELECT
    TO anon, authenticated
    USING (status = 'deployed');
