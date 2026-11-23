import os
from pathlib import Path
import sys

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    root = Path(sys._MEIPASS)
    sys._bokeh_dll_directories = [
        os.add_dll_directory(str(root / folder))
        for folder in ("PySide6", "shiboken6", "torch/lib", "numpy.libs", "pandas.libs")
        if (root / folder).is_dir()
    ]
