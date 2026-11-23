"""Offline checks for model download resumption and V2 inference device selection."""

from contextlib import ExitStack, nullcontext
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from depth_worker import _TransformersBackend, _fetch_file


class DeviceBatch(dict):
    """Model the processor's CPU batch and its BatchFeature.to(device) API."""

    def to(self, device):
        return DeviceBatch(pixel_values=device)


class DepthDeviceCheck(unittest.TestCase):
    def test_inputs_follow_model_device(self):
        for device in ("cpu", "cuda:0"):
            for size, fallback in ((0, False), (504, False), (504, True)):
                with self.subTest(device=device, size=size, fallback=fallback):
                    batch = DeviceBatch(pixel_values="cpu")
                    backend = _TransformersBackend.__new__(_TransformersBackend)
                    backend.model = Mock(device=device)
                    backend.processor = Mock(side_effect=[ValueError(), batch] if fallback else [batch])
                    torch = Mock()
                    torch.no_grad = nullcontext
                    notice = Mock()
                    with patch.dict(sys.modules, torch=torch):
                        backend.infer(Mock(size=(100, 50)), size, notice, lambda: False)
                    backend.model.assert_called_once_with(pixel_values=device)
                    self.assertEqual(notice.call_count, int(fallback))
                    self.assertEqual(backend.processor.call_count, 1 + int(fallback))


def response(status, headers, body=b""):
    result = MagicMock(status_code=status, headers=headers)
    result.__enter__.return_value = result
    result.iter_content.return_value = (body,)
    return result


class ModelDownloadCheck(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.target = Path(folder.name) / "model.safetensors"
        self.target.write_bytes(b"previous complete model")
        self.part = self.target.with_suffix(".safetensors.part")
        self.requests = Mock()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules, requests=self.requests))
        stack.enter_context(patch("depth_worker.time.sleep"))

    def fetch(self, responses):
        self.requests.get.side_effect = responses
        return _fetch_file("offline/model", self.target.name, self.target, None, lambda: False)

    def ranges(self):
        return [call.kwargs["headers"].get("Range") for call in self.requests.get.call_args_list]

    def test_complete_partial_is_promoted_on_confirmed_length(self):
        self.part.write_bytes(b"abc")
        self.assertTrue(self.fetch([response(416, {"Content-Range": "bytes */3"})]))
        self.assertEqual(self.target.read_bytes(), b"abc")
        self.assertEqual(self.ranges(), ["bytes=3-"])

    def test_invalid_range_restarts_without_appending(self):
        for status, header in ((416, "bytes */2"), (416, ""), (206, "bytes 2-3/4"), (206, "")):
            with self.subTest(status=status, header=header):
                self.requests.reset_mock()
                self.part.write_bytes(b"old")
                self.assertTrue(self.fetch([response(status, {"Content-Range": header}, b"cd"),
                                            response(200, {"Content-Length": "4"}, b"abcd")]))
                self.assertEqual(self.target.read_bytes(), b"abcd")
                self.assertEqual(self.ranges(), ["bytes=3-", None])

    def test_short_response_continues_at_received_offset(self):
        self.part.write_bytes(b"ab")
        self.assertTrue(self.fetch([response(206, {"Content-Range": "bytes 2-4/5"}, b"c"),
                                    response(206, {"Content-Range": "bytes 3-4/5"}, b"de")]))
        self.assertEqual(self.target.read_bytes(), b"abcde")
        self.assertEqual(self.ranges(), ["bytes=2-", "bytes=3-"])

    def test_oversize_response_leaves_existing_file_untouched(self):
        self.part.write_bytes(b"ab")
        with self.assertRaises(RuntimeError):
            self.fetch([response(206, {"Content-Range": "bytes 2-3/4"}, b"cde") for _ in range(6)])
        self.assertEqual(self.target.read_bytes(), b"previous complete model")
        self.assertEqual(self.part.read_bytes(), b"ab")


if __name__ == "__main__":
    unittest.main(verbosity=2)
