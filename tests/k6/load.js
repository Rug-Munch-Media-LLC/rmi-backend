// Load test — run nightly or on-demand.
// 50 VUs ramping up over 2 minutes, sustained for 5 minutes.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '1m', target: 20 },   // Ramp up to 20 VUs
    { duration: '1m', target: 50 },   // Ramp up to 50 VUs
    { duration: '3m', target: 50 },   // Stay at 50 VUs
    { duration: '1m', target: 0 },    // Ramp down
  ],
  thresholds: {
    'errors': ['rate<0.05'],
    'http_req_duration': ['p(95)<2000'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Mix of endpoints simulating real traffic
  const endpoints = [
    { url: `${BASE_URL}/health`, weight: 3 },
    { url: `${BASE_URL}/api/v1/token/eth:0x1234567890abcdef`, weight: 5 },
    { url: `${BASE_URL}/api/v1/stats`, weight: 2 },
  ];
  
  // Weighted random endpoint selection
  const totalWeight = endpoints.reduce((sum, e) => sum + e.weight, 0);
  let random = Math.random() * totalWeight;
  let selected = endpoints[0];
  for (const endpoint of endpoints) {
    random -= endpoint.weight;
    if (random <= 0) { selected = endpoint; break; }
  }
  
  const res = http.get(selected.url);
  check(res, { 'status ok': (r) => r.status === 200 || r.status === 404 || r.status === 422 }) || errorRate.add(1);
  sleep(1);
}

export function handleSummary(data) {
  return {
    'tests/k6/results/load-summary.json': JSON.stringify(data, null, 2),
  };
}
