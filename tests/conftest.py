import importlib
import sys

import cv2
import numpy as np
import pytest


def make_part(width=220, height=140):
    """Synthetic photo: dark rectangle on a light background."""
    img = np.full((480, 640, 3), 235, np.uint8)
    x0, y0 = 320 - width // 2, 240 - height // 2
    cv2.rectangle(img, (x0, y0), (x0 + width, y0 + height), (40, 40, 40), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh hub (empty database + image folder) per test."""
    monkeypatch.setenv("VISIONQC_DATA_DIR", str(tmp_path))
    for mod in ("app", "database", "config"):
        sys.modules.pop(mod, None)
    from fastapi.testclient import TestClient
    app = importlib.import_module("app")
    return TestClient(app.app)
