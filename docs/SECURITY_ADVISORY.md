# 🔒 Security Advisory - Hardcoded API Key Removed

**Date:** January 14, 2026  
**Severity:** 🔴 CRITICAL  
**Status:** FIXED

---

## Summary

A critical security vulnerability has been identified and **FIXED**: hardcoded Zerodha Kite API key was exposed in source code at `Webapp/momentum_strategy.py` line 250.

**API Key Exposed:** `da0ztb3q4k9ckiwn`

---

## What Was The Issue?

The API key was hardcoded in the source code:
```python
api_key = "da0ztb3q4k9ckiwn"  # INSECURE
```

**Risks:**
- ❌ Exposed in version control history (can be found even if deleted)
- ❌ Visible to anyone with repository access
- ❌ If repository is made public, credentials are immediately compromised
- ❌ Could allow unauthorized trades on your Zerodha account
- ❌ Violates PCI-DSS and security best practices

---

## What Has Been Fixed?

✅ **Replaced hardcoded key with environment variable:**
```python
api_key = os.getenv("KITE_API_KEY")
if not api_key:
    raise RuntimeError(
        "KITE_API_KEY environment variable not set. "
        "Please set it before running the application."
    )
```

**Commit:** `bd8ed24`  
**Changed Files:** `Webapp/momentum_strategy.py`

---

## What You Must Do Immediately

### Step 1: Rotate Your API Key ⚠️

**CRITICAL:** The exposed API key must be rotated immediately via Zerodha Console:

1. Go to https://kite.zerodha.com
2. Navigate to Settings → API Console
3. **Delete the compromised key:** `da0ztb3q4k9ckiwn`
4. **Generate a new API key**
5. Save the new API secret securely

### Step 2: Update Environment Configuration

You have two options:

#### Option A: Using `.env` File (Recommended for Development)

1. Create/update `.env` file in project root:
```bash
KITE_API_KEY=your_new_api_key_here
KITE_API_SECRET=your_api_secret_here
```

2. **IMPORTANT:** Ensure `.env` is in `.gitignore`:
```bash
# Verify .gitignore exists and contains .env
cat .gitignore | grep "^.env$"

# If not present, add it:
echo ".env" >> .gitignore
```

3. Source the `.env` before running:
```bash
source .env
python3 Webapp/main.py --port 5050
```

#### Option B: Using Export Command

```bash
export KITE_API_KEY="your_new_api_key_here"
export KITE_API_SECRET="your_new_secret_here"
python3 Webapp/main.py --port 5050
```

#### Option C: Docker/Systemd

Pass as environment variables:
```bash
docker run -e KITE_API_KEY=your_key -e KITE_API_SECRET=your_secret pure-alpha
```

Or in systemd service file:
```ini
[Service]
Environment="KITE_API_KEY=your_key"
Environment="KITE_API_SECRET=your_secret"
```

### Step 3: Verify The Fix

Run the application and verify it starts without errors:

```bash
export KITE_API_KEY="your_new_key"
python3 Webapp/main.py --port 5050
```

You should see:
```
✓ Kite Connect initialized for LIVE trading
✓ Flask server running on http://127.0.0.1:5050
```

---

## Git History Cleanup

The exposed key **exists in git history**. To fully secure your repository:

### Option 1: Force Push (⚠️ Only if private repo)

If repository is **private and not shared**:
```bash
# Remove the key from history using git-filter-repo
pip install git-filter-repo
git filter-repo --replace-text <(echo 'da0ztb3q4k9ckiwn==>[REMOVED]') --force

# Force push (only safe for private repos)
git push origin --force-all
```

### Option 2: Add To `.gitignore` and Warn Users

If history is already shared:
1. Add to `.gitignore`
2. Commit the fix
3. Notify all users to rotate their keys
4. Add to repository README

### Option 3: Scan For Leaks

Use automated tools to detect other potential leaks:
```bash
# Install detection tools
pip install trufflehog gitleaks

# Scan the repository
trufflehog filesystem .
gitleaks detect --source filesystem --verbose
```

---

## Prevention Going Forward

### ✅ Best Practices to Prevent Future Leaks

1. **Use environment variables** for all secrets
2. **Add to `.gitignore`:**
   ```
   .env
   .env.local
   .env.*.local
   secrets.json
   token.txt
   *.pem
   *.key
   ```

3. **Use `.env.example`** for reference:
   ```bash
   # .env.example (safe to commit)
   KITE_API_KEY=your_api_key_here
   KITE_API_SECRET=your_api_secret_here
   KITE_ACCESS_TOKEN=your_access_token
   ```

4. **Enable pre-commit hooks** to prevent accidental commits:
   ```bash
   pip install pre-commit
   cat > .pre-commit-config.yaml << 'EOF'
   repos:
     - repo: https://github.com/Yelp/detect-secrets
       rev: v1.4.0
       hooks:
         - id: detect-secrets
           args: ['--baseline', '.secrets.baseline']
   EOF
   
   pre-commit install
   ```

5. **Code review checklist:**
   - [ ] No hardcoded passwords/keys in code
   - [ ] Secrets loaded from environment only
   - [ ] Sensitive files are in `.gitignore`
   - [ ] No API keys in logging output
   - [ ] Use `.env.example` for documentation

---

## Testing Verification

Verify the fix works correctly:

```bash
# Test 1: Missing env var should fail gracefully
unset KITE_API_KEY
python3 -c "from Webapp.momentum_strategy import get_kite_instance; get_kite_instance()" 2>&1 | grep -i "environment variable"

# Test 2: With env var should work
export KITE_API_KEY="test_key"
python3 -c "from Webapp.momentum_strategy import get_kite_instance; print('✓ API key loaded from environment')"
```

---

## Timeline

| Date | Event |
|------|-------|
| 2026-01-14 | 🔴 Hardcoded key discovered during security audit |
| 2026-01-14 | 🟡 Vulnerability documented in CODEBASE_REVIEW_REPORT.md |
| 2026-01-14 | ✅ Fix implemented and committed (bd8ed24) |
| 2026-01-14 | 📋 This advisory created |
| **NOW** | 🔒 **ACTION REQUIRED: Rotate API key via Zerodha** |

---

## Support & Questions

If you encounter issues:

1. **Application won't start?**
   - Check: `echo $KITE_API_KEY` returns your API key
   - Verify no special characters need escaping in the value

2. **Got error "KITE_API_KEY environment variable not set"?**
   - Set the variable: `export KITE_API_KEY="your_key"`
   - Verify: `echo $KITE_API_KEY`

3. **Need to rotate key multiple times?**
   - You can have multiple API keys in Zerodha Console
   - Generate new ones and discard old ones as needed
   - Keep track of which key is in `.env`

4. **Found other hardcoded secrets?**
   - Search: `grep -r "api_key\|password\|secret" --include="*.py" | grep -v "os.getenv" | grep -v "#"`
   - Report to maintainer immediately

---

## References

- Zerodha API Console: https://kite.zerodha.com/
- OWASP Secrets Management: https://owasp.org/www-project-top-10-proactive-controls/#6-store-secure-data-securely
- Git Security Best Practices: https://github.com/git/git/security
- Pre-commit hooks: https://pre-commit.com/

---

**⚠️ REMINDER:** Rotate your API key and set `KITE_API_KEY` environment variable before next run.

*Security Advisory generated: 2026-01-14*
