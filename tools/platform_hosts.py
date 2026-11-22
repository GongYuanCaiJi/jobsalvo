#!/usr/bin/env python3
"""Load the editable platform-to-host defaults used during registry upgrades."""
from __future__ import annotations

import json
import os

from web.seen import host as host_key


DEFAULT_HOSTS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'defaults', 'platform_hosts.json')


def _listed_host(value):
    value = str(value or '').strip()
    try:
        return host_key(value if '//' in value else f'//{value}')
    except ValueError:
        return ''


def defaults():
    with open(DEFAULT_HOSTS_FILE, encoding='utf-8') as source:
        data = json.load(source)
    return {str(platform): [_listed_host(host) for host in hosts] for platform, hosts in data.items()}
