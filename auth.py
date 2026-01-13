"""
Authentication Module

Implements user authentication with:
- Secure password hashing (argon2)
- Session-based authentication
- User registration and login
- Logout and session cleanup
- Role-based user management

Usage:
    from auth import init_auth, hash_password, verify_password, require_login
    from auth import User, UserRole
    
    # Hashing passwords
    hashed = hash_password('mypassword')
    if verify_password('mypassword', hashed):
        print("Password match!")
    
    # Role-based access
    @app.get('/admin')
    @require_login(required_role=UserRole.ADMIN)
    def admin_panel():
        return "Admin only"
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from functools import wraps
import logging

from flask import Flask, session, request, jsonify

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
except ImportError:
    # Fallback to simple hashing if argon2 not available
    import hashlib
    PasswordHasher = None
    VerifyMismatchError = Exception

logger = logging.getLogger(__name__)

# Use Argon2 hasher for password hashing
if PasswordHasher:
    pwd_hasher = PasswordHasher()
else:
    pwd_hasher = None


class UserRole(Enum):
    """User roles with hierarchical permissions."""
    VIEWER = 1      # Read-only access
    TRADER = 2      # Can place trades
    ADMIN = 3       # Full access


class AuthError(Exception):
    """Raised when authentication fails."""
    pass


@dataclass
class User:
    """User data class."""
    user_id: int
    username: str
    email: str
    role: UserRole
    created_at: datetime
    last_login: Optional[datetime] = None
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert user to dictionary for JSON serialization."""
        return {
            'user_id': self.user_id,
            'username': self.username,
            'email': self.email,
            'role': self.role.name,
            'created_at': self.created_at.isoformat(),
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'is_active': self.is_active,
        }


def hash_password(password: str) -> str:
    """
    Hash a password using Argon2.
    
    Args:
        password: Plain text password
        
    Returns:
        Argon2 hash string (includes algorithm, salt, hash)
        
    Raises:
        ValueError: If password is invalid
        
    Example:
        >>> hashed = hash_password('mypassword123')
        >>> hashed.startswith('$argon2')
        True
    """
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    
    try:
        return pwd_hasher.hash(password)
    except Exception as e:
        logger.error("Password hashing failed: %s", e)
        raise


