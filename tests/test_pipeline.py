import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentready.config import Settings  # noqa: E402
from agentready.llm import extract_json, strip_reasoning  # noqa: E402
from agentready.metrics import readiness_score, wilson  # noqa: E402
from agentready.pipeline import Pipeline, RunOptions  # noqa: E402
from agentready.repo import detect_test_command, fetch_repo, new_temp_root, python_shim_dir  # noqa: E402
from agentready.sandbox import denied  # noqa: E402
from agentready.tasks import propose_mutations  # noqa: E402
from fake_llm import ScriptedLLM  # noqa: E402

DEMO = ROOT / "examples" / "parcelpost"


class HelperTests(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('<think>hmm {"x":2}</think> sure: {"a": "}"}'), {"a": "}"})
        self.assertEqual(strip_reasoning("<think>x</think>hello"), "hello")

    def test_wilson(self):
        lo, hi = wilson(5, 10)
        self.assertTrue(0.2 < lo < 0.5 < hi < 0.8)
        self.assertEqual(readiness_score({"n": 0}), 0)

    def test_sandbox_policy(self):
        self.assertIsNotNone(denied("rm -rf /"))
        self.assertIsNotNone(denied("git push origin main"))
        self.assertIsNone(denied("python -m unittest discover -s tests"))

    def test_mutations_only_touch_source(self):
        props = propose_mutations(DEMO, random.Random(1))
        self.assertTrue(props)
        self.assertTrue(all(not p["file"].startswith("tests/") for p in props))

    def test_detects_green_command(self):
        work = new_temp_root()
        src = fetch_repo(str(DEMO), work)
        info = detect_test_command(src, python_shim_dir(work))
        self.assertTrue(info["baseline_ok"])
        self.assertEqual(info["env"], {"PYTHONPATH": "src"})


class SymlinkTests(unittest.TestCase):
    def test_links_out_of_a_clone_are_dropped(self):
        from agentready.repo import _drop_escaping_links
        root = Path(tempfile.mkdtemp())
        (root / "a.py").write_text("x = 1")
        try:
            (root / "inside.py").symlink_to(root / "a.py")
            (root / "README.md").symlink_to(Path(tempfile.mkdtemp()))
        except OSError:
            self.skipTest("this OS user can't create symlinks")
        _drop_escaping_links(root)
        self.assertTrue((root / "inside.py").exists())
        self.assertFalse((root / "README.md").is_symlink())


class PipelineTests(unittest.TestCase):
    def test_end_to_end_with_scripted_model(self):
        runs = Path(tempfile.mkdtemp())
        settings = Settings(runs_dir=runs)
        llm = ScriptedLLM(DEMO)
        opts = RunOptions(source=str(DEMO), tasks=4, repeats=1, parallel=2, max_steps=10)
        st = Pipeline(settings, llm, opts).run()
        self.assertEqual(st.status, "done", st.error)
        self.assertEqual(len(st.tasks), 4)
        self.assertEqual(len(st.episodes), 8)
        m = st.metrics
        self.assertEqual(m["improved"]["success_rate"], 1.0)
        self.assertLess(m["improved"]["avg_steps"], m["baseline"]["avg_steps"])
        self.assertRegex(st.agents_md, r"python -m (pytest|unittest)")  # pytest wins when installed
        saved = json.loads((runs / st.id / "run.json").read_text())
        self.assertEqual(saved["status"], "done")
        self.assertTrue((runs / st.id / "AGENTS.md").exists())
        for t in st.tasks:
            self.assertNotIn(".py", t["issue"])

    def test_cancel_stops_run(self):
        settings = Settings(runs_dir=Path(tempfile.mkdtemp()))
        pipe = Pipeline(settings, ScriptedLLM(DEMO), RunOptions(source=str(DEMO), tasks=2, repeats=1))
        pipe.cancel()
        st = pipe.run()
        self.assertEqual(st.status, "cancelled")
        self.assertEqual(st.episodes, {})


if __name__ == "__main__":
    unittest.main()
