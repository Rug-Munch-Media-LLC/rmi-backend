# Dependency-Track CI Integration

## Overview

This document describes the conditional SBOM upload to Dependency-Track (DTrack) from the rmi-backend CI pipeline.

## How It Works

1. **SBOM Generation**: The `sbom` job generates a CycloneDX JSON SBOM using `anchore/sbom-action`
2. **SBOM Verification**: The SBOM is validated via `scripts/sbom-verify.sh`
3. **SpecVersion Downgrade**: DTrack currently accepts CycloneDX specVersion 1.6; the CI step downgrades 1.7→1.6 when needed
4. **Conditional Upload**: If `DTRACK_API_KEY` secret is configured, the SBOM is POSTed to the DTrack API:
   - Endpoint: `${DTRACK_URL}/api/v1/bom` (default: `http://[INTERNAL_HOST]:8089`)
   - Method: `multipart/form-data`
   - Fields: `projectName`, `projectVersion`, `autoCreate`, `bom`
5. **Graceful Skip**: If the key is missing or the service is unreachable, the step exits 0 with a notice — the build continues

## Required Secrets / Variables

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `DTRACK_API_KEY` | Secret | Yes* | API key with `BOM_UPLOAD` permission. If empty/missing, upload is skipped. |
| `DTRACK_URL` | Variable | No | Base URL of DTrack API. Default: `http://[INTERNAL_HOST]:8089` |

*Required for upload; pipeline succeeds without it.

## Runner Reachability Constraint

**Critical**: CI runners may not be able to reach the default endpoint (DTrack runs on the production host, not in the runner container/network).

- **Forgejo runners on production host**: Can reach `http://[INTERNAL_HOST]:8089` (default works)
- **GitHub Actions runners / external runners**: Cannot reach production localhost — **must** set `DTRACK_URL` to a reachable endpoint

Set `DTRACK_URL` as a repository variable in Forgejo/GitHub settings.

## Key Rotation

1. Generate new API key in DTrack UI: Administration → Access Management → Teams → `rmi-ci` → API Keys
2. Update `DTRACK_API_KEY` secret in Forgejo (Settings → Secrets → Actions) and/or GitHub
3. Revoke old key in DTrack
4. Verify next CI run uploads successfully

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `⚠️ Dependency-Track upload failed` + connection refused | Runner cannot reach `DTRACK_URL` | Set `DTRACK_URL` to a reachable endpoint |
| `401 Unauthorized` | Invalid/expired API key | Rotate key (see above) |
| `403 Forbidden` | Key lacks `BOM_UPLOAD` permission | Add permission in DTrack team settings |
| `400 Bad Request: Schema validation failed` | SBOM missing required fields | Check `scripts/sbom-verify.sh` output; ensure CycloneDX format |
| `400 Bad Request: Unrecognized specVersion 1.7` | DTrack doesn't support CycloneDX 1.7 | CI downgrades to 1.6 automatically; if not, update workflow |
| Upload succeeds but project not visible | Project created but not latest version | Check `isLatest` flag; DTrack only shows latest by default |

## Testing Locally

```bash
# Generate test SBOM
syft scan . -o cyclonedx-json > sbom.json

# Downgrade specVersion if needed (DTrack currently accepts 1.6)
python3 -c "
import json
with open('sbom.json') as f:
    d = json.load(f)
if d.get('specVersion') == '1.7':
    d['specVersion'] = '1.6'
    with open('sbom.json', 'w') as f:
        json.dump(d, f)
"

# Upload (requires DTRACK_API_KEY in env)
export DTRACK_URL=http://[INTERNAL_HOST]:8089
export DTRACK_API_KEY=your-key
curl -X POST "${DTRACK_URL}/api/v1/bom" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -F "projectName=rmi-backend" \
  -F "projectVersion=local-test" \
  -F "autoCreate=true" \
  -F "bom=@sbom.json"
```

## Verification

After a successful CI run, check DTrack UI:
- Projects → `rmi-backend` → latest version should show the new BOM import timestamp
- Vulnerability analysis runs automatically after import
