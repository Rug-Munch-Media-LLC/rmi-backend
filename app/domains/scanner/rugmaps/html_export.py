"""
RugMaps — Interactive HTML Export
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domains.scanner.rugmaps.models import BubbleMap


def export_html(bubble_map: BubbleMap, output_path: str) -> Any:
    """Export a bubble map to an interactive HTML file."""
    html = _generate_interactive_html(bubble_map)
    with open(output_path, "w") as f:
        f.write(html)
    return output_path


def _generate_interactive_html(bubble_map: BubbleMap) -> str:
    """Generate a self-contained HTML page with D3.js visualization."""
    data = {
        "center_wallet": bubble_map.center_wallet,
        "depth": bubble_map.depth,
        "nodes": [
            {"id": n.id, "label": n.label, "type": n.type, "risk": n.risk,
             "riskScore": n.risk_score, "layer": n.layer, "x": n.x, "y": n.y,
             "radius": n.radius, "color": n.color, "transactions": n.transaction_count,
             "entity": {"name": n.entity.name if n.entity else "", "type": n.entity.type if n.entity else ""} if n.entity else None}
            for n in bubble_map.nodes
        ],
        "links": [{"source": l.source_id, "target": l.target_id, "strength": l.strength,
                    "label": l.label, "color": l.color} for l in bubble_map.links],
        "stats": bubble_map.stats,
        "generated_at": bubble_map.generated_at,
        "_format_timestamp": bubble_map.generated_at,
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RMI Bubble Map - {{data['center_wallet'][:16]}}</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; overflow: hidden; }}
#tooltip {{ position: absolute; visibility: hidden; background: rgba(10,10,15,0.95); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 12px; font-size: 12px; max-width: 300px; pointer-events: none; backdrop-filter: blur(8px); z-index: 100; }}
#tooltip .label {{ color: #888; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }}
#tooltip .value {{ color: #fff; font-size: 13px; }}
#tooltip .risk {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 600; }}
.legend {{ position: absolute; bottom: 20px; left: 20px; display: flex; gap: 16px; flex-wrap: wrap; font-size: 11px; background: rgba(10,10,15,0.8); padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }}
.legend-item {{ display: flex; align-items: center; gap: 6px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
.controls {{ position: absolute; top: 20px; right: 20px; display: flex; gap: 8px; }}
.controls button {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #e0e0e0; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 11px; transition: all 0.2s; }}
.controls button:hover {{ background: rgba(255,255,255,0.1); }}
.link {{ stroke-opacity: 0.3; transition: stroke-opacity 0.2s; }}
.link:hover {{ stroke-opacity: 0.8; }}
.node {{ cursor: pointer; transition: transform 0.15s; }}
.node:hover {{ transform: scale(1.1); }}
.node-label {{ font-size: 10px; fill: #888; pointer-events: none; }}
</style>
</head>
<body>
<div id="tooltip"></div>
<div class="legend" id="legend"></div>
<div class="controls">
<button onclick="zoomIn()">+</button>
<button onclick="zoomOut()">-</button>
<button onclick="resetView()">Reset</button>
<button onclick="exportPNG()">PNG</button>
<button onclick="exportJSON()">JSON</button>
</div>
<script>
const data = {json.dumps(data)};
const width = window.innerWidth, height = window.innerHeight;
const svg = d3.select('body').append('svg').attr('width', width).attr('height', height);
const g = svg.append('g');
const zoom = d3.zoom().scaleExtent([0.1, 8]).on('zoom', (event) => g.attr('transform', event.transform));
svg.call(zoom);

const simulation = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(data.links).id(d => d.id).distance(d => 150 * (1 - (d.strength || 0.2))).strength(d => d.strength || 0.3))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => (d.radius || 20) + 5));

const link = g.append('g').selectAll('line').data(data.links).join('line')
    .attr('class', 'link').attr('stroke', d => d.color || '#555').attr('stroke-width', d => Math.max(1, (d.strength || 0.2) * 6))
    .attr('stroke-dasharray', d => d.label === 'suspicious' ? '4,2' : 'none');

const node = g.append('g').selectAll('g').data(data.nodes).join('g').attr('class', 'node')
    .call(d3.drag().on('start', (e, d) => {{ if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
        .on('drag', (e, d) => {{ d.fx = e.x; d.fy = e.y; }}).on('end', (e, d) => {{ if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }}));

node.append('circle').attr('r', d => d.radius || 15).attr('fill', d => d.color || '#666')
    .attr('stroke', d => d.risk === 'critical' ? '#ff0000' : d.risk === 'high' ? '#ff6b6b' : 'rgba(255,255,255,0.1)')
    .attr('stroke-width', d => d.risk === 'critical' ? 3 : d.risk === 'high' ? 2 : 1);

node.append('text').attr('class', 'node-label').attr('text-anchor', 'middle').attr('dy', d => (d.radius || 15) + 14)
    .text(d => d.label.length > 20 ? d.label.slice(0, 18) + '..' : d.label);

node.on('mouseover', (event, d) => {{
    const tip = d3.select('#tooltip');
    tip.style('visibility', 'visible').html(`
        <div class="label">Wallet</div><div class="value" style="font-family:monospace;font-size:11px;">${{d.id}}</div>
        <div class="label" style="margin-top:6px;">Label</div><div class="value">${{d.label}}</div>
        <div class="label" style="margin-top:6px;">Type</div><div class="value">${{d.type || 'unknown'}}</div>
        <div style="margin-top:8px;"><span class="risk" style="background:${{d.risk === 'critical' ? '#ff000033' : d.risk === 'high' ? '#ff6b6b33' : d.risk === 'medium' ? '#ffd70033' : d.risk === 'low' ? '#90ee9033' : '#88888833'}};color:${{d.risk === 'critical' ? '#ff0000' : d.risk === 'high' ? '#ff6b6b' : d.risk === 'medium' ? '#ffd700' : d.risk === 'low' ? '#90ee90' : '#888'}};">${{(d.riskScore || 0).toFixed(2)}} ${{d.risk || 'unknown'}}</span></div>
        <div class="label" style="margin-top:6px;">Transactions</div><div class="value">${{d.transactions || 0}}</div>
        ${{d.entity ? `<div class="label" style="margin-top:6px;">Entity</div><div class="value">${{d.entity.name}} (${{d.entity.type}})</div>` : ''}}
    `).style('left', (event.pageX + 12) + 'px').style('top', (event.pageY - 10) + 'px');
}}).on('mouseout', () => d3.select('#tooltip').style('visibility', 'hidden'));

simulation.on('tick', () => {{
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
}});

// Legend
const legendTypes = ['center', 'scammer', 'exchange', 'whale', 'bot', 'kol', 'dev', 'unknown'];
const typeColors = {{center:'#ff6b6b',scammer:'#ff0000',exchange:'#4dabf7',whale:'#ffd43b',bot:'#9775fa',kol:'#69db7c',dev:'#ff8787',unknown:'#868e96'}};
const legendDiv = d3.select('#legend');
legendTypes.forEach(t => {{
    legendDiv.append('div').attr('class', 'legend-item')
        .html(`<span class="legend-dot" style="background:${{typeColors[t]||'#666'}}"></span>${{t}}`);
}});

function zoomIn() {{ svg.transition().call(zoom.scaleBy, 1.3); }}
function zoomOut() {{ svg.transition().call(zoom.scaleBy, 0.7); }}
function resetView() {{ svg.transition().call(zoom.transform, d3.zoomIdentity); }}
function exportPNG() {{
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#0a0a0f'; ctx.fillRect(0, 0, width, height);
    const svgData = new XMLSerializer().serializeToString(svg.node());
    const img = new Image();
    img.onload = () => {{ ctx.drawImage(img, 0, 0); const link = document.createElement('a'); link.download = `rmi-bubble-${{data.center_wallet.slice(0, 16)}}.png`; link.href = canvas.toDataURL('image/png'); link.click(); }};
    img.src = 'data:image/svg+xml;base64,' + btoa(svgData);
}}
function exportJSON() {{
    const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
    const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url;
    link.download = `rmi-bubble-${{data.center_wallet.slice(0, 16)}}.json`; link.click();
}}
</script>
</body>
</html>"""