def verify_password(password: str, hash_str: str) -> bool:
    """
    Verify a password against its hash.
    
    Uses constant-time comparison to prevent timing attacks.
    
    Args:
        password: Plain text password to verify
        hash_str: Argon2 hash to compare against
        
    Returns:
        True if password matches, False otherwise
        
    Example:
        >>> hashed = hash_password('mypassword123')
        >>> verify_password('mypassword123', hashed)
        True
        >>> verify_password('wrongpassword', hashed)
        False
    """
    if not pwd_hasher:
        return False
    
    try:
        pwd_hasher.verify(hash_str, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False


def get_current_user() -> Optional[User]:
    """
    Get currently authenticated user from session.
    
    Returns:
        User object if authenticated, None otherwise
        
    Example:
        >>> user = get_current_user()
        >>> if user:
        ...     print(f"Logged in as {user.username}")
    """
    user_data = session.get('user')
    if not user_data:
        return None
    
    try:
        return User(
            user_id=user_data['user_id'],
            username=user_data['username'],
            email=user_data['email'],
            role=UserRole[user_data['role']],
            created_at=datetime.fromisoformat(user_data['created_at']),
            last_login=datetime.fromisoformat(user_data['last_login']) 
                      if user_data.get('last_login') else None,
            is_active=user_data.get('is_active', True),
        )
    except (KeyError, ValueError) as e:
        logger.error("Invalid user data in session: %s", e)
        return None


def login_user(user: User) -> None:
    """
    Log in a user by storing in session.
    
    Args:
        user: User object to authenticate
        
    Example:
        >>> user = User(1, 'john', 'john@example.com', UserRole.TRADER, datetime.now())
        >>> login_user(user)
    """
    user_data = user.to_dict()
    user_data['last_login'] = datetime.now().isoformat()
    
    session['user'] = user_data
    session.permanent = True
    
    logger.info("User logged in: %s", user.username)


def logout_user() -> None:
    """
    Log out current user by clearing session.
    
    Example:
        >>> logout_user()
    """
    username = session.get('user', {}).get('username', 'unknown')
    session.clear()
    logger.info("User logged out: %s", username)


def is_authenticated() -> bool:
    """
    Check if current request has authenticated user.
    
    Returns:
        True if user is logged in, False otherwise
        
    Example:
        >>> if is_authenticated():
        ...     print("User is logged in")
    """
    return get_current_user() is not None


def require_login(required_role: Optional[UserRole] = None):
    """
    Decorator to require authentication on routes.
    
    Args:
        required_role: Minimum role required (None = any authenticated user)
        
    Returns:
        Decorated function
        
    Example:
        @app.get('/trades')
        @require_login()
        def view_trades():
            user = get_current_user()
            return f"Trades for {user.username}"
        
        @app.post('/admin')
        @require_login(required_role=UserRole.ADMIN)
        def admin_panel():
            return "Admin access"
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            
            # Check if authenticated
            if not user:
                logger.warning("Unauthenticated access attempt to: %s", request.path)
                return jsonify({"error": "Authentication required"}), 401
            
            # Check if user is active
            if not user.is_active:
                logger.warning("Inactive user access attempt: %s", user.username)
                return jsonify({"error": "User account inactive"}), 403
            
            # Check role if required
            if required_role and user.role.value < required_role.value:
                logger.warning(
                    "Insufficient permissions: %s (role: %s) for: %s",
                    user.username, user.role.name, request.path
                )
                return jsonify({"error": "Insufficient permissions"}), 403
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator


def init_auth(app: Flask) -> Flask:
    """
    Initialize authentication for Flask application.
    
    Sets up session configuration and logging.
    
    Args:
        app: Flask application instance
        
    Returns:
        Flask application with auth initialized
        
    Requirements:
        - app.secret_key must be set
        - Session support must be available
        
    Example:
        from flask import Flask
        from auth import init_auth
        
        app = Flask(__name__)
        app.secret_key = 'your-secret-key'
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['PERMANENT_SESSION_LIFETIME'] = 3600
        
        init_auth(app)
    """
    
    if not app.secret_key:
        raise RuntimeError(
            "Flask app secret_key must be set before initializing auth. "
            "Use: app.secret_key = 'your-secret-key'"
        )
    
    # Secure session configuration
    app.config.setdefault('SESSION_COOKIE_SECURE', True)  # HTTPS only
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)  # No JS access
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')  # CSRF protection
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', 3600)  # 1 hour
    
    logger.info("Authentication initialized")
    return app


# ============================================================================
# Test Cases
# ============================================================================

if __name__ == "__main__":
    import sys
    
    print("Testing Authentication Module\n")
    
    tests_passed = 0
    tests_failed = 0
    
    # Test 1: Password hashing
    try:
        password = "MySecurePass123!"
        hashed = hash_password(password)
        
        assert isinstance(hashed, str), "Hash must be string"
        assert hashed.startswith("$argon2"), "Hash must use Argon2"
        assert hashed != password, "Hash must differ from plain text"
        
        print("✓ PASS: Password hashing")
        tests_passed += 1
    except Exception as e:
        print(f"✗ FAIL: Password hashing - {e}")
        tests_failed += 1
    
    # Test 2: Password verification - correct
    try:
        password = "MySecurePass123!"
        hashed = hash_password(password)
        assert verify_password(password, hashed) == True
        
        print("✓ PASS: Correct password verification")
        tests_passed += 1
    except Exception as e:
        print(f"✗ FAIL: Correct password verification - {e}")
        tests_failed += 1
    
    # Test 3: Password verification - incorrect
    try:
        password = "MySecurePass123!"
        hashed = hash_password(password)
        assert verify_password("WrongPassword123!", hashed) == False
        
        print("✓ PASS: Incorrect password rejected")
        tests_passed += 1
    except Exception as e:
        print(f"✗ FAIL: Incorrect password rejected - {e}")
        tests_failed += 1
    
    # Test 4: Weak password rejection
    try:
        hash_password("weak")  # Too short
        print("✗ FAIL: Should reject weak password")
        tests_failed += 1
    except ValueError:
        print("✓ PASS: Weak password rejected")
        tests_passed += 1
    
    # Test 5: User role hierarchy
    try:
        assert UserRole.VIEWER.value < UserRole.TRADER.value
        assert UserRole.TRADER.value < UserRole.ADMIN.value
        
        print("✓ PASS: User role hierarchy")
        tests_passed += 1
    except Exception as e:
        print(f"✗ FAIL: User role hierarchy - {e}")
        tests_failed += 1
    
    # Test 6: User to_dict conversion
    try:
        user = User(
            user_id=1,
            username='john',
            email='john@example.com',
            role=UserRole.TRADER,
            created_at=datetime.now(),
        )
        user_dict = user.to_dict()
        
        assert user_dict['username'] == 'john'
        assert user_dict['role'] == 'TRADER'
        assert 'created_at' in user_dict
        
        print("✓ PASS: User to_dict conversion")
        tests_passed += 1
    except Exception as e:
        print(f"✗ FAIL: User to_dict conversion - {e}")
        tests_failed += 1
    
    print(f"\n{'='*60}")
    print(f"Passed: {tests_passed}")
    print(f"Failed: {tests_failed}")
    print(f"Total: {tests_passed + tests_failed}")
    
    sys.exit(0 if tests_failed == 0 else 1)
