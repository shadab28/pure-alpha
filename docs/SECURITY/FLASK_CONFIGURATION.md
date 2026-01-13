# ⚙️ Flask Configuration Guide

**Date Updated:** January 14, 2026  
**Fix Commit:** `f2b1b90`

---

## Overview

Flask is now configured with secure defaults:
- ✅ Debug mode disabled by default
- ✅ Localhost binding (127.0.0.1) by default
- ✅ Environment variable controlled
- ✅ Warning logs for unsafe configurations

---

## Configuration Options

### Environment Variables

#### `FLASK_DEBUG` (default: `false`)

Controls Flask debug mode (Werkzeug debugger, auto-reload).

**Values:**
- `true`, `1`, `yes` → Debug mode ON ⚠️
- `false`, `0`, `no` (or unset) → Debug mode OFF ✅

**Example:**
```bash
# Development (with debug)
export FLASK_DEBUG=true
python3 Webapp/app.py

# Production (without debug)
export FLASK_DEBUG=false
python3 Webapp/app.py

# Default (debug disabled)
python3 Webapp/app.py
```

#### `FLASK_HOST` (default: `127.0.0.1`)

Controls which network interface Flask listens on.

**Values:**
- `127.0.0.1` → Localhost only ✅ (RECOMMENDED)
- `0.0.0.0` → All interfaces ⚠️ (EXPOSED)
- `192.168.1.100` → Specific IP

**Example:**
```bash
# Localhost only (default - most secure)
python3 Webapp/app.py

# Custom interface
export FLASK_HOST=0.0.0.0
python3 Webapp/app.py

# Specific IP
export FLASK_HOST=192.168.1.100
python3 Webapp/app.py
```

#### `PORT` (default: `5050`)

Controls Flask port.

**Example:**
```bash
export PORT=8080
python3 Webapp/app.py
```

---

## Recommended Configurations

### 🟢 Development Environment

Safe for local development with debugging enabled:

```bash
export KITE_API_KEY="your_api_key"
export FLASK_DEBUG=true
export FLASK_HOST=127.0.0.1
export PORT=5050
python3 Webapp/main.py
```

**Output:**
```
⚠️  FLASK DEBUG MODE ENABLED - NOT FOR PRODUCTION
✓ Kite Connect initialized for LIVE trading
✓ Flask server running on http://127.0.0.1:5050
```

### 🟡 Staging Environment

With logging but no debug mode:

```bash
export KITE_API_KEY="your_api_key"
export FLASK_DEBUG=false
export FLASK_HOST=127.0.0.1
export PORT=5050
python3 Webapp/main.py
```

### 🔴 Production Environment

Locked down for security:

```bash
export KITE_API_KEY="your_api_key"
# FLASK_DEBUG not set (defaults to false)
# FLASK_HOST not set (defaults to 127.0.0.1)
export PORT=5050
python3 Webapp/main.py
```

**Use behind a reverse proxy (nginx/Apache):**
```nginx
# nginx.conf
server {
    listen 80;
    server_name api.yourdomain.com;
    
    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
    }
}
```

---

## Docker Configuration

### Development Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

ENV FLASK_DEBUG=true
ENV FLASK_HOST=127.0.0.1
EXPOSE 5050

CMD ["python3", "Webapp/main.py"]
```

### Production Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

# Debug disabled by default
ENV FLASK_DEBUG=false
ENV FLASK_HOST=127.0.0.1
EXPOSE 5050

CMD ["python3", "Webapp/main.py"]
```

### Docker Compose

```yaml
version: '3.8'

services:
  pure-alpha:
    build: .
    environment:
      - KITE_API_KEY=${KITE_API_KEY}
      - KITE_API_SECRET=${KITE_API_SECRET}
      - FLASK_DEBUG=false        # Production: disabled
      - FLASK_HOST=127.0.0.0     # Production: localhost only
      - PORT=5050
    ports:
      - "127.0.0.1:5050:5050"   # Bind to localhost only
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5050/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
```

---

## Systemd Service Configuration

### `/etc/systemd/system/pure-alpha.service`

