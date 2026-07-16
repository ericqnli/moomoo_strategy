# executor.py v1.1
# 下单执行模块 - Paper / Live 切换（已接入 runner）

from datetime import datetime
from typing import Any, Dict, Optional


class Executor:
    def __init__(self, config):
        self.trade_mode = (config.get("trade_mode") or "paper").lower()
        print(f"[{datetime.now()}] Executor 初始化完成 - 模式: {self.trade_mode}")

    def place_order(
        self,
        code: str,
        quantity: int,
        side: str = "BUY",
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        执行订单。

        返回:
          {
            ok: bool,
            mode: str,
            code, side, quantity, price,
            message: str,
          }
        """
        side = side.upper()
        full_code = f"US.{code}" if not code.startswith("US.") else code

        if quantity <= 0:
            return {
                "ok": False,
                "mode": self.trade_mode,
                "code": code,
                "side": side,
                "quantity": quantity,
                "price": price,
                "message": "数量无效",
            }

        if self.trade_mode == "paper":
            fill_price = float(price) if price is not None else None
            msg = (
                f"[PAPER ORDER] {side} {quantity} {code}"
                + (f" @ {fill_price:.4f}" if fill_price is not None else "")
            )
            print(f"[{datetime.now()}] {msg}")
            return {
                "ok": True,
                "mode": "paper",
                "code": code,
                "side": side,
                "quantity": quantity,
                "price": fill_price,
                "message": msg,
            }

        # Live：安全起见默认拒单，避免假成交导致账本漂移
        msg = (
            f"[LIVE ORDER REJECTED] {side} {quantity} {full_code} "
            f"- live 实盘下单尚未实现，请保持 trade_mode: paper"
        )
        print(f"[{datetime.now()}] {msg}")
        return {
            "ok": False,
            "mode": "live",
            "code": code,
            "side": side,
            "quantity": quantity,
            "price": price,
            "message": msg,
        }

    def close(self):
        print(f"[{datetime.now()}] Executor 已关闭")
