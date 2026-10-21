"""CPU-only guarded packaging and launch-gate tests; no downloads or GPU imports."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from cases import public_prompt
from contracts import canonical, sha
import dataset
from grpo import VERSIONS
import grpo_guarded as runtime
import grpo_guarded_bundle as bundle


def fake_source(root):
    """A valid immutable fixture with deliberately unparsable old blind bytes."""
    source = root / "source"
    source.mkdir()
    cases = dataset.build_cases()["validation"]
    entries = {
        "evaluation/validation.jsonl": bundle.rows_bytes(cases),
        "evaluation/blind_test.jsonl": b"sealed-old-blind: do not deserialize or copy",
        "v1-adapter/adapter_config.json": b"{}",
        "v1-adapter/adapter_model.safetensors": b"fixture-only-never-load-a-model",
        "validation.prompts.jsonl": bundle.rows_bytes(
            {"id": c["id"], "prompt": public_prompt(c)} for c in cases),
    }
    for name, raw in entries.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = {
        "schema": "tool-rpo-corrected-same-gpu-v1", "model_revision": bundle.MODEL_REVISION,
        "review": {"status": "user_confirmed_sample_review_completed"},
        "files": {name: sha(raw) for name, raw in entries.items()},
        "versions": {**VERSIONS, "torch": "2.9.1+cu128"},
        "model_files_sha256": {"config.json": "a" * 64},
        "v1_adapter_sha256": {name: sha(entries["v1-adapter/" + name])
                              for name in ("adapter_config.json", "adapter_model.safetensors")},
        "v1_validation": {"n": 200, "correct": 180, "unsafe": 3, "contract_errors": 4},
    }
    (source / "corrected-bundle.json").write_text(canonical(manifest), encoding="utf-8")
    return source


class GuardedBundleTests(unittest.TestCase):
    def test_bundle_roundtrip_preserves_source_and_seals_new_blind(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fake_source(root)
            snapshot = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
            out = root / "guarded"
            result = bundle.build(source, out)
            manifest, splits = runtime.load_bundle(out)
            self.assertEqual(set(splits), {"train", "qualification", "validation", "focused"})
            self.assertEqual(manifest["counts"], bundle.COUNTS)
            self.assertIs(manifest["human_reviewed"], False)
            self.assertEqual(manifest["review"]["status"], "pending_direct_user_review")
            self.assertEqual(manifest["review"]["boundary_pairs"], 8)
            self.assertEqual(manifest["review"]["training_samples"], 16)
            self.assertEqual(manifest["data_sha256"], sha(canonical(manifest["data_files_sha256"])))
            self.assertEqual(manifest["data_sha256"], runtime.data_digest(manifest["files"]))
            self.assertEqual(result["zip_sha256"], bundle.file_sha(Path(result["zip"])))
            self.assertEqual(result["optimizer_steps"], 0)
            self.assertFalse(result["gpu_loaded"])
            self.assertEqual({p: p.read_bytes() for p in snapshot}, snapshot)
            self.assertEqual((out / "evaluation/validation.jsonl").read_bytes(),
                             snapshot[source / "evaluation/validation.jsonl"])
            self.assertFalse((out / "evaluation/blind_test.jsonl").exists())
            self.assertNotEqual(bundle.file_sha(out / "evaluation/blind.jsonl"),
                                bundle.file_sha(source / "evaluation/blind_test.jsonl"))
            review = (out / "train-review.txt").read_text(encoding="utf-8")
            self.assertEqual(review.count("SAMPLE "), 16)
            self.assertEqual(review.count("correct_score: "), 16)
            self.assertEqual(review.count("wrong_score: "), 16)
            self.assertIn("human_reviewed=false", review)
            reviewed_ids = [line.split(": ", 1)[1] for line in review.splitlines() if line.startswith("SAMPLE ")]
            self.assertTrue(set(reviewed_ids) <= {c["id"] for c in splits["train"]})
            with zipfile.ZipFile(result["zip"]) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(set(z.namelist()), bundle.RUNTIME | bundle.ARTIFACTS | {"guarded-bundle.json"})
                for name, digest in manifest["files"].items():
                    self.assertEqual(sha(z.read(name)), digest)
            with self.assertRaisesRegex(ValueError, "new_guarded_output_required"):
                bundle.build(source, out)
            (out / "train.oracle.jsonl").write_bytes(b"tamper")
            with self.assertRaisesRegex(ValueError, "bundle_changed"):
                runtime.load_bundle(out)

    def test_changed_source_and_unsafe_manifest_names_fail_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = fake_source(root)
            (source / "v1-adapter/adapter_config.json").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "source_bundle_changed"):
                bundle.build(source, root / "never-created")
            self.assertFalse((root / "never-created").exists())
        for name in ("../outside", "/absolute", "C:/outside", "some\\file", "a/./b", ""):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid_manifest_path"):
                bundle.safe_relative(name)

    def test_frozen_import_closure_and_no_gpu_import_on_cpu_builder(self):
        entries = bundle.runtime_bytes(Path(__file__).parent)
        self.assertEqual(set(entries), bundle.RUNTIME)
        result = subprocess.run([sys.executable, "-B", "-c",
            "import sys; import grpo_guarded_bundle; import grpo_guarded; "
            "assert not ({'torch', 'transformers', 'trl', 'peft', 'huggingface_hub'} & sys.modules.keys())"],
            cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_default_bad_ack_and_existing_lock_never_install_or_download(self):
        bash = shutil.which("bash") or next((str(p) for p in (
            Path("C:/Program Files/Git/bin/bash.exe"), Path("C:/Program Files/Git/usr/bin/bash.exe")) if p.is_file()), None)
        if bash is None:
            self.skipTest("Bash is unavailable for launch-gate integration checks")
        script = Path(__file__).parent / "run_grpo_guarded.sh"
        syntax = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "guarded"
            result = bundle.build(fake_source(root), out)
            python_path = shlex.quote(Path(sys.executable).as_posix())
            wrapper = f'python3() {{ {python_path} "$@"; }}\nexport -f python3\nexec bash run_grpo_guarded.sh "$@"'
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            def launch(*args):
                return subprocess.run([bash, "-c", wrapper, "guarded-test", *args], cwd=out,
                                      capture_output=True, text=True, encoding="utf-8", env=environment)
            before = {p.relative_to(out) for p in out.rglob("*")}
            default = launch()
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertIn("Preflight only", default.stdout)
            bad = launch("--execute", "--ack-data-sha", "0" * 64)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("Exact caller-supplied", bad.stderr)
            missing = launch("--execute")
            self.assertNotEqual(missing.returncode, 0)
            self.assertEqual({p.relative_to(out) for p in out.rglob("*")}, before)
            (out / ".guarded-launch.lock").mkdir()
            locked = launch("--execute", "--ack-data-sha", result["data_sha256"])
            self.assertNotEqual(locked.returncode, 0)
            self.assertIn("Persistent launch lock exists", locked.stderr)
            self.assertFalse((out / ".venv-guarded").exists())
            self.assertFalse((out / "models").exists())
            self.assertFalse((out / "runs").exists())


if __name__ == "__main__":
    unittest.main()
