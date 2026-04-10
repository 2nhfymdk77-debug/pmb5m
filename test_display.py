import time
from datetime import datetime

# 模拟单行刷新显示
for i in range(10):
    time_str = datetime.now().strftime("%H:%M:%S")
    yes = 50 + i % 10
    no = 50 - i % 10
    remaining = 300 - i
    status = "等待机会"
    line = f"[{time_str}] {status} | YES={yes}% NO={no}% | 剩余{remaining}s"
    print(f"\r{' '*60}\r{line}", end="", flush=True)
    time.sleep(1)

print("\n完成")
