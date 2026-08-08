// RMI Backend — k6 Load Test Scripts
// Run: k6 run tests/load/token_scan.js
//      k6 run tests/load/databus_fetch.js

import { check, sleep } from "k6";
import http from "k6/http";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// ═══════════════════════════════════════════
// Token Scan — Performance Budget: p95 < 500ms
// ═══════════════════════════════════════════
export function tokenScan() {
	const payload = JSON.stringify({
		token_address: "So11111111111111111111111111111111111111112",
		chain: "solana",
	});
	const params = {
		headers: {
			"Content-Type": "application/json",
			"X-RMI-Key": "rmi-internal-2026",
		},
	};
	const res = http.post(`${BASE_URL}/api/v1/token/scan`, payload, params);
	check(res, {
		"status 200": (r) => r.status === 200,
		"p95 < 500ms": (r) => r.timings.duration < 500,
	});
	sleep(0.1);
}

// ═══════════════════════════════════════════
// DataBus Fetch — Performance Budget: p95 < 300ms
// ═══════════════════════════════════════════
export function databusFetch() {
	const res = http.get(
		`${BASE_URL}/api/v1/databus/fetch/token_price?mint=So11111111111111111111111111111111111111112`,
	);
	check(res, {
		"status 200": (r) => r.status === 200,
		"p95 < 300ms": (r) => r.timings.duration < 300,
	});
	sleep(0.05);
}

// ═══════════════════════════════════════════
// MEV Sniper — Performance Budget: p95 < 2s
// ═══════════════════════════════════════════
export function mevSniper() {
	const res = http.get(
		`${BASE_URL}/api/v1/mev-sniper/signals?chain=solana&max_age_min=30`,
	);
	check(res, {
		"status 200": (r) => r.status === 200,
		"p99 < 2000ms": (r) => r.timings.duration < 2000,
	});
	sleep(0.2);
}

// ── Default test config ──
export const options = {
	stages: [
		{ duration: "30s", target: 10 }, // ramp up
		{ duration: "1m", target: 50 }, // sustain
		{ duration: "30s", target: 0 }, // ramp down
	],
	thresholds: {
		http_req_duration: ["p(95)<500", "p(99)<2000"],
		http_req_failed: ["rate<0.01"],
	},
};
