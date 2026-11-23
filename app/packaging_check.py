"""目录版离线运行自检。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def check_runtime(report_path):
    result = {"ok": False, "checks": [], "stage": "初始化"}
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import socket
        original_connect = socket.socket.connect
        def no_network(*args, **kwargs):
            raise AssertionError("离线自检不允许联网")
        socket.socket.connect = no_network
        try:
            import cv2
            import numpy as np
            from PIL import Image
            from depth_worker import DEFAULT_MODEL, resolve_model_path, _TransformersBackend
            from engine import BokehEngine, EngineSettings
            from theme import asset
            result["stage"] = "图像编解码与渲染"
            yy, xx = np.mgrid[:112, :168]
            image = np.stack([(xx * 1.5) % 255, (yy * 2) % 255, (xx + yy) % 255], axis=-1).astype(np.uint8)
            engine = BokehEngine()
            output = engine.process(image, None, EngineSettings())
            assert output.shape == image.shape and np.isfinite(output).all()
            for extension in (".png", ".jpg", ".bmp", ".tiff", ".webp"):
                ok, data = cv2.imencode(extension, output)
                assert ok and cv2.imdecode(data, cv2.IMREAD_COLOR).shape == image.shape
            result["checks"].append("image-codecs-and-render")
            result["stage"] = "界面资源"
            from PySide6.QtGui import QImageReader
            from PySide6.QtCore import QByteArray
            for path in asset().rglob("*.png"):
                reader = QImageReader(str(path))
                assert not reader.read().isNull(), str(path)
            for path in asset("fonts").glob("*.ttf"):
                assert path.stat().st_size > 100000
            result["checks"].append("ui-images-and-fonts")
            result["stage"] = "离线深度估算"
            model = resolve_model_path(DEFAULT_MODEL)
            assert model is not None, "默认模型未随包提供"
            backend = _TransformersBackend(str(model))
            backend._torch.set_num_threads(2)
            depth = backend.infer(Image.fromarray(image), 112, lambda text: None, lambda: False)
            assert depth.shape == image.shape[:2] and np.isfinite(depth).all()
            assert float(np.ptp(depth)) > 0
            result["checks"].append("offline-depth-anything-v2-small")
            result["depth_range"] = [float(depth.min()), float(depth.max())]
            result["stage"] = "深度模型辅助依赖"
            import torch
            from depth_anything_3.cfg import create_object, load_config
            from depth_anything_3.registry import MODEL_REGISTRY
            from depth_anything_3.utils.io.input_processor import InputProcessor
            from depth_anything_3.utils.io.output_processor import OutputProcessor
            assert load_config(MODEL_REGISTRY["da3-small"])
            assert load_config(MODEL_REGISTRY["da3-base"])
            result["checks"].append("da3-configuration-and-processors")
            result["torch"] = torch.__version__
            result["ok"] = True
            result["stage"] = "完成"
        finally:
            socket.socket.connect = original_connect
    except Exception:
        result["traceback"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def check_download(report_path):
    result = {"ok": False, "checks": []}
    try:
        import certifi
        import requests
        from depth_worker import _ENDPOINTS
        certificate = Path(certifi.where())
        result["certificate_exists"] = certificate.is_file()
        result["certificate"] = str(certificate)
        for endpoint in _ENDPOINTS:
            check = {"endpoint": endpoint}
            try:
                response = requests.get(endpoint + "/depth-anything/Depth-Anything-V2-Small-hf/resolve/main/config.json", timeout=(10, 20))
                response.raise_for_status()
                assert response.json()["model_type"] == "depth_anything"
                check["ok"] = True
                result["ok"] = True
            except Exception:
                check["ok"] = False
                check["traceback"] = traceback.format_exc()
            result["checks"].append(check)
    except Exception:
        result["traceback"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
