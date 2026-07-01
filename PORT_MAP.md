# Backend Port Map

## Active Services

| Port | Status | Purpose |
|------|--------|---------|
| 8002 | Running | Legacy instance |
| 8003 | Running | Legacy instance |
| 8005 | Running | Legacy instance |
| 8006 | Running | Legacy instance |
| 8010 | Running | **Current dev instance** |

## Port SelectionGuide

To find an available port:
```bash
# Check what's listening
netstat -tlnp | grep -E ':800[0-9]'

# Or use Python
python3 -c "import socket; s=socket.socket(); s.bind(('', 0)); print('Free port:', s.getsockname()[1]); s.close()"
```

## Testing Commands

```bash
# Health check
curl http://localhost:8010/health

# Ready check
curl http://localhost:8010/ready

# Status
curl http://localhost:8010/api/v1/status

# Auth endpoints (require Redis)
curl -X POST http://localhost:8010/api/v1/auth/register -H "Content-Type: application/json" -d '{"email":"test@test.com","password":"Test123!","display_name":"Test User"}'
curl -X POST http://localhost:8010/api/v1/auth/login -H "Content-Type: application/json" -d '{"email":"test@test.com","password":"Test123!"}'

# Wallet auth
curl -X POST http://localhost:8010/api/v1/auth/wallet/nonce -H "Content-Type: application/json" -d '{"address":"0x123","chain":"ethereum"}'
```

## Redis Required

The auth endpoints require Redis to be running. Start it with:
```bash
redis-server --port 6379
```

Or configure a different host/port via environment variables:
- `REDIS_HOST` (default: localhost)
- `REDIS_PORT` (default: 6379)
- `REDIS_PASSWORD` (optional)
