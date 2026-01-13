# 🔒 Pure Alpha Trading Webapp - Security Configuration Guide

---

## 📋 CURRENT SETUP

**Framework:** Flask (Python)  
**Current Binding:** `0.0.0.0:5050` (All network interfaces)  
**Mode:** PAPER Trading (Simulated, but handles real API credentials)  
**Database:** PostgreSQL (Local)  

---

## ⚠️ SECURITY STATUS

### Current State
```
❌ NOT SUITABLE FOR INTERNET EXPOSURE

Reasons:
- No authentication/authorization
- No HTTPS/TLS encryption
- Exposed on 0.0.0.0 (all interfaces)
- No firewall protection
- Contains sensitive API credentials
- No rate limiting
- Debug mode OFF (good), but no security headers
```

---

## 🎯 SECURITY REQUIREMENTS BY USE CASE

### 1️⃣ **LOCAL DEVELOPMENT ONLY**
```
Current Setup: ✅ ACCEPTABLE

Port Binding:  localhost:5050 (not 0.0.0.0)
Firewall:      Hardware firewall only
Auth:          None needed
HTTPS:         Not required
Access:        Only from your machine

Changes Needed:
- Bind to 127.0.0.1 instead of 0.0.0.0
- Add localhost to Flask app
```

### 2️⃣ **LAN SHARING (Same Office/Home Network)**
```
Current Setup: ⚠️ RISKY - Needs Protection

Requirements:
- Firewall rules (Block external access)
- Basic authentication (username/password)
- HTTPS with self-signed certificate
- API credential encryption
- Network segmentation

Recommended Tools:
- Nginx (reverse proxy + basic auth)
- Let's Encrypt (free HTTPS)
- Fail2ban (DDoS protection)
```

### 3️⃣ **PRODUCTION (Internet-Facing)**
```
Current Setup: ❌ COMPLETELY UNSUITABLE

Required:
✅ OAuth2 / JWT authentication
✅ HTTPS/TLS (Let's Encrypt)
✅ Nginx reverse proxy
✅ Rate limiting
✅ WAF (Web Application Firewall)
✅ API key encryption
✅ Database encryption
✅ VPN access only
✅ Audit logging
✅ DDoS protection (Cloudflare)
✅ Regular security audits
✅ Penetration testing
```

---

## 🔧 IMMEDIATE SECURITY FIXES

### Fix 1: Change Port Binding (Development)
**File:** `Webapp/main.py` (Line 308)

**Current:**
```python
app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
```

**Change to:**
```python
# For local development only
app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)
```

**Access:** `http://localhost:5050` only

---

### Fix 2: Add Security Headers (Flask)
**File:** `Webapp/app.py` (Add after Flask initialization)

```python
@app.after_request
def set_security_headers(response):
    """Add security headers to all responses."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'"
    return response
```

---

### Fix 3: Protect API Endpoints (Authentication)
**File:** `Webapp/app.py` (Add before routes)

```python
from functools import wraps
from flask import request, jsonify
import os

API_KEY = os.getenv('API_KEY', 'change-me-in-production')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('X-API-Key')
        if not auth or auth != API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# Use on sensitive routes:
@app.route('/api/strategy/mode', methods=['POST'])
@require_auth
def change_strategy_mode():
    # ... implementation
```

---

### Fix 4: Encrypt Sensitive Data (API Credentials)
**File:** `.env`

```bash
# ⚠️ NEVER commit this file to git

# API Credentials
KITE_API_KEY=your_key_here
KITE_API_SECRET=your_secret_here
ACCESS_TOKEN=your_token_here

# Database
DB_PASSWORD=secure_password_here

# API Security
API_KEY=strong-random-key-here-32-chars-min
JWT_SECRET=another-strong-secret-here

# Session
SESSION_COOKIE_SECURE=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE=Strict
```

---

### Fix 5: Use Nginx as Reverse Proxy (LAN/Prod)
**File:** `/etc/nginx/sites-available/pure-alpha.conf`

```nginx
upstream flask_app {
    server 127.0.0.1:5050;
}

server {
    listen 80;
    server_name your-domain.com;
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    # SSL Certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
    limit_req_status 429;
    
    location / {
        limit_req zone=general burst=20 nodelay;
        
        # Basic authentication
        auth_basic "Restricted Access";
        auth_basic_user_file /etc/nginx/.htpasswd;
        
        proxy_pass http://flask_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /api/ {
        limit_req zone=api burst=5 nodelay;
        proxy_pass http://flask_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 🚀 DEPLOYMENT RECOMMENDATIONS

### For Your Current Setup (LOCAL/LAN)
```
1. ✅ Bind Flask to 127.0.0.1 only
2. ✅ Add security headers
3. ✅ Set strong API_KEY in .env
4. ✅ Enable firewall rules
5. ✅ Use VPN for remote access
6. ⚠️ Don't expose to internet
```

### For Production (If Needed)
```
1. ✅ Move to proper server (AWS/DigitalOcean/Heroku)
2. ✅ Use managed PostgreSQL
3. ✅ Implement OAuth2/JWT
4. ✅ Use Nginx reverse proxy
5. ✅ Get SSL cert (Let's Encrypt)
6. ✅ Enable DDoS protection (Cloudflare)
7. ✅ Regular security audits
8. ✅ Separate credentials from code
9. ✅ Database encryption
10. ✅ API rate limiting
```

---

## 🔐 CHECKLIST

### ✅ Immediate (This Week)
- [ ] Move credentials to .env (not in code)
- [ ] Add .env to .gitignore
- [ ] Change Flask host to 127.0.0.1
- [ ] Add security headers
- [ ] Set strong API_KEY

### ⚠️ If Sharing on LAN
- [ ] Install Nginx
- [ ] Configure reverse proxy
- [ ] Set up basic auth
- [ ] Enable firewall rules
- [ ] Use HTTPS (self-signed cert)

### 🚨 If Internet-Facing
- [ ] Do NOT use this setup
- [ ] Implement full OAuth2
- [ ] Use managed hosting service
- [ ] Get proper SSL certificate
- [ ] Enable DDoS protection
- [ ] Hire security consultant

---

## 📝 SUMMARY

**Current Status:** 
- ✅ Safe for **LOCAL DEVELOPMENT**
- ⚠️ Risky for **LAN SHARING** (needs Nginx + Auth)
- ❌ Unsuitable for **PRODUCTION** (needs complete overhaul)

**Your Use Case:** Based on the setup, this appears to be:
- **LOCAL/LAN trading bot** with paper trading
- **Not meant for public internet**
- **Personal/office use only**

If you confirm the use case, I can provide:
1. **Local Dev:** Minor fixes only
2. **LAN Sharing:** Complete Nginx setup guide
3. **Production:** Full enterprise security guide

---

**Which applies to you?**
- 🏠 Local development only?
- 🏢 Sharing on office/home LAN?
- 🌐 Internet-facing production?

Let me know and I'll provide the exact configuration! 🔒

---

*Last Updated: 2026-01-14*