```ini
[Unit]
Description=Pure Alpha Trading Platform
After=network.target

[Service]
Type=simple
User=trading
WorkingDirectory=/opt/pure-alpha

Environment="KITE_API_KEY=your_key_here"
Environment="KITE_API_SECRET=your_secret_here"
Environment="FLASK_DEBUG=false"
Environment="FLASK_HOST=127.0.0.1"
Environment="PORT=5050"

ExecStart=/usr/bin/python3 /opt/pure-alpha/Webapp/main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable pure-alpha
sudo systemctl start pure-alpha
sudo systemctl status pure-alpha
```

---

## Security Warnings

### ⚠️ Debug Mode Enabled

If you see this warning:
```
⚠️  FLASK DEBUG MODE ENABLED - NOT FOR PRODUCTION
```

**Risks:**
- Werkzeug debugger accessible with potential PIN bypass
- Auto-reload can interfere with production stability
- Stack traces reveal application structure
- Code execution possible via debugger

**Fix:**
- Set `FLASK_DEBUG=false` or leave unset
- Disable only in development environments

### ⚠️ Network Binding

If you see this warning:
```
⚠️  FLASK LISTENING ON 0.0.0.0 - EXPOSED TO NETWORK
```

**Risks:**
- Application accessible from any machine on the network
- Potential unauthorized access
- API endpoints exposed without additional security

**Fix:**
- Keep `FLASK_HOST=127.0.0.1` (default)
- Use reverse proxy (nginx) for external access
- Only expose `FLASK_HOST=0.0.0.0` if behind firewall

---

## Verification

### Check Configuration

```bash
# View all Flask environment variables
env | grep FLASK

# Should show:
# FLASK_DEBUG=false (or not set)
# FLASK_HOST=127.0.0.1 (or not set)
```

### Test Localhost Only

```bash
# Should work:
curl http://127.0.0.1:5050/

# Should NOT work (if FLASK_HOST=127.0.0.1):
curl http://0.0.0.0:5050/  # No route
curl http://192.168.1.100:5050/  # Fails
```

### View Startup Logs

```bash
# Start application with logging
python3 Webapp/main.py 2>&1 | grep -E "(DEBUG|LISTENING|running)"

# Should see:
# Flask server running on http://127.0.0.1:5050
# (No warnings about debug mode or network exposure)
```

---

## Migration Guide

### From Old Configuration

**Before (Unsafe):**
```python
app.run(host="0.0.0.0", port=5050, debug=True)
```

**After (Safe):**
```python
debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
host = os.environ.get('FLASK_HOST', '127.0.0.1')
port = int(os.environ.get('PORT', 5050))
app.run(host=host, port=port, debug=debug_mode)
```

### Update Your Scripts

If you have scripts that start the app, update them:

**Old:**
```bash
python3 Webapp/app.py
```

**New (Development):**
```bash
export FLASK_DEBUG=true
python3 Webapp/app.py
```

**New (Production):**
```bash
export FLASK_DEBUG=false
export FLASK_HOST=127.0.0.1
python3 Webapp/app.py
```

---

## Troubleshooting

### Q: I can't access the app from another machine

**A:** This is by design. The app now only listens on `127.0.0.1` (localhost).

**Solution:**
- Use a reverse proxy (nginx/Apache) to expose externally
- Or temporarily set `FLASK_HOST=0.0.0.0` for testing (⚠️ not recommended)

### Q: Debug mode isn't working

**A:** Make sure you set `FLASK_DEBUG=true` before starting:

```bash
export FLASK_DEBUG=true
python3 Webapp/app.py
```

**Verify:**
```bash
echo $FLASK_DEBUG  # Should show: true
```

### Q: App crashes on startup

**A:** Check if port 5050 is already in use:

```bash
# Check what's using port 5050
lsof -i :5050

# Use different port
export PORT=8080
python3 Webapp/app.py
```

### Q: Why localhost only?

**A:** For security:
1. Prevents unauthorized network access
2. Blocks external API access without authentication
3. Forces use of reverse proxy for external access
4. Standard security best practice

---

## References

- Flask Documentation: https://flask.palletsprojects.com/
- Werkzeug Debugger: https://werkzeug.palletsprojects.com/
- OWASP Security Guidelines: https://owasp.org/
- Reverse Proxy Setup: https://nginx.org/en/docs/http/ngx_http_proxy_module.html

---

**Configuration Complete!** ✅

Your Flask application is now securely configured with:
- Debug mode disabled by default
- Localhost binding by default
- Warning logs for unsafe configurations
- Environment variable control for flexibility

*Last Updated: January 14, 2026*
