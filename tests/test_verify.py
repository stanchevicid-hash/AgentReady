"""Offline checks for the AI-code verifier against the parcelpost demo (no model, no network)."""

import difflib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentready import verify as verify_mod  # noqa: E402
from agentready.verify import score, verify  # noqa: E402

# offline: pretend PyPI knows exactly these projects
verify_mod.pypi_exists = lambda name: name in ("requests", "numpy")

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


class StandaloneTests(unittest.TestCase):
    """No repository: just the code (or a diff) someone got from an AI assistant."""

    def test_plain_code(self):
        code = "import requests|import leftpadx||def f(x):|    try:|        return eval(x)|    except:|        return None|"
        res = verify("", code.replace("|", chr(10)))
        c = by_key(res)
        self.assertEqual(c["applies"]["status"], "skip")
        self.assertEqual(c["syntax"]["status"], "pass")
        self.assertEqual(c["imports"]["status"], "fail")
        self.assertEqual(c["imports"]["items"], ["leftpadx"])  # requests is real, just not installed
        self.assertTrue(any("eval()" in i for i in c["risky"]["items"]))
        self.assertEqual(c["tests"]["status"], "skip")
        self.assertGreater(score(res["checks"]), 0)

    def test_broken_code(self):
        c = by_key(verify("", "def f(:|    pass|".replace("|", chr(10))))
        self.assertEqual(c["syntax"]["status"], "fail")

    def test_diff_without_its_repo(self):
        nl = chr(10)
        bad = patch_for("        return 0.12" + nl, "        import leftpadx" + nl + "        return eval('0.12')" + nl)
        c = by_key(verify("", bad))
        self.assertEqual(c["applies"]["status"], "skip")
        self.assertIn("leftpadx", c["imports"]["items"])
        self.assertTrue(any("eval()" in i for i in c["risky"]["items"]))
        self.assertNotEqual(c["syntax"]["status"], "fail")  # half a file can't be judged broken

    def test_good_code_scores_full(self):
        code = "import json||def load(path):|    with open(path) as f:|        return json.load(f)|"
        self.assertEqual(score(verify("", code.replace("|", chr(10)))["checks"]), 100)

if __name__ == "__main__":
    unittest.main()
