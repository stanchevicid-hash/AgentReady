"""Offline checks for the AI-code verifier against the parcelpost demo (no model, no network)."""

import difflib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentready.verify import score, verify  # noqa: E402

DEMO = ROOT / "examples" / "parcelpost"
REL = "src/parcelpost/core/discounts.py"


def patch_for(old: str, new: str, rel: str = REL) -> str:
    src = (DEMO / rel).read_text(encoding="utf-8")
    assert old in src
    return "".join(difflib.unified_diff(src.splitlines(True), src.replace(old, new).splitlines(True),
                                        f"a/{rel}", f"b/{rel}"))


def by_key(res):
    return {c["key"]: c for c in res["checks"]}


class VerifyTests(unittest.TestCase):
    def test_good_patch_passes(self):
        res = verify(str(DEMO), patch_for('"""Customer discounts.', '"""Customer discounts (tidied).'))
        c = by_key(res)
        for k in ("applies", "syntax", "tests_edited", "risky", "imports", "tests"):
            self.assertEqual(c[k]["status"], "pass", (k, c[k]))
        self.assertEqual(c["ai_review"]["status"], "skip")
        self.assertEqual(score(res["checks"]), 100)

    def test_bad_patch_is_caught(self):
        bad = patch_for("        return 0.12\n",
                        "        import leftpadx\n        try:\n            return eval('0.5')\n        except:\n            pass\n")
        c = by_key(verify(str(DEMO), bad))
        self.assertEqual(c["imports"]["status"], "fail")
        self.assertIn("leftpadx", c["imports"]["items"])
        self.assertEqual(c["risky"]["status"], "warn")
        self.assertTrue(any("eval()" in i for i in c["risky"]["items"]))
        self.assertTrue(any("bare except" in i for i in c["risky"]["items"]))
        self.assertEqual(c["tests"]["status"], "fail")

    def test_broken_patch_does_not_apply(self):
        res = verify(str(DEMO), "--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-x\n+y\n")
        self.assertEqual(by_key(res)["applies"]["status"], "fail")
        self.assertEqual(score(res["checks"]), 0)


if __name__ == "__main__":
    unittest.main()
