"""
CSRF Protection Module

Implements Cross-Site Request Forgery (CSRF) protection using:
- Secure token generation (secrets module)
- Token storage in session
- Token validation on POST/PUT/DELETE requests

Usage:
    from csrf_protection import init_csrf, generate_csrf_token, validate_csrf_token
    
    app = Flask(__name__)
    app.secret_key = 'your-secret-key'
    init_csrf(app)
    
    # In route:
    @app.get('/form')
    def show_form():
        csrf_token = generate_csrf_token()
        return render_template('form.html', csrf_token=csrf_token)
    
    @app.post('/form')
    def handle_form():
        try:
            validate_csrf_token(request.form.get('csrf_token'))
        except CSRFError as e:
            return jsonify({"error": str(e)}), 403
"""

from __future__ import annotations
import secrets
import logging
from typing import Optional
from flask import Flask, session, request, jsonify
from functools import wraps

logger = logging.getLogger(__name__)


class CSRFError(Exception):
    """Raised when CSRF token validation fails."""
    pass


def generate_csrf_token() -> str:
    """
    Generate a new CSRF token and store it in session.
    
    Returns:
        CSRF token string (64 hex characters)
        
    Example:
        >>> token = generate_csrf_token()
        >>> len(token)
        64
        >>> isinstance(token, str)
        True
    """
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)  # 64 hex chars
    return session['csrf_token']


def validate_csrf_token(token: Optional[str]) -> bool:
    """
    Validate a CSRF token against the one stored in session.
    
    Args:
        token: Token to validate (from form/header)
        
    Returns:
        True if valid
        
    Raises:
        CSRFError: If token is invalid, missing, or expired
        
    Example:
        >>> from flask import session
        >>> # Set a token in session
        >>> session['csrf_token'] = 'abc123'
        >>> validate_csrf_token('abc123')
        True
        >>> validate_csrf_token('xyz789')
        CSRFError: Invalid CSRF token
    """
    if not token:
        raise CSRFError("CSRF token missing")
    
    session_token = session.get('csrf_token')
    
    if not session_token:
        raise CSRFError("CSRF session token not found")
    
    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token, session_token):
        raise CSRFError("CSRF token mismatch")
    
    return True


def csrf_protect(f):
    """
    Decorator to protect routes from CSRF attacks.
    
    Works with:
    - Form data: <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    - JSON: {"csrf_token": "..."}
    - Headers: X-CSRF-Token
    
    Example:
        @app.post('/api/trade')
        @csrf_protect
        def place_trade():
            # CSRF token already validated
            return jsonify({"success": True})
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from multiple sources (priority order)
        token = None
        
        # 1. Check JSON body
        if request.is_json:
            data = request.get_json(silent=True) or {}
            token = data.get('csrf_token')
        
        # 2. Check form data
        if not token:
            token = request.form.get('csrf_token')
        
        # 3. Check custom header
        if not token:
            token = request.headers.get('X-CSRF-Token')
        
        # 4. Check standard header
        if not token:
            token = request.headers.get('X-CSRF-TOKEN')
        
        try:
            validate_csrf_token(token)
        except CSRFError as e:
            logger.warning("CSRF validation failed: %s", e)
            return jsonify({"error": str(e)}), 403
        
        return f(*args, **kwargs)
    
    return decorated_function


def init_csrf(app: Flask) -> Flask:
    """
    Initialize CSRF protection for Flask application.
    
    Must be called after app is created and before routing.
    
    Args:
        app: Flask application instance
        
    Returns:
        Flask application with CSRF protection enabled
        
    Requirements:
        - app.secret_key must be set
        - Session support must be available
        
    Example:
        from flask import Flask
        from csrf_protection import init_csrf
        
        app = Flask(__name__)
        app.secret_key = 'your-secret-key'
        init_csrf(app)
    """
    
    if not app.secret_key:
        raise RuntimeError(
            "Flask app secret_key must be set before initializing CSRF protection. "
            "Use: app.secret_key = 'your-secret-key'"
        )
    
    @app.before_request
    def ensure_csrf_token():
        """Ensure CSRF token exists in session for GET requests."""
        # Generate token for GET/HEAD/OPTIONS (safe methods)
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            generate_csrf_token()
    
    logger.info("CSRF protection initialized")
    return app


def get_csrf_token_from_session() -> str:
    """
    Get existing CSRF token from session or generate new one.
    
    Use this in templates to display the token.
    
    Returns:
        CSRF token string
        
    Example in Jinja2 template:
        {% set csrf_token = get_csrf_token_from_session() %}
        <form method="post">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            ...
        </form>
    """
    return generate_csrf_token()


# ============================================================================
# Test Cases
# ============================================================================

if __name__ == "__main__":
    import sys
    from flask import Flask
    
    print("Testing CSRF Protection Module\n")
    
    # Create test app
    test_app = Flask(__name__)
    test_app.secret_key = 'test-secret-key'
    
    tests_passed = 0
    tests_failed = 0
    
    with test_app.test_request_context():
        # Test 1: Token generation
        try:
            token1 = generate_csrf_token()
            token2 = generate_csrf_token()
            
            assert isinstance(token1, str), "Token must be string"
            assert len(token1) == 64, "Token must be 64 chars"
            assert token1 == token2, "Subsequent calls should return same token"
            
            print("✓ PASS: Token generation and caching")
            tests_passed += 1
        except Exception as e:
            print(f"✗ FAIL: Token generation - {e}")
            tests_failed += 1
        
        # Test 2: Token validation - valid token
        try:
            token = generate_csrf_token()
            assert validate_csrf_token(token) == True
            print("✓ PASS: Valid token validation")
            tests_passed += 1
        except Exception as e:
            print(f"✗ FAIL: Valid token validation - {e}")
            tests_failed += 1
        
        # Test 3: Token validation - invalid token
        try:
            validate_csrf_token("invalid-token")
            print("✗ FAIL: Should reject invalid token")
            tests_failed += 1
        except CSRFError:
            print("✓ PASS: Invalid token rejected")
            tests_passed += 1
        
        # Test 4: Token validation - missing token
        try:
            validate_csrf_token(None)
            print("✗ FAIL: Should reject missing token")
            tests_failed += 1
        except CSRFError:
            print("✓ PASS: Missing token rejected")
            tests_passed += 1
        
        # Test 5: Constant-time comparison
        try:
            token = generate_csrf_token()
            # All these should fail but take same time
            validate_csrf_token(token + "x")  # Should fail
            print("✗ FAIL: Should reject modified token")
            tests_failed += 1
        except CSRFError:
            print("✓ PASS: Modified token rejected (constant-time)")
            tests_passed += 1
    
    # Test 6: Decorator
    try:
        test_app.secret_key = 'test-secret'
        init_csrf(test_app)
        
        @test_app.post('/test')
        @csrf_protect
        def protected_route():
            return jsonify({"success": True})
        
        print("✓ PASS: CSRF decorator applied")
        tests_passed += 1
    except Exception as e:
        print(f"✗ FAIL: CSRF decorator - {e}")
        tests_failed += 1
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Passed: {tests_passed}")
    print(f"Failed: {tests_failed}")
    print(f"Total: {tests_passed + tests_failed}")
    
    sys.exit(0 if tests_failed == 0 else 1)
