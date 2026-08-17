import sys
from pathlib import Path

# 未安装(pip install -e .)时也能直接跑 pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
