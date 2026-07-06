# executor.py v1.0
# 下单执行模块 - Paper / Live 切换
# 当前仅作为独立模块，暂不接入 runner

from datetime import datetime

class Executor:
    def __init__(self, config):
        self.trade_mode = config.get('trade_mode', 'paper')
        print(f"[{datetime.now()}] Executor 初始化完成 - 模式: {self.trade_mode}")

    def place_order(self, code, quantity, side="BUY"):
        """执行订单"""
        full_code = f"US.{code}" if not code.startswith("US.") else code

        if self.trade_mode == 'paper':
            print(f"[{datetime.now()}] [PAPER ORDER] {side} {quantity} shares of {code}")
            return True
        else:
            # Live 模式（后续实现）
            print(f"[{datetime.now()}] [LIVE ORDER] {side} {quantity} {full_code}")
            # 这里以后添加真实下单代码
            return True

    def close(self):
        print(f"[{datetime.now()}] Executor 已关闭")
