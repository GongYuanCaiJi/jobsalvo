import glob
import hashlib
import os
import re

import card
import cf
import ship


def ident(one):
    # ruleid: card-id-hash-outside-card
    return hashlib.sha1(one.encode()).hexdigest()[:12]


def title(j):
    # ruleid: card-name-cleanup-outside-card
    return re.sub(r'\*', '', j.get('target'))


def title_suffix(old):
    # ruleid: card-name-cleanup-outside-card
    return re.sub(r'（\[.*', '', old)


def folder():
    # ruleid: ship-folder-scan-outside-ship
    return glob.glob(os.path.join(cf.SHIP_DIR, '*'))


def allowed(url, job, SHIP_ROOT):
    # ok: ship-folder-scan-outside-ship
    d = ship.folder(url, root=SHIP_ROOT)
    # ok: card-name-cleanup-outside-card
    return card.name(job), d
