# env_util.py - 轻量 .env 加载（无第三方依赖）

import os
from typing import Optional


def load_dotenv(path: str = ".env") -> None:
    """将 .env 中的 KEY=VALUE 写入 os.environ（不覆盖已有环境变量）。"""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as e:
        print(f"[env] 加载 {path} 失败: {e}")


def env_first(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default
