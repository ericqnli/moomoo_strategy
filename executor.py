# executor.py v1.2
# 下单执行：paper 模拟 / live（moomoo OpenSecTradeContext）
#
# 安全层：
#   1) config trade_mode 默认 paper
#   2) live.trd_env 默认 SIMULATE（官方模拟盘）
#   3) live.allow_real 默认 false：REAL 真金须显式打开
#   4) REAL 需 unlock 密码（.env TRADE_UNLOCK_PWD 或 password_md5）

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from env_util import env_first, load_dotenv


def _resolve_enum(enum_cls, name: str, default=None):
    key = str(name or "").strip().upper()
    if not key:
        return default
    if hasattr(enum_cls, key):
        return getattr(enum_cls, key)
    for attr in dir(enum_cls):
        if attr.upper() == key and not attr.startswith("_"):
            return getattr(enum_cls, attr)
    return default


def to_trade_code(code: str, default_market: str = "US") -> str:
    """策略裸代码 -> US.SPY；已是 US.xx 则规范为大写。"""
    s = str(code).strip()
    if not s:
        return s
    upper = s.upper()
    if upper.startswith("US.."):
        return upper
    if "." in upper and upper.split(".", 1)[0] in {
        "US", "HK", "SH", "SZ", "SG", "JP", "AU", "CA",
    }:
        return upper
    mkt = (default_market or "US").upper()
    # BRK.B 保持
    bare = upper[3:] if upper.startswith("US.") else s.upper()
    return f"{mkt}.{bare}"


