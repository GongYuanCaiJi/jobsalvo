#!/usr/bin/env python3
"""Shared URL-origin normalization for fetch and proxy security checks."""
import urllib.parse


def origin(url):
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or '').lower().rstrip('.')
    port = parts.port or (443 if scheme == 'https' else 80)
    return scheme, host, port
