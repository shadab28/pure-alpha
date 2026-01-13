"""
Input Validation Module

Provides comprehensive input validation for trading application.
Validates symbols, quantities, prices, and other trading parameters.

Usage:
    from validation import validate_symbol, validate_quantity, validate_price
    
    try:
        symbol = validate_symbol(user_input)
        quantity = validate_quantity(qty_input)
        price = validate_price(price_input)
    except ValidationError as e:
        logger.error(f"Validation failed: {e}")
        return jsonify({"error": str(e)}), 400
"""

from __future__ import annotations
import re
from typing import Any
from decimal import Decimal, InvalidOperation


class ValidationError(ValueError):
    """Raised when input validation fails."""
    pass


def validate_symbol(symbol: Any) -> str:
    """
    Validate a trading symbol (NSE format).
    
    Args:
        symbol: User input to validate
        
    Returns:
        Cleaned symbol string
        
    Raises:
        ValidationError: If symbol is invalid
        
    Examples:
        >>> validate_symbol("AAPL")
        'AAPL'
        >>> validate_symbol("aapl")
        'AAPL'
        >>> validate_symbol("<script>")
        ValidationError: Invalid symbol format
    """
    if not isinstance(symbol, str):
        raise ValidationError(f"Symbol must be string, got {type(symbol).__name__}")
    
    symbol = symbol.strip().upper()
    
    if not symbol:
        raise ValidationError("Symbol cannot be empty")
    
    if len(symbol) > 20:
        raise ValidationError(f"Symbol too long (max 20 chars): {len(symbol)}")
    
    # Allow only alphanumeric and common separators (&, -, %)
    if not re.match(r'^[A-Z0-9&\-\%]{1,20}$', symbol):
        raise ValidationError(f"Invalid symbol format: {symbol}")
    
    return symbol


def validate_quantity(quantity: Any) -> int:
    """
    Validate order quantity.
    
    Args:
        quantity: User input to validate
        
    Returns:
        Validated quantity as integer
        
    Raises:
        ValidationError: If quantity is invalid
        
    Examples:
        >>> validate_quantity("100")
        100
        >>> validate_quantity(50)
        50
        >>> validate_quantity("-10")
        ValidationError: Quantity must be positive
    """
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError(f"Quantity must be integer: {quantity}")
    
    if qty <= 0:
        raise ValidationError("Quantity must be positive")
    
    if qty > 100000:
        raise ValidationError(f"Quantity too large (max 100000): {qty}")
    
    return qty


def validate_price(price: Any, min_price: float = 0.01, max_price: float = 999999.99) -> float:
    """
    Validate order/trigger price.
    
    Args:
        price: User input to validate
        min_price: Minimum allowed price
        max_price: Maximum allowed price
        
    Returns:
        Validated price as float
        
    Raises:
        ValidationError: If price is invalid
        
    Examples:
        >>> validate_price("150.50")
        150.5
        >>> validate_price(100)
        100.0
        >>> validate_price("-10.50")
        ValidationError: Price must be positive
    """
    try:
        # Use Decimal for precise validation
        price_decimal = Decimal(str(price))
        price_float = float(price_decimal)
    except (TypeError, ValueError, InvalidOperation):
        raise ValidationError(f"Price must be a valid number: {price}")
    
    if price_float < min_price:
        raise ValidationError(f"Price too low (min {min_price}): {price_float}")
    
    if price_float > max_price:
        raise ValidationError(f"Price too high (max {max_price}): {price_float}")
    
    # Round to 2 decimal places
    return round(price_float, 2)


def validate_order_type(order_type: Any) -> str:
    """
    Validate order type.
    
    Args:
        order_type: User input to validate
        
    Returns:
        Validated order type (BUY or SELL)
        
    Raises:
        ValidationError: If order type is invalid
        
    Examples:
        >>> validate_order_type("BUY")
        'BUY'
        >>> validate_order_type("buy")
        'BUY'
        >>> validate_order_type("DELETE")
        ValidationError: Invalid order type
    """
    if not isinstance(order_type, str):
        raise ValidationError(f"Order type must be string: {order_type}")
    
    order_type = order_type.strip().upper()
    
    if order_type not in ['BUY', 'SELL']:
        raise ValidationError(f"Order type must be BUY or SELL: {order_type}")
    
    return order_type


def validate_timeframe(timeframe: Any) -> str:
    """
    Validate trading timeframe.
    
    Args:
        timeframe: User input to validate
        
    Returns:
        Validated timeframe
        
    Raises:
        ValidationError: If timeframe is invalid
        
    Examples:
        >>> validate_timeframe("15m")
        '15m'
        >>> validate_timeframe("1h")
        '1h'
        >>> validate_timeframe("2h")
        ValidationError: Invalid timeframe
    """
    if not isinstance(timeframe, str):
        raise ValidationError(f"Timeframe must be string: {timeframe}")
    
    timeframe = timeframe.strip().lower()
    
    valid_timeframes = ['1m', '5m', '15m', '30m', '60m', '1h', '4h', '1d']
    
    if timeframe not in valid_timeframes:
        raise ValidationError(f"Invalid timeframe: {timeframe}. Valid: {', '.join(valid_timeframes)}")
    
    return timeframe


