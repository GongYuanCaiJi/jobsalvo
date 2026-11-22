import glob
import hashlib
import os
import re
import unittest

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


def allowed(url, job):
    # ok: ship-folder-scan-outside-ship
    d = ship.folder(url)
    # ok: card-name-cleanup-outside-card
    return card.name(job), d


class KnownBugs(unittest.TestCase):
    # ruleid: expected-failure-hides-a-known-bug
    @unittest.expectedFailure
    def test_known(self):
        self.fail()

    # ok: expected-failure-hides-a-known-bug
    def test_fixed(self):
        self.assertTrue(True)
