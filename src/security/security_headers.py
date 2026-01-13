"""
Security Headers Middleware

Implements OWASP-recommended security headers for HTTP responses.

Headers added:
- Content-Security-Policy: XSS and injection attack prevention
- X-Frame-Options: Clickjacking prevention
- X-Content-Type-Options: MIME sniffing prevention
- Strict-Transport-Security: Force HTTPS
- Referrer-Policy: Control referrer information
- X-XSS-Protection: Legacy XSS protection (deprecated but useful)
- Permissions-Policy: Control browser features
"""

from flask import Flask
from werkzeug.wrappers import Response


def add_security_headers(app: Flask) -> Flask:
    """
    Register security header middleware with Flask application.
    
    Args:
        app: Flask application instance
        
    Returns:
        Flask application with security headers middleware registered
    """
    
    @app.after_request
    def set_security_headers(response: Response) -> Response:
        """Add security headers to all responses."""
        
        # Content-Security-Policy: Prevent XSS and injection attacks
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "  # Allow inline for now (Phase 4: external scripts)
            "style-src 'self' 'unsafe-inline'; "   # Allow inline styles
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        
        # X-Frame-Options: Prevent clickjacking
        response.headers['X-Frame-Options'] = 'DENY'
        
        # X-Content-Type-Options: Prevent MIME sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        # Strict-Transport-Security: Force HTTPS
        # Note: In development, this is set with includeSubDomains=false
        # In production, ensure HTTPS is enabled and increase max-age
        response.headers['Strict-Transport-Security'] = (
            'max-age=31536000; includeSubDomains; preload'
        )
        
        # Referrer-Policy: Control information sent in Referer header
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Permissions-Policy: Disable unnecessary browser features
        response.headers['Permissions-Policy'] = (
            'geolocation=(), '
            'microphone=(), '
            'camera=(), '
            'accelerometer=(), '
            'gyroscope=(), '
            'magnetometer=(), '
            'usb=(), '
            'payment=()'
        )
        
        # X-XSS-Protection: Legacy header (modern browsers use CSP)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # Remove potentially revealing headers
        response.headers.pop('Server', None)
        response.headers.pop('X-Powered-By', None)
        
        return response
    
    return app


# ============================================================================
# Security Headers Reference
# ============================================================================
#
# Content-Security-Policy (CSP):
#   - default-src 'self': Only allow resources from same origin by default
#   - script-src: Where scripts can be loaded from
#   - style-src: Where stylesheets can be loaded from
#   - img-src: Where images can be loaded from
#   - frame-ancestors: Who can embed this page (DENY = no one)
#
# X-Frame-Options:
#   - DENY: Page cannot be displayed in a frame
#   - SAMEORIGIN: Only same origin can frame this page
#
# X-Content-Type-Options:
#   - nosniff: Browser must respect Content-Type header
#   - Prevents MIME sniffing attacks
#
# Strict-Transport-Security (HSTS):
#   - max-age: How long to enforce HTTPS (in seconds)
#   - includeSubDomains: Apply to all subdomains
#   - preload: Enable HSTS preload list submission
#
# Referrer-Policy:
#   - strict-origin-when-cross-origin: Send origin only for cross-origin
#
# Permissions-Policy:
#   - Control access to browser APIs (geolocation, microphone, etc.)
#
# ============================================================================