def validate_gtt_type(gtt_type: Any) -> str:
    """
    Validate GTT (Good Till Triggered) order type.
    
    Args:
        gtt_type: User input to validate
        
    Returns:
        Validated GTT type
        
    Raises:
        ValidationError: If GTT type is invalid
        
    Examples:
        >>> validate_gtt_type("OCO")
        'OCO'
        >>> validate_gtt_type("oco")
        'OCO'
    """
    if not isinstance(gtt_type, str):
        raise ValidationError(f"GTT type must be string: {gtt_type}")
    
    gtt_type = gtt_type.strip().upper()
    
    valid_types = ['OCO', 'SINGLE', 'BRACKET']
    
    if gtt_type not in valid_types:
        raise ValidationError(f"Invalid GTT type: {gtt_type}. Valid: {', '.join(valid_types)}")
    
    return gtt_type


def validate_email(email: Any) -> str:
    """
    Validate email address (for future user management).
    
    Args:
        email: User input to validate
        
    Returns:
        Validated email
        
    Raises:
        ValidationError: If email is invalid
        
    Examples:
        >>> validate_email("user@example.com")
        'user@example.com'
        >>> validate_email("invalid-email")
        ValidationError: Invalid email format
    """
    if not isinstance(email, str):
        raise ValidationError(f"Email must be string: {email}")
    
    email = email.strip().lower()
    
    # RFC 5322 simplified regex
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    if not re.match(pattern, email):
        raise ValidationError(f"Invalid email format: {email}")
    
    if len(email) > 254:
        raise ValidationError(f"Email too long: {len(email)}")
    
    return email


def validate_password(password: Any) -> str:
    """
    Validate password strength (for future authentication).
    
    Args:
        password: User input to validate
        
    Returns:
        Password (only returns if valid)
        
    Raises:
        ValidationError: If password doesn't meet requirements
        
    Requirements:
        - Minimum 8 characters
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one digit
        - At least one special character
    """
    if not isinstance(password, str):
        raise ValidationError("Password must be string")
    
    if len(password) < 8:
        raise ValidationError("Password must be at least 8 characters")
    
    if not re.search(r'[A-Z]', password):
        raise ValidationError("Password must contain uppercase letter")
    
    if not re.search(r'[a-z]', password):
        raise ValidationError("Password must contain lowercase letter")
    
    if not re.search(r'\d', password):
        raise ValidationError("Password must contain digit")
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        raise ValidationError("Password must contain special character")
    
    return password


def validate_dict_keys(data: Any, required_keys: list[str]) -> dict:
    """
    Validate that dictionary contains all required keys.
    
    Args:
        data: Dictionary to validate
        required_keys: List of required keys
        
    Returns:
        Validated dictionary
        
    Raises:
        ValidationError: If required keys are missing
        
    Examples:
        >>> validate_dict_keys({"a": 1, "b": 2}, ["a", "b"])
        {"a": 1, "b": 2}
        >>> validate_dict_keys({"a": 1}, ["a", "b"])
        ValidationError: Missing required keys: ['b']
    """
    if not isinstance(data, dict):
        raise ValidationError(f"Data must be dictionary: {type(data).__name__}")
    
    missing = [k for k in required_keys if k not in data]
    
    if missing:
        raise ValidationError(f"Missing required keys: {missing}")
    
    return data


# ============================================================================
# Test Functions (can be run with: python -m pytest validation.py)
# ============================================================================

if __name__ == "__main__":
    import sys
    
    print("Testing Input Validation Module\n")
    
    # Test cases
    tests = [
        ("Symbol validation - valid", lambda: validate_symbol("AAPL"), "AAPL"),
        ("Symbol validation - lowercase", lambda: validate_symbol("aapl"), "AAPL"),
        ("Symbol validation - with dash", lambda: validate_symbol("BRK-A"), "BRK-A"),
        ("Quantity validation - valid", lambda: validate_quantity("100"), 100),
        ("Quantity validation - int", lambda: validate_quantity(50), 50),
        ("Price validation - valid", lambda: validate_price("150.50"), 150.5),
        ("Price validation - decimal", lambda: validate_price(100), 100.0),
        ("Order type - BUY", lambda: validate_order_type("BUY"), "BUY"),
        ("Order type - buy", lambda: validate_order_type("buy"), "BUY"),
        ("Timeframe - 15m", lambda: validate_timeframe("15m"), "15m"),
        ("Email - valid", lambda: validate_email("user@example.com"), "user@example.com"),
        ("Dict keys - valid", lambda: validate_dict_keys({"a": 1, "b": 2}, ["a", "b"]), {"a": 1, "b": 2}),
    ]
    
    error_tests = [
        ("Symbol validation - invalid chars", lambda: validate_symbol("<script>")),
        ("Quantity validation - negative", lambda: validate_quantity("-10")),
        ("Price validation - negative", lambda: validate_price("-10.50")),
        ("Order type - invalid", lambda: validate_order_type("DELETE")),
        ("Timeframe - invalid", lambda: validate_timeframe("2h")),
        ("Email - invalid", lambda: validate_email("invalid-email")),
        ("Dict keys - missing", lambda: validate_dict_keys({"a": 1}, ["a", "b"])),
    ]
    
    passed = 0
    failed = 0
    
    print("Positive Tests:")
    for name, test_func, expected in tests:
        try:
            result = test_func()
            if result == expected:
                print(f"  ✓ {name}")
                passed += 1
            else:
                print(f"  ✗ {name} - got {result}, expected {expected}")
                failed += 1
        except Exception as e:
            print(f"  ✗ {name} - {e}")
            failed += 1
    
    print("\nNegative Tests (should raise ValidationError):")
    for name, test_func in error_tests:
        try:
            test_func()
            print(f"  ✗ {name} - should have raised ValidationError")
            failed += 1
        except ValidationError:
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name} - wrong exception: {e}")
            failed += 1
    
    print(f"\n{'='*60}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total: {passed + failed}")
    
    sys.exit(0 if failed == 0 else 1)
