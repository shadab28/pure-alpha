# 🔄 Quick Guide: Rotate Zerodha API Key

**Exposed Key:** `da0ztb3q4k9ckiwn`  
**Action Required:** Immediate rotation  
**Time to Complete:** ~5 minutes

---

## Step 1: Delete Old Key (Zerodha Console)

1. Open https://kite.zerodha.com in your browser
2. Log in with your Zerodha credentials
3. Navigate to **Settings** → **API Console**
4. Find the API key that looks like: `da0ztb3q4k9ckiwn`
5. Click **Delete** button next to it
6. Confirm the deletion

**Screenshot locations:**
- Settings: Top right corner menu → "Settings"
- API Console: Left sidebar → "API Console"

---

## Step 2: Generate New Key

1. In the API Console, click **Generate New Key**
2. Zerodha will generate:
   - **API Key** (looks like: `abc123xyz789`)
   - **API Secret** (long string, appears once)
3. **IMPORTANT:** Save both values immediately in a secure location
4. Click "Accept" to confirm

**⚠️ WARNING:** API Secret appears only once. If you miss it, you'll need to regenerate.

---

## Step 3: Update Application

Choose ONE of these methods:

### Option A: Update `.env` File (Recommended)

```bash
# Edit .env file
nano .env

# Add/Update these lines:
KITE_API_KEY=your_new_api_key_here
KITE_API_SECRET=your_new_secret_here
KITE_ACCESS_TOKEN=your_existing_token_here

# Save and exit (Ctrl+X, Y, Enter)
```

### Option B: Export Environment Variables

```bash
export KITE_API_KEY="your_new_api_key_here"
export KITE_API_SECRET="your_new_secret_here"
export KITE_ACCESS_TOKEN="your_existing_token_here"
```

### Option C: Docker/Container

```bash
docker run \
  -e KITE_API_KEY="your_new_key" \
  -e KITE_API_SECRET="your_new_secret" \
  -p 127.0.0.1:5050:5050 \
  pure-alpha:latest
```

---

## Step 4: Verify the Change

```bash
# Make sure env var is set
echo $KITE_API_KEY

# Should output: your_new_api_key_here
```

---

## Step 5: Restart Application

```bash
# Stop current instance (Ctrl+C if running in terminal)

# Restart with new credentials
python3 Webapp/main.py --port 5050

# Or if using Flask directly:
python3 -m flask --app Webapp/app.py run --host 127.0.0.1 --port 5050
```

You should see:
```
✓ Kite Connect initialized for LIVE trading
✓ Flask server running on http://127.0.0.1:5050
```

---

## Verification Checklist

- [ ] Old key deleted from Zerodha Console
- [ ] New key generated and saved
- [ ] `.env` file updated with new credentials
- [ ] Application started without errors
- [ ] No "KITE_API_KEY environment variable not set" errors
- [ ] Dashboard loads at http://127.0.0.1:5050

---

## Troubleshooting

### Error: "KITE_API_KEY environment variable not set"
- **Solution:** Verify env var is set: `echo $KITE_API_KEY`
- Make sure to source `.env` or export the variable

### Error: "Invalid API key"
- **Solution:** Double-check the key from Zerodha console (copy-paste to avoid typos)
- Ensure no extra spaces: `KITE_API_KEY="key_without_spaces"`

### Still using old key?
- **Solution:** Check which `.env` file is being read
- Look at application logs for which API key it's trying to use

---

## Security Best Practices

✅ **DO:**
- Store new key in `.env` (not in code)
- Keep `.env` in `.gitignore`
- Rotate keys regularly (quarterly recommended)
- Use different keys for dev/production if possible

❌ **DON'T:**
- Hardcode keys in Python files
- Commit `.env` to git
- Share API keys with others
- Use same key across multiple machines

---

## Next Steps

1. ✅ Rotate API key (THIS GUIDE)
2. ⏭️ [Disable Flask debug mode](CODEBASE_REVIEW_REPORT.md#phase-1-critical-fixes-immediate---1-day)
3. ⏭️ [Add `.env` to `.gitignore`](SECURITY_ADVISORY.md)
4. ⏭️ Review [SECURITY_ADVISORY.md](SECURITY_ADVISORY.md) for additional steps

---

**Time Estimate:** 5 minutes  
**Difficulty:** Easy  
**Risk:** Low (old key will be invalid after deletion)

*Last Updated: January 14, 2026*
