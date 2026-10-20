"""版本号单一来源 —— 发版时只改这一处。

约定：语义化版本 ``MAJOR.MINOR.PATCH``；发布时打同名 git 标签 ``v1.1.0``。
界面标题与本文件构建脚本的 Release 附件命名都会用到它。
"""

from __future__ import annotations

__version__ = "1.1.1"

# 方便 ``from version import VERSION_TAG`` 直接拼标签
VERSION_TAG = f"v{__version__}"
