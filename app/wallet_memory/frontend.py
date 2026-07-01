"""
WalletSafe Frontend — Entity explorer, wallet search, risk page.
===============================================================
Served at /walletsafe/ as a single-page app.
Hits /api/v1/wallet/* endpoints directly via fetch().
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["walletsafe"])

WALLETSAFE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WalletSafe — Wallet Intelligence Explorer</title>
<style>
  :root {
    --bg: #0a0a0f;
    --card: #12121a;
    --border: #1e1e2e;
    --text: #e0e0e8;
    --muted: #8888a0;
    --accent: #6c5ce7;
    --danger: #ff4757;
    --warning: #ffa502;
    --safe: #2ed573;
    --info: #1e90ff;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:'Inter',system-ui,sans-serif; min-height:100vh; }

  nav { background:var(--card); border-bottom:1px solid var(--border); padding:12px 24px; display:flex; align-items:center; gap:24px; }
  nav .logo { font-size:20px; font-weight:700; color:var(--accent); letter-spacing:-0.5px; }
  nav .tab { color:var(--muted); cursor:pointer; padding:6px 14px; border-radius:6px; font-size:14px; transition:all .2s; }
  nav .tab:hover { color:var(--text); background:var(--border); }
  nav .tab.active { color:var(--accent); background:rgba(108,92,231,0.15); }

  .container { max-width:1200px; margin:0 auto; padding:24px; }

  /* Search bar */
  .search-bar { display:flex; gap:12px; margin-bottom:24px; }
  .search-bar input { flex:1; padding:12px 16px; background:var(--card); border:1px solid var(--border); border-radius:8px; color:var(--text); font-size:15px; outline:none; }
  .search-bar input:focus { border-color:var(--accent); }
  .search-bar select { padding:10px 12px; background:var(--card); border:1px solid var(--border); border-radius:8px; color:var(--text); font-size:14px; }
  .search-bar button { padding:10px 24px; background:var(--accent); color:#fff; border:none; border-radius:8px; font-size:15px; cursor:pointer; font-weight:600; }
  .search-bar button:hover { opacity:0.9; }

  /* Cards */
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:16px; }
  .card h3 { font-size:16px; color:var(--muted); margin-bottom:12px; text-transform:uppercase; letter-spacing:0.5px; }

  /* Risk gauge */
  .risk-gauge { display:flex; align-items:center; gap:16px; margin:16px 0; }
  .risk-score { font-size:48px; font-weight:800; line-height:1; }
  .risk-label { font-size:18px; font-weight:600; padding:4px 12px; border-radius:6px; }
  .risk-minimal { color:var(--safe); background:rgba(46,213,115,0.1); }
  .risk-low { color:#7bed9f; background:rgba(123,237,159,0.1); }
  .risk-medium { color:var(--warning); background:rgba(255,165,2,0.1); }
  .risk-high { color:#ff6348; background:rgba(255,99,72,0.1); }
  .risk-critical { color:var(--danger); background:rgba(255,71,87,0.15); }
  .risk-unknown { color:var(--muted); background:rgba(136,136,160,0.1); }

  /* Risk bar */
  .risk-bar { height:8px; background:var(--border); border-radius:4px; overflow:hidden; margin-top:8px; }
  .risk-bar-fill { height:100%; border-radius:4px; transition:width .5s ease; }

  /* Profile grid */
  .profile-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media(max-width:768px) { .profile-grid { grid-template-columns:1fr; } }
  .profile-field { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border); }
  .profile-field:last-child { border:none; }
  .profile-field .label { color:var(--muted); font-size:13px; }
  .profile-field .value { font-weight:500; font-size:14px; word-break:break-all; }

  /* Tags */
  .tag { display:inline-block; padding:3px 8px; border-radius:4px; font-size:12px; margin:2px; }
  .tag-exchange { background:rgba(30,144,255,0.15); color:var(--info); }
  .tag-scam { background:rgba(255,71,87,0.15); color:var(--danger); }
  .tag-sanctioned { background:rgba(255,71,87,0.25); color:var(--danger); }
  .tag-defi { background:rgba(108,92,231,0.15); color:var(--accent); }
  .tag-bridge { background:rgba(255,165,2,0.15); color:var(--warning); }
  .tag-mev { background:rgba(255,165,2,0.15); color:var(--warning); }
  .tag-deployer { background:rgba(46,213,115,0.15); color:var(--safe); }
  .tag-wallet { background:rgba(136,136,160,0.15); color:var(--muted); }
  .tag-unknown { background:rgba(136,136,160,0.1); color:var(--muted); }

  /* Wallet list */
  .wallet-item { display:flex; align-items:center; gap:12px; padding:10px 0; border-bottom:1px solid var(--border); }
  .wallet-item:last-child { border:none; }
  .wallet-addr { font-family:monospace; font-size:13px; color:var(--info); cursor:pointer; }
  .wallet-addr:hover { text-decoration:underline; }
  .wallet-chain { font-size:11px; padding:2px 6px; border-radius:3px; background:rgba(108,92,231,0.15); color:var(--accent); }

  /* Deployer history */
  .deploy-item { padding:10px 0; border-bottom:1px solid var(--border); font-size:13px; }
  .deploy-item:last-child { border:none; }
  .deploy-token { font-family:monospace; color:var(--info); }

  /* Loading state */
  .loading { display:flex; align-items:center; justify-content:center; padding:60px; color:var(--muted); }
  .spinner { width:24px; height:24px; border:3px solid var(--border); border-top-color:var(--accent); border-radius:50%; animation:spin 0.8s linear infinite; margin-right:12px; }
  @keyframes spin { to { transform:rotate(360deg); } }

  /* Empty state */
  .empty { text-align:center; padding:60px; color:var(--muted); }
  .empty h2 { font-size:20px; margin-bottom:8px; color:var(--text); }

  /* Error */
  .error { background:rgba(255,71,87,0.1); border:1px solid rgba(255,71,87,0.3); border-radius:8px; padding:16px; color:var(--danger); margin-bottom:16px; }

  /* Stats bar */
  .stats-bar { display:flex; gap:24px; margin-bottom:24px; flex-wrap:wrap; }
  .stat-item { display:flex; flex-direction:column; }
  .stat-value { font-size:28px; font-weight:700; }
  .stat-label { font-size:12px; color:var(--muted); text-transform:uppercase; }

  /* Entity graph placeholder */
  .entity-graph { min-height:200px; display:flex; flex-wrap:wrap; gap:8px; padding:12px; }
  .entity-node { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:8px 12px; font-size:12px; cursor:pointer; transition:all .2s; }
  .entity-node:hover { border-color:var(--accent); }
  .entity-node.center { border-color:var(--accent); background:rgba(108,92,231,0.1); }
  .entity-node .addr { font-family:monospace; color:var(--info); }
  .entity-node .method { color:var(--muted); font-size:10px; margin-top:2px; }
</style>
</head>
<body>

<nav>
  <div class="logo">WalletSafe</div>
  <div class="tab active" data-tab="search" onclick="switchTab('search')">Search</div>
  <div class="tab" data-tab="dashboard" onclick="switchTab('dashboard')">Dashboard</div>
</nav>

<!-- Search Tab -->
<div id="tab-search" class="container">
  <div class="search-bar">
    <input id="search-input" type="text" placeholder="Enter wallet address (0x..., Solana...)" onkeydown="if(event.key==='Enter')searchWallet()">
    <select id="chain-select">
      <option value="solana">Solana</option>
      <option value="ethereum">Ethereum</option>
      <option value="bsc">BSC</option>
      <option value="polygon">Polygon</option>
      <option value="arbitrum">Arbitrum</option>
      <option value="base">Base</option>
    </select>
    <button onclick="searchWallet()">Analyze</button>
  </div>

  <div id="search-results"></div>
  <div id="empty-state" class="empty">
    <h2>Wallet Intelligence Explorer</h2>
    <p>Enter a wallet address to see risk scores, entity clusters, labels, and deployer history</p>
  </div>
</div>

<!-- Dashboard Tab -->
<div id="tab-dashboard" class="container" style="display:none">
  <div class="stats-bar" id="dashboard-stats"></div>
  <div class="profile-grid" id="dashboard-content"></div>
</div>

<script>
const API = '/api/v1/wallet';
let currentTab = 'search';

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.getElementById('tab-search').style.display = tab === 'search' ? 'block' : 'none';
  document.getElementById('tab-dashboard').style.display = tab === 'dashboard' ? 'block' : 'none';
  if (tab === 'dashboard') loadDashboard();
}

async function api(endpoint, opts = {}) {
  try {
    const r = await fetch(API + endpoint, opts);
    return await r.json();
  } catch(e) {
    console.error('API error:', e);
    return null;
  }
}

async function searchWallet() {
  const addr = document.getElementById('search-input').value.trim();
  const chain = document.getElementById('chain-select').value;
  if (!addr) return;

  const results = document.getElementById('search-results');
  const empty = document.getElementById('empty-state');
  empty.style.display = 'none';
  results.innerHTML = '<div class="loading"><div class="spinner"></div>Analyzing wallet...</div>';

  const profile = await api(`/${addr}/profile?chain=${chain}`);
  if (!profile) {
    results.innerHTML = '<div class="error">Failed to fetch wallet profile. Check the address and try again.</div>';
    return;
  }

  results.innerHTML = renderProfile(profile, chain);
}

function renderProfile(p, chain) {
  const score = p.risk_score || 0;
  const level = p.risk_level || 'unknown';
  const scoreColor = riskColor(score);
  const barColor = riskBarColor(level);

  let html = '';

  // Risk Card
  html += `<div class="card">
    <h3>Risk Assessment</h3>
    <div class="risk-gauge">
      <div class="risk-score" style="color:${scoreColor}">${Math.round(score)}</div>
      <div>
        <span class="risk-label risk-${level}">${level.toUpperCase()}</span>
        <div class="risk-bar" style="width:200px;margin-top:8px">
          <div class="risk-bar-fill" style="width:${score}%;background:${barColor}"></div>
        </div>
      </div>
    </div>
    ${p.scam_associations?.length ? `<div style="margin-top:12px"><strong>Scam Associations:</strong> ${p.scam_associations.map(s => `<span class="tag tag-scam">${s.type} (${s.source})</span>`).join(' ')}</div>` : ''}
  </div>`;

  // Profile Info
  html += `<div class="profile-grid">
    <div class="card">
      <h3>Wallet Identity</h3>
      <div class="profile-field"><span class="label">Address</span><span class="value" style="font-family:monospace;font-size:12px">${p.address}</span></div>
      <div class="profile-field"><span class="label">Chain</span><span class="value">${p.chain}</span></div>
      <div class="profile-field"><span class="label">Entity</span><span class="value">${p.entity_label || p.entity_id?.slice(0,16) + '...' || 'Unknown'}</span></div>
      <div class="profile-field"><span class="label">Category</span><span class="value"><span class="tag tag-${p.entity_category || 'unknown'}">${p.entity_category || 'unknown'}</span></span></div>
      <div class="profile-field"><span class="label">Confidence</span><span class="value">${(p.confidence * 100).toFixed(0)}%</span></div>
      ${p.total_transactions ? `<div class="profile-field"><span class="label">Transactions</span><span class="value">${p.total_transactions.toLocaleString()}</span></div>` : ''}
      ${p.total_volume ? `<div class="profile-field"><span class="label">Volume</span><span class="value">$${p.total_volume.toLocaleString()}</span></div>` : ''}
    </div>

    <div class="card">
      <h3>Labels (${p.labels?.length || 0})</h3>
      ${p.labels?.length ? p.labels.map(l => `<div class="profile-field"><span class="label">${l.source}</span><span class="value"><span class="tag tag-${l.category || 'unknown'}">${l.label || l.category}</span></span></div>`).join('') : '<div style="color:var(--muted);padding:20px;text-align:center">No labels found</div>'}
    </div>
  </div>`;

  // Linked Wallets / Entity
  if (p.linked_wallets?.length) {
    html += `<div class="card">
      <h3>Linked Wallets (${p.linked_wallets.length})</h3>
      <div class="entity-graph">
        <div class="entity-node center">
          <div class="addr">${p.address.slice(0,10)}...${p.address.slice(-6)}</div>
          <div class="method">primary</div>
        </div>
        ${p.linked_wallets.map(w => `<div class="entity-node" onclick="drillDown('${w.address}','${w.chain}')">
          <div class="addr">${w.address.slice(0,10)}...${w.address.slice(-6)}</div>
          <div class="method">${w.heuristic || w.method || 'linked'} · ${w.chain} · ${(w.confidence * 100).toFixed(0)}%</div>
        </div>`).join('')}
      </div>
    </div>`;
  }

  // Cross-chain
  if (p.cross_chain_presence?.length) {
    html += `<div class="card"><h3>Cross-Chain Presence</h3>
      ${p.cross_chain_presence.map(b => `<div class="wallet-item">
        <span class="wallet-addr" onclick="drillDown('${b.dest_address}','${b.dest_chain}')">${b.dest_address.slice(0,12)}...${b.dest_address.slice(-6)}</span>
        <span class="wallet-chain">${b.dest_chain}</span>
        <span style="color:var(--muted);font-size:12px">via ${b.bridge_name}</span>
      </div>`).join('')}
    </div>`;
  }

  // Deployer History
  if (p.deployer_history?.length) {
    html += `<div class="card"><h3>Deployer History (${p.deployer_history.length})</h3>
      ${p.deployer_history.map(d => `<div class="deploy-item">
        <span class="deploy-token">${d.token_symbol || d.token_address?.slice(0,12)}</span>
        <span style="margin-left:8px">${d.token_name || ''}</span>
        ${d.is_scam_related ? '<span class="tag tag-scam" style="margin-left:8px">SCAM</span>' : ''}
        <span style="float:right;color:var(--muted)">${d.deployed_at ? new Date(d.deployed_at).toLocaleDateString() : ''} · ${d.outcome || 'unknown'}</span>
      </div>`).join('')}
    </div>`;
  }

  return html;
}

function drillDown(addr, chain) {
  document.getElementById('search-input').value = addr;
  document.getElementById('chain-select').value = chain;
  searchWallet();
}

function riskColor(score) {
  if (score >= 80) return 'var(--danger)';
  if (score >= 60) return '#ff6348';
  if (score >= 40) return 'var(--warning)';
  if (score >= 20) return '#7bed9f';
  return 'var(--safe)';
}

function riskBarColor(level) {
  const m = {critical:'var(--danger)',high:'#ff6348',medium:'var(--warning)',low:'#7bed9f',minimal:'var(--safe)',unknown:'var(--muted)'};
  return m[level] || 'var(--muted)';
}

async function loadDashboard() {
  const stats = document.getElementById('dashboard-stats');
  const content = document.getElementById('dashboard-content');
  stats.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';

  const [health, labelStats] = await Promise.all([
    api('/health'),
    api('/labels/stats'),
  ]);

  let statsHtml = '';
  if (health) {
    statsHtml += `
      <div class="stat-item"><span class="stat-value" style="color:${health.storage?.redis ? 'var(--safe)' : 'var(--danger)'}">${health.storage?.redis ? 'Online' : 'Offline'}</span><span class="stat-label">Redis</span></div>
      <div class="stat-item"><span class="stat-value" style="color:${health.storage?.clickhouse ? 'var(--safe)' : 'var(--warning)'}">${health.storage?.clickhouse ? 'Online' : 'Offline'}</span><span class="stat-label">ClickHouse</span></div>
      <div class="stat-item"><span class="stat-value">${health.clustering?.wallets || 0}</span><span class="stat-label">Cluster Nodes</span></div>
    `;
  }
  if (labelStats) {
    const c = labelStats.last_counts || {};
    statsHtml += `
      <div class="stat-item"><span class="stat-value">${c.total || 0}</span><span class="stat-label">Total Labels</span></div>
      <div class="stat-item"><span class="stat-value">${c.ofac || 0}</span><span class="stat-label">OFAC Sanctions</span></div>
      <div class="stat-item"><span class="stat-value">${c.etherscan_combined || 0}</span><span class="stat-label">Etherscan Labels</span></div>
      <div class="stat-item"><span class="stat-value">${c.solana_cex || 0}</span><span class="stat-label">Solana CEX</span></div>
    `;
  }
  stats.innerHTML = statsHtml;

  // Show recent high-risk wallets from Redis scan
  content.innerHTML = `
    <div class="card" style="grid-column:1/-1">
      <h3>System Health</h3>
      <div class="profile-field"><span class="label">Status</span><span class="value">${health?.status === 'ok' ? '<span style="color:var(--safe)">Operational</span>' : '<span style="color:var(--warning)">Degraded</span>'}</span></div>
      <div class="profile-field"><span class="label">Redis</span><span class="value">${health?.storage?.redis ? '<span style="color:var(--safe)">Connected</span>' : '<span style="color:var(--danger)">Disconnected</span>'}</span></div>
      <div class="profile-field"><span class="label">ClickHouse</span><span class="value">${health?.storage?.clickhouse ? '<span style="color:var(--safe)">Connected</span>' : '<span style="color:var(--warning)">Unavailable (Redis-only)</span>'}</span></div>
      <div class="profile-field"><span class="label">Clustering Heuristics</span><span class="value">${health?.clustering?.heuristics || 6}</span></div>
      <div class="profile-field"><span class="label">Label Sources</span><span class="value">9 (6 static + 3 live)</span></div>
      <div class="profile-field"><span class="label">Label Import</span><span class="value">${labelStats?.loading ? '<span style="color:var(--warning)">Loading...</span>' : '<span style="color:var(--safe)">Complete</span>'}</span></div>
    </div>
  `;
}
</script>
</body>
</html>"""


@router.get("/walletsafe/")
async def walletsafe_root():
    """Serve the WalletSafe frontend."""
    return HTMLResponse(content=WALLETSAFE_HTML)


@router.get("/walletsafe")
async def walletsafe_no_slash():
    """Redirect to /walletsafe/."""
    return HTMLResponse(content=WALLETSAFE_HTML)
