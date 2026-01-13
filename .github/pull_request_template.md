# 📌 Pull Request Review Checklist (MANDATORY)

> 🚨 **This system handles execution logic. Assume real capital is at risk.**
> All unchecked items must be justified in comments.

---

## 🔍 Summary
**What does this PR change?**
- 

**Why is this change needed?**
- 

**Is this affecting live execution or strategy logic?**
- [ ] Yes
- [ ] No

---

## 🔐 Security & Exposure
- [ ] No service binds to `0.0.0.0` without auth, firewall, or reverse proxy
- [ ] No secrets, tokens, or credentials in code or logs
- [ ] Debug mode disabled outside local dev
- [ ] CORS rules are explicit (no wildcards in prod)
- [ ] Control / admin endpoints are authenticated

---

## 🧠 Architecture & Design
- [ ] Single clear entry point
- [ ] Business logic is NOT inside route handlers
- [ ] Strategy logic is decoupled from execution & UI
- [ ] No circular imports or hidden globals
- [ ] Configuration is environment-based

---

## ⚙️ Runtime & Concurrency
- [ ] No blocking calls in async paths
- [ ] Shared state is protected (locks / queues / immutability)
- [ ] Order execution is atomic & idempotent
- [ ] Graceful shutdown implemented
- [ ] Restart does not corrupt in-flight state

---

## 📈 Performance & Scalability
- [ ] No unbounded memory growth
- [ ] Expensive computations cached or batched
- [ ] I/O is async where required
- [ ] Rate limiting exists for critical endpoints

---

## 🚨 Error Handling & Observability
- [ ] No silent exception handling
- [ ] Errors include request + strategy context
- [ ] Structured logging (no print statements)
- [ ] Health check endpoint exists
- [ ] Metrics exposed (latency, error rate, throughput)

---

## 🧪 Testing & Safety
- [ ] Unit tests added/updated
- [ ] Integration tests cover order lifecycle
- [ ] External services are mocked
- [ ] Paper vs Live mode explicitly gated
- [ ] Kill switch / circuit breaker exists

---

## 🧾 Risk Declaration (REQUIRED)
**Worst-case failure if this PR is wrong:**
- 

**Rollback plan:**
- 

---

## ✅ Reviewer Sign-off
- [ ] I understand the execution & capital risk
- [ ] I have reviewed all unchecked boxes
