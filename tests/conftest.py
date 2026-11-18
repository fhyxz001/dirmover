# 测试环境：把数据与日志写到临时目录，并关闭调度器
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="dirmover-test-")
# 强制使用临时目录，避免已有环境变量把测试数据写进真实数据目录
os.environ["DIRMOVER_DATA"] = os.path.join(_tmp, "data")
os.environ["DIRMOVER_LOG_DIR"] = os.path.join(_tmp, "logs")
os.environ["DIRMOVER_SCHEDULER"] = "0"
