"""Verify last-session persistence without a GUI or AI dependencies."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.session import SESSION_FILENAME, load_session, save_session, source_signature


class SessionPersistenceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bokeh-session-check-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / "sessions"
        self.source = self.root / "原图.jpg"
        self.source.write_bytes(b"source-image-placeholder")
        self.depth = np.linspace(0, 1, 30, dtype=np.float32).reshape(5, 6)
        self.full_depth = np.linspace(0, 1, 120, dtype=np.float32).reshape(10, 12)
        self.snapshot = {
            "aperture": 9,
            "blur_slider": 160,
            "full_luminance_bokeh": True,
            "protect_subject": True,
            "parked_bokeh_values": {"bokeh_size": 500},
            "image_path": str(self.source),
            "original": np.zeros((5, 6, 3), dtype=np.uint8),
            "original_full": np.zeros((10, 12, 3), dtype=np.uint8),
            "depth": self.depth,
            "depth_full": self.full_depth,
            "depth_name": "导入深度.png",
            "model_ref": "depth-anything/Depth-Anything-V2-Small-hf",
        }

    def test_roundtrip_controls_and_both_depths_without_image_pixels(self):
        save_session(self.base, self.snapshot)
        result = load_session(self.base)
        self.assertTrue(result.available)
        self.assertEqual(result.status, "ready")
        self.assertFalse(result.warning)
        self.assertEqual(result.state["blur_slider"], 160)
        self.assertEqual(result.state["model_ref"], self.snapshot["model_ref"])
        self.assertEqual(result.state["parked_bokeh_values"], {"bokeh_size": 500})
        self.assertEqual(result.state["depth_name"], "导入深度.png")
        np.testing.assert_array_equal(result.state["depth"], self.depth)
        np.testing.assert_array_equal(result.state["depth_full"], self.full_depth)
        self.assertIsNone(result.state["original"])
        self.assertIsNone(result.state["original_full"])
        with np.load(self.base / SESSION_FILENAME, allow_pickle=False) as archive:
            self.assertEqual(set(archive.files), {"metadata", "depth", "depth_full"})

    def test_missing_session_has_no_directory_side_effect(self):
        result = load_session(self.base)
        self.assertEqual(result.status, "missing")
        self.assertFalse(result.available)
        self.assertFalse(self.base.exists())

    def test_missing_source_keeps_parameters_but_drops_image_and_depth(self):
        save_session(self.base, self.snapshot)
        self.source.unlink()
        result = load_session(self.base)
        self.assertEqual(result.status, "source-missing")
        self.assertTrue(result.warning)
        self.assertEqual(result.state["blur_slider"], 160)
        self.assertIsNone(result.state["image_path"])
        self.assertIsNone(result.state["depth_full"])
        self.assertIsNone(result.state["depth"])

    def test_changed_source_does_not_reuse_old_depth(self):
        save_session(self.base, self.snapshot)
        self.source.write_bytes(b"a changed source with a different file length")
        result = load_session(self.base)
        self.assertEqual(result.status, "source-changed")
        self.assertEqual(result.state["image_path"], str(self.source))
        self.assertEqual(result.state["aperture"], 9)
        self.assertIsNone(result.state["depth"])
        self.assertIsNone(result.state["depth_full"])

    def test_source_replaced_during_edit_is_still_detected_when_saving(self):
        loaded_signature = source_signature(self.source)
        snapshot = {**self.snapshot, "image_source_signature": loaded_signature}
        self.source.write_bytes(b"new pixels replaced the original during editing")
        save_session(self.base, snapshot)
        result = load_session(self.base)
        self.assertEqual(result.status, "source-changed")
        self.assertEqual(result.state["image_source_signature"], loaded_signature)
        self.assertNotEqual(result.state["image_source_signature"], source_signature(self.source))
        self.assertIsNone(result.state["depth"])
        self.assertIsNone(result.state["depth_full"])

    def test_explicit_unknown_signature_does_not_fall_back_to_current_stat(self):
        snapshot = {**self.snapshot, "image_source_signature": None}
        save_session(self.base, snapshot)
        result = load_session(self.base)
        self.assertEqual(result.status, "source-changed")
        self.assertIsNone(result.state["image_source_signature"])
        self.assertIsNone(result.state["depth"])
        self.assertIsNone(result.state["depth_full"])

    def test_loaded_signature_survives_resaving_unmodified_source(self):
        snapshot = {**self.snapshot, "image_source_signature": source_signature(self.source)}
        save_session(self.base, snapshot)
        result = load_session(self.base)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.state["image_source_signature"], source_signature(self.source))
        save_session(self.base, result.state)
        resumed = load_session(self.base)
        self.assertEqual(resumed.status, "ready")
        np.testing.assert_array_equal(resumed.state["depth"], self.depth)
        np.testing.assert_array_equal(resumed.state["depth_full"], self.full_depth)

    def test_failed_atomic_replace_keeps_previous_snapshot_and_cleans_temp(self):
        save_session(self.base, self.snapshot)
        original = (self.base / SESSION_FILENAME).read_bytes()
        newer = {**self.snapshot, "blur_slider": 80}
        with patch("app.session.os.replace", side_effect=OSError("write blocked")):
            with self.assertRaises(OSError):
                save_session(self.base, newer)
        self.assertEqual((self.base / SESSION_FILENAME).read_bytes(), original)
        self.assertEqual([p.name for p in self.base.iterdir()], [SESSION_FILENAME])
        self.assertEqual(load_session(self.base).state["blur_slider"], 160)

    def test_corrupt_archive_returns_nonfatal_result(self):
        self.base.mkdir()
        (self.base / SESSION_FILENAME).write_bytes(b"not-an-npz")
        result = load_session(self.base)
        self.assertEqual(result.status, "invalid")
        self.assertFalse(result.available)
        self.assertTrue(result.warning)

    def test_pickle_arrays_are_rejected(self):
        self.base.mkdir()
        np.savez_compressed(self.base / SESSION_FILENAME,
                            metadata=np.array([{"schema_version": 1}], dtype=object))
        self.assertEqual(load_session(self.base).status, "invalid")

    def test_nonfinite_depth_cannot_replace_a_valid_snapshot(self):
        save_session(self.base, self.snapshot)
        damaged = {**self.snapshot, "depth": np.full((5, 6), np.nan, np.float32)}
        with self.assertRaises(ValueError):
            save_session(self.base, damaged)
        np.testing.assert_array_equal(load_session(self.base).state["depth"], self.depth)

    def test_invalid_control_type_does_not_reach_qt(self):
        self.base.mkdir()
        metadata = {"schema_version": 1, "state": {"blur_slider": "bad"},
                    "source": None, "image_shape": None, "depth_keys": []}
        encoded = np.frombuffer(json.dumps(metadata).encode(), dtype=np.uint8)
        np.savez_compressed(self.base / SESSION_FILENAME, metadata=encoded)
        self.assertEqual(load_session(self.base).status, "invalid")

    def test_huge_array_header_is_rejected_before_allocation(self):
        self.base.mkdir()
        payload = io.BytesIO()
        np.lib.format.write_array_header_1_0(payload, {
            "shape": (10**9, 10**9), "fortran_order": False, "descr": "<f4"})
        metadata = np.frombuffer(b"{}", dtype=np.uint8)
        header = io.BytesIO()
        np.save(header, metadata, allow_pickle=False)
        with zipfile.ZipFile(self.base / SESSION_FILENAME, "w") as archive:
            archive.writestr("metadata.npy", header.getvalue())
            archive.writestr("depth.npy", payload.getvalue())
        self.assertEqual(load_session(self.base).status, "invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
