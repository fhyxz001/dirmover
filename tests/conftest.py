# 测试环境：把数据与日志写到临时目录，并关闭调度器
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="dirmover-test-")
os.environ.setdefault("DIRMOVER_DATA", os.path.join(_tmp, "data"))
os.environ.setdefault("DIRMOVER_LOG_DIR", os.path.join(_tmp, "logs"))
os.environ["DIRMOVER_SCHEDULER"] = "0"
