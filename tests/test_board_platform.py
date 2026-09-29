import json
from pathlib import Path
import shutil
import subprocess
import unittest


BOARD_JS = Path(__file__).resolve().parents[1] / "board" / "board.js"


class BoardPlatform(unittest.TestCase):
    def test_legacy_cards_use_listing_url_when_site_is_missing(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is needed to execute the board's JavaScript")

        source = BOARD_JS.read_text(encoding="utf-8")
        start = source.index("    function sourcePlatform(j){")
        end = source.index("\n    var DIMS=[", start)
        classifier = source[start:end]
        cards = [
            {"id": "https://www.104.com.tw/job/8bbbb"},
            {"id": "https://jobs.lever.co/northwind/1"},
            {"id": "https://job-boards.greenhouse.io/northwind/jobs/1"},
            {"id": "old-card-id"},
        ]
        script = (
            classifier
            + "\nconsole.log(JSON.stringify("
            + json.dumps(cards)
            + ".map(sourcePlatform)));"
        )

        result = subprocess.run(
            [node, "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(json.loads(result.stdout), ["104", "Lever", "Greenhouse", "不明"])


if __name__ == "__main__":
    unittest.main()
