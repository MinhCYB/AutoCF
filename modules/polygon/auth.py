"""
Polygon API authentication — HMAC-SHA512 signature generation.

Every Polygon API request requires:
  apiKey   = <key>
  time     = unix timestamp
  apiSig   = rand(6) + SHA512(rand + "/" + method + "?" + sorted_params + "#" + secret)

Reference: https://codeforces.github.io/polygon-misc/API#authorization
"""

import hashlib
import random
import string
import time as _time


def _random_prefix(length: int = 6) -> str:
    """Generate random alphanumeric prefix for API signature."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))


def make_api_sig(method_name: str, params: dict, secret: str) -> str:
    """
    Build the apiSig value for a Polygon API request.

    Returns: rand(6 chars) + hex(SHA-512(rand/method?sorted_params#secret))
    """
    rand = _random_prefix()

    # Sort params lexicographically by (key, value)
    sorted_pairs = sorted(params.items(), key=lambda kv: (kv[0], str(kv[1])))
    param_string = "&".join(f"{k}={v}" for k, v in sorted_pairs)

    to_hash = f"{rand}/{method_name}?{param_string}#{secret}"
    hash_hex = hashlib.sha512(to_hash.encode("utf-8")).hexdigest()

    return rand + hash_hex


def sign_request(
    method_name: str,
    params: dict,
    api_key: str,
    secret: str,
) -> dict:
    """
    Add authentication parameters (apiKey, time, apiSig) to a request.

    Returns a new dict with all original params plus auth fields.
    """
    signed = dict(params)
    signed["apiKey"] = api_key
    signed["time"] = str(int(_time.time()))

    signed["apiSig"] = make_api_sig(method_name, signed, secret)
    return signed