class Executor:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        load_dotenv(".env")
        self.config = config or {}
        self.trade_mode = (self.config.get("trade_mode") or "paper").lower().strip()

        live = self.config.get("live") if isinstance(self.config.get("live"), dict) else {}
        self.live_cfg = live
        self.host = str(live.get("host") or "127.0.0.1")
        self.port = int(live.get("port") or 11111)
        self.trd_env_name = str(live.get("trd_env") or "SIMULATE").upper()
        self.trd_market_name = str(live.get("trd_market") or "US").upper()
        self.acc_id = int(live.get("acc_id") or 0)
        self.acc_index = int(live.get("acc_index") or 0)
        self.allow_real = bool(live.get("allow_real", False))
        self.order_type_name = str(live.get("order_type") or "NORMAL").upper()
        self.fill_outside_rth = bool(live.get("fill_outside_rth", False))
        self.remark = str(live.get("remark") or "qa-strategy")[:64]
        self.default_market = str(live.get("default_market") or "US")
        self.unlock_password = env_first(
            "TRADE_UNLOCK_PWD",
            "MOOMOO_TRADE_PWD",
            default=str(live.get("unlock_password") or ""),
        )
        self.unlock_password_md5 = env_first(
            "TRADE_UNLOCK_PWD_MD5",
            default=str(live.get("unlock_password_md5") or ""),
        )

        self._trade_ctx = None
        self._unlocked = False

        print(
            f"[{datetime.now()}] Executor 初始化 - mode={self.trade_mode} "
            f"trd_env={self.trd_env_name} allow_real={self.allow_real}"
        )

        if self.trade_mode == "live":
            ok, msg = self._init_live()
            if not ok:
                print(f"[{datetime.now()}] [Executor] live 初始化失败: {msg}")
                # 不在此 raise：place_order 会继续失败并返回 ok=False
                self._live_init_error = msg
            else:
                self._live_init_error = None
        else:
            self._live_init_error = None

    def _init_live(self) -> tuple:
        try:
            from moomoo import OpenSecTradeContext, RET_OK, TrdEnv, TrdMarket
        except ImportError as e:
            return False, f"无法 import moomoo: {e}"

        if self.trd_env_name == "REAL" and not self.allow_real:
            return (
                False,
                "trd_env=REAL 但 live.allow_real=false；"
                "真金交易请在 config 显式设置 live.allow_real: true",
            )

        trd_market = _resolve_enum(TrdMarket, self.trd_market_name, TrdMarket.US)
        trd_env = _resolve_enum(TrdEnv, self.trd_env_name, TrdEnv.SIMULATE)

        try:
            self._trade_ctx = OpenSecTradeContext(
                filter_trdmarket=trd_market,
                host=self.host,
                port=self.port,
            )
        except Exception as e:
            return False, f"OpenSecTradeContext 失败: {e}"

        # REAL 需要解锁；SIMULATE 一般不需要
        if trd_env == TrdEnv.REAL or self.trd_env_name == "REAL":
            pwd = self.unlock_password or None
            pwd_md5 = self.unlock_password_md5 or None
            if not pwd and not pwd_md5:
                return False, "REAL 环境需要 TRADE_UNLOCK_PWD 或 TRADE_UNLOCK_PWD_MD5"
            ret, data = self._trade_ctx.unlock_trade(
                password=pwd, password_md5=pwd_md5, is_unlock=True
            )
            if ret != RET_OK:
                return False, f"unlock_trade 失败: {data}"
            self._unlocked = True
            print(f"[{datetime.now()}] [Executor] 交易已解锁 (REAL)")
        else:
            self._unlocked = True
            print(f"[{datetime.now()}] [Executor] SIMULATE 模式，跳过 unlock")

        print(
            f"[{datetime.now()}] [Executor] live 就绪 "
            f"{self.host}:{self.port} market={self.trd_market_name} env={self.trd_env_name}"
        )
        return True, "ok"

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
            order_id: optional,
            raw: optional,
          }
        """
        side = side.upper()
        full_code = to_trade_code(code, self.default_market)

        base = {
            "mode": self.trade_mode,
            "code": code,
            "side": side,
            "quantity": quantity,
            "price": price,
        }

        if quantity <= 0:
            return {**base, "ok": False, "message": "数量无效"}

        if self.trade_mode == "paper":
            fill_price = float(price) if price is not None else None
            msg = (
                f"[PAPER ORDER] {side} {quantity} {code}"
                + (f" @ {fill_price:.4f}" if fill_price is not None else "")
            )
            print(f"[{datetime.now()}] {msg}")
            return {
                **base,
                "ok": True,
                "mode": "paper",
                "price": fill_price,
                "message": msg,
            }

        if self.trade_mode != "live":
            return {
                **base,
                "ok": False,
                "message": f"未知 trade_mode={self.trade_mode!r}，请用 paper 或 live",
            }

        return self._place_live(full_code, code, quantity, side, price, base)

    def _place_live(
        self,
        full_code: str,
        code: str,
        quantity: int,
        side: str,
        price: Optional[float],
        base: Dict[str, Any],
    ) -> Dict[str, Any]:
        from moomoo import OrderType, RET_OK, TrdEnv, TrdSide

        if self._live_init_error:
            return {
                **base,
                "ok": False,
                "message": f"live 未就绪: {self._live_init_error}",
            }
        if self._trade_ctx is None:
            return {**base, "ok": False, "message": "trade_ctx 为空"}

        if self.trd_env_name == "REAL" and not self.allow_real:
            return {
                **base,
                "ok": False,
                "message": "REAL 被 allow_real=false 拦截",
            }

        if price is None or float(price) <= 0:
            return {
                **base,
                "ok": False,
                "message": "live 限价单需要有效 price",
            }

        trd_env = _resolve_enum(TrdEnv, self.trd_env_name, TrdEnv.SIMULATE)
        trd_side = TrdSide.BUY if side == "BUY" else TrdSide.SELL
        order_type = _resolve_enum(OrderType, self.order_type_name, OrderType.NORMAL)

        try:
            ret, data = self._trade_ctx.place_order(
                price=float(price),
                qty=int(quantity),
                code=full_code,
                trd_side=trd_side,
                order_type=order_type,
                trd_env=trd_env,
                acc_id=self.acc_id,
                acc_index=self.acc_index,
                remark=self.remark,
                fill_outside_rth=self.fill_outside_rth,
            )
        except Exception as e:
            msg = f"[LIVE ORDER ERROR] {side} {quantity} {full_code}: {e}"
            print(f"[{datetime.now()}] {msg}")
            return {**base, "ok": False, "message": msg}

        if ret != RET_OK:
            msg = f"[LIVE ORDER FAIL] {side} {quantity} {full_code}: {data}"
            print(f"[{datetime.now()}] {msg}")
            return {**base, "ok": False, "message": str(msg), "raw": data}

        # data 一般为 DataFrame 一行订单
        order_id = None
        fill_price = float(price)
        dealt_qty = 0
        status = ""
        try:
            import pandas as pd

            if isinstance(data, pd.DataFrame) and len(data) > 0:
                row = data.iloc[0]
                order_id = row.get("order_id")
                status = str(row.get("order_status", "") or "")
                dealt_qty = int(float(row.get("dealt_qty") or 0))
                dap = row.get("dealt_avg_price")
                if dap is not None and float(dap) > 0:
                    fill_price = float(dap)
                elif row.get("price") is not None:
                    # 未成交时用委托价；账本会按此价记（限价已报）
                    fill_price = float(row.get("price") or price)
        except Exception:
            pass

        msg = (
            f"[LIVE ORDER] {side} {quantity} {full_code} @ {fill_price:.4f} "
            f"env={self.trd_env_name} order_id={order_id} status={status} "
            f"dealt_qty={dealt_qty}"
        )
        print(f"[{datetime.now()}] {msg}")

        # 已成功提交订单即 ok=True；若完全未成交，仍用委托价更新本地账本
        # （与「报单成功」语义一致；深度成交确认可后续增强）
        return {
            **base,
            "ok": True,
            "mode": "live",
            "price": fill_price,
            "message": msg,
            "order_id": order_id,
            "order_status": status,
            "dealt_qty": dealt_qty,
            "raw": data,
        }

    def close(self):
        if self._trade_ctx is not None:
            try:
                self._trade_ctx.close()
            except Exception as e:
                print(f"[{datetime.now()}] [Executor] 关闭 trade_ctx 失败: {e}")
            self._trade_ctx = None
        print(f"[{datetime.now()}] Executor 已关闭")
