import time
from collections import defaultdict

# Format: { "ip_or_email": (failure_count, lockout_until_timestamp) }
_rate_limits = defaultdict(lambda: [0, 0])

MAX_FAILURES = 5
LOCKOUT_DURATION_SECONDS = 300 # 5 minutes

def is_rate_limited(identifier: str) -> bool:
    """Check if the given identifier (IP or email) is currently locked out."""
    data = _rate_limits[identifier]
    failures, lockout_until = data[0], data[1]
    
    if time.time() < lockout_until:
        return True
        
    # If time has passed the lockout, reset failures
    if failures >= MAX_FAILURES and time.time() >= lockout_until:
        _rate_limits[identifier] = [0, 0]
        
    return False

def record_failure(identifier: str):
    """Record a failed attempt."""
    data = _rate_limits[identifier]
    data[0] += 1
    if data[0] >= MAX_FAILURES:
        data[1] = time.time() + LOCKOUT_DURATION_SECONDS
    _rate_limits[identifier] = data

def clear_failures(identifier: str):
    """Clear failures upon successful action."""
    if identifier in _rate_limits:
        _rate_limits[identifier] = [0, 0]
