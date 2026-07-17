# position_store.py
# 持仓与资金落盘，重启后可恢复

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional


class PositionStore:
    """持久化 cash + 各标的持仓（股数、入场价、时间、部分止盈标记）。"""

    def __init__(
        self,
        path: str,
        symbols: list,
        initial_cash: float = 100_000.0,
    ):
        self.path = path
        self.symbols = list(symbols)
        self.initial_cash = float(initial_cash)
        self.cash = self.initial_cash
        # code -> {shares, entry_price, entry_time, partial_tp_done}
        self.positions: Dict[str, Dict[str, Any]] = {
            code: self._empty_pos() for code in self.symbols
        }
        self.load()

    @staticmethod
    def _empty_pos() -> Dict[str, Any]:
        return {
            "shares": 0,
            "entry_price": 0.0,
            "entry_time": None,
            "partial_tp_done": False,
        }

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.save()
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[PositionStore] 读取失败，使用初始状态: {e}")
            self.save()
            return

        self.cash = float(data.get("cash", self.initial_cash))
        loaded = data.get("positions") or {}

        # 兼容旧格式: {"SPY": 50}
        legacy_flat = bool(loaded) and all(not isinstance(v, dict) for v in loaded.values())

        # 并入：初始 symbols + 文件中有记录的代码（动态股票池切换后仍能恢复持仓）
        codes = list(self.symbols)
        for code in loaded.keys():
            if code not in codes:
                codes.append(code)

        for code in codes:
            if legacy_flat:
                self.positions[code] = {
                    "shares": int(loaded.get(code) or 0),
                    "entry_price": 0.0,
                    "entry_time": None,
                    "partial_tp_done": False,
                }
                continue
            raw = loaded.get(code) or {}
            if not isinstance(raw, dict):
                raw = {}
            self.positions[code] = {
                "shares": int(raw.get("shares", 0) or 0),
                "entry_price": float(raw.get("entry_price", 0) or 0),
                "entry_time": raw.get("entry_time"),
                "partial_tp_done": bool(raw.get("partial_tp_done", False)),
            }
        self.symbols = list(dict.fromkeys(list(self.symbols) + list(self.positions.keys())))

    def save(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "cash": round(self.cash, 4),
            "positions": self.positions,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def ensure_symbol(self, code: str) -> None:
        """动态股票池：确保 code 在账本中有条目。"""
        if not code:
            return
        if code not in self.positions:
            self.positions[code] = self._empty_pos()
        if code not in self.symbols:
            self.symbols.append(code)

    def held_codes(self) -> list:
        """当前有持仓的代码列表。"""
        return [c for c, p in self.positions.items() if int(p.get("shares") or 0) > 0]

    def shares(self, code: str) -> int:
        return int(self.positions.get(code, self._empty_pos())["shares"])

    def entry_price(self, code: str) -> float:
        return float(self.positions.get(code, self._empty_pos())["entry_price"] or 0)

    def partial_tp_done(self, code: str) -> bool:
        return bool(self.positions.get(code, self._empty_pos()).get("partial_tp_done"))

    def get_position(self, code: str) -> Dict[str, Any]:
        return dict(self.positions.get(code, self._empty_pos()))

    def open_long(self, code: str, shares: int, price: float) -> bool:
        """开多：扣减现金并记录入场。"""
        if shares <= 0 or price <= 0:
            return False
        cost = shares * price
        if cost > self.cash + 1e-9:
            return False
        self.ensure_symbol(code)
        self.cash -= cost
        self.positions[code] = {
            "shares": shares,
            "entry_price": float(price),
            "entry_time": datetime.now().isoformat(timespec="seconds"),
            "partial_tp_done": False,
        }
        self.save()
        return True

    def reduce_long(
        self,
        code: str,
        shares: int,
        price: float,
        mark_partial_tp: bool = False,
    ) -> bool:
        """减仓或平仓：回收现金。"""
        self.ensure_symbol(code)
        pos = self.positions.get(code, self._empty_pos())
        held = int(pos["shares"])
        if shares <= 0 or price <= 0 or held <= 0:
            return False
        qty = min(shares, held)
        self.cash += qty * price
        remain = held - qty
        if remain <= 0:
            self.positions[code] = self._empty_pos()
        else:
            self.positions[code] = {
                "shares": remain,
                "entry_price": float(pos["entry_price"]),
                "entry_time": pos.get("entry_time"),
                "partial_tp_done": True if mark_partial_tp else bool(pos.get("partial_tp_done")),
            }
        self.save()
        return True

    def close_long(self, code: str, price: float) -> bool:
        return self.reduce_long(code, self.shares(code), price, mark_partial_tp=False)

    def summary_line(self) -> str:
        parts = [f"现金:{self.cash:.2f}"]
        # 有持仓的优先；其余按 symbols 顺序
        seen = set()
        ordered = list(self.held_codes()) + [c for c in self.symbols if c not in self.held_codes()]
        for code in ordered:
            if code in seen:
                continue
            seen.add(code)
            s = self.shares(code)
            if s > 0:
                ep = self.entry_price(code)
                parts.append(f"{code}:{s}@{ep:.2f}")
        return " | ".join(parts)
