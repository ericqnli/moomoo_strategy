# monitor.py v1.2
# Telegram + 文件日志（runner 日日志 / 因子 detail 频率可配）

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from env_util import env_first, load_dotenv


class Monitor:
    def __init__(self, config=None):
        config = config or {}
        load_dotenv(".env")

        self.telegram_bot_token = env_first(
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_TOKEN",
            default=str(config.get("telegram_bot_token") or ""),
        )
        self.telegram_chat_id = env_first(
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_CHAT",
            default=str(config.get("telegram_chat_id") or ""),
        )

        log_cfg = config.get("logging") if isinstance(config.get("logging"), dict) else {}
        self.log_dir = str(log_cfg.get("dir") or "logs")
        self.detail_mode = str(log_cfg.get("detail_mode") or "daily").lower()
        self.detail_interval_sec = int(log_cfg.get("detail_interval_sec") or 86400)
        self.always_log_events = bool(log_cfg.get("always_log_events", True))
        self.console_mode = str(log_cfg.get("console") or "signal").lower()
        # 是否在 detail 文件里包含 none（daily 时仍每票一天一条）
        self.include_none = bool(log_cfg.get("include_none", True))

        self._stamp_path = os.path.join(self.log_dir, ".detail_stamp.json")
        self._detail_stamp: Dict[str, Any] = self._load_stamp()

        os.makedirs(self.log_dir, exist_ok=True)

        if not (self.telegram_bot_token and self.telegram_chat_id):
            print(
                "[Monitor] 未配置 Telegram（设置 .env 中 TELEGRAM_BOT_TOKEN / "
                "TELEGRAM_CHAT_ID，或环境变量）。将仅打印日志。"
            )
        print(
            f"[Monitor] 日志 dir={self.log_dir} detail_mode={self.detail_mode} "
            f"console={self.console_mode} events={self.always_log_events}"
        )

    def _load_stamp(self) -> Dict[str, Any]:
        if not os.path.exists(self._stamp_path):
            return {}
        try:
            with open(self._stamp_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_stamp(self) -> None:
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            tmp = f"{self._stamp_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._detail_stamp, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._stamp_path)
        except OSError as e:
            print(f"[Monitor] 保存 detail stamp 失败: {e}")

    def _runner_path(self) -> str:
        day = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"runner_{day}.log")

    def _append_file(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

    def send_telegram(self, message: str) -> None:
        """发送 Telegram；未配置时降级为日志。"""
        if not (self.telegram_bot_token and self.telegram_chat_id):
            print(f"[LOG] {message}")
            self._append_file(self._runner_path(), f"[{datetime.now()}] [TG-FALLBACK] {message}")
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = {
                "chat_id": self.telegram_chat_id,
                "text": f"[{datetime.now()}] {message}",
            }
            requests.post(url, data=data, timeout=5)
            print(f"[Telegram] {message}")
            if self.always_log_events:
                self._append_file(self._runner_path(), f"[{datetime.now()}] [TG] {message}")
        except Exception as e:
            print(f"[Telegram 失败] {e}")

    def log(self, message: str, *, console: Optional[bool] = None) -> None:
        """
        普通日志：默认写 runner 日文件。
        console: None 时按 console_mode（signal/detail 打印，quiet 不打印）。
        """
        line = f"[{datetime.now()}] {message}"
        self._append_file(self._runner_path(), line)
        if console is None:
            console = self.console_mode in ("signal", "detail", "all")
        if console:
            print(line)

    def log_event(self, message: str, *, console: bool = True) -> None:
        """成交 / 风控 / 失败等事件：始终写文件（若 always_log_events）。"""
        line = f"[{datetime.now()}] [EVENT] {message}"
        if self.always_log_events:
            self._append_file(self._runner_path(), line)
        if console and self.console_mode != "quiet":
            print(line)

    def should_write_detail(self, code: str, *, force: bool = False) -> bool:
        if force:
            return True
        mode = self.detail_mode
        if mode in ("off", "none", "false", "0"):
            return False
        if mode == "every_cycle":
            return True

        now = datetime.now()
        if mode == "interval":
            last_ts = self._detail_stamp.get(code)
            if last_ts is None:
                return True
            try:
                last = float(last_ts)
            except (TypeError, ValueError):
                return True
            return (now.timestamp() - last) >= self.detail_interval_sec

        # daily（默认）：每个自然日每票一条
        day = now.strftime("%Y-%m-%d")
        last_day = self._detail_stamp.get(code)
        return last_day != day

    def mark_detail_written(self, code: str) -> None:
        mode = self.detail_mode
        now = datetime.now()
        if mode == "interval":
            self._detail_stamp[code] = now.timestamp()
        else:
            # daily / every_cycle 也记日期，every_cycle 不靠 stamp 限频
            self._detail_stamp[code] = now.strftime("%Y-%m-%d")
        if mode != "every_cycle":
            self._save_stamp()

    def log_detail(
        self,
        code: str,
        snapshot: str,
        *,
        force: bool = False,
        is_none_signal: bool = False,
    ) -> bool:
        """
        写入因子详细快照。受 detail_mode 限频。
        返回是否实际写入。
        """
        if is_none_signal and not self.include_none and not force:
            return False
        if not self.should_write_detail(code, force=force):
            return False

        block = f"[{datetime.now()}] [DETAIL] {snapshot}"
        self._append_file(self._runner_path(), block)
        if self.console_mode in ("detail", "all"):
            print(block)
        self.mark_detail_written(code)
        return True

    @staticmethod
    def format_factor_snapshot(
        code: str,
        price: float,
        held: int,
        entry: float,
        cash: float,
        factors: Dict[str, Any],
        *,
        ma200: Optional[float] = None,
        ma_gate: Optional[bool] = None,
        ma_msg: str = "",
        atr: Optional[float] = None,
        stop_entry: Optional[float] = None,
        trail_stop: Optional[float] = None,
        tp_px: Optional[float] = None,
        atr_trail: str = "none",
        unreal_pct: Optional[float] = None,
        action: str = "hold",
        reason: str = "",
    ) -> str:
        """拼可读多行 DETAIL 文本。"""
        def b(v: Any) -> str:
            return "1" if v else "0"

        def fnum(v: Optional[float], nd: int = 2) -> str:
            if v is None:
                return "—"
            try:
                return f"{float(v):.{nd}f}"
            except (TypeError, ValueError):
                return "—"

        gold = factors.get("gold")
        dead = factors.get("dead")
        if gold:
            cross = "gold"
        elif dead:
            cross = "dead"
        else:
            cross = "none"

        lines = [
            (
                f"{code} px={price:.4f} held={held} entry={fnum(entry)} "
                f"cash={cash:.2f} action={action}"
            ),
            (
                f"  cross={cross} div_b={b(factors.get('div_b'))} "
                f"div_t={b(factors.get('div_t'))} "
                f"rsi={fnum(factors.get('rsi'))} "
                f"rsi_buy_ok={b(factors.get('rsi_buy_ok'))} "
                f"rsi_sell_ok={b(factors.get('rsi_sell_ok'))} "
                f"vol={fnum(factors.get('vol_ratio'), 3)} "
                f"vol_ok={b(factors.get('vol_ok'))}"
            ),
            (
                f"  ma20={fnum(factors.get('ma20'), 4)} ma200={fnum(ma200, 4)} "
                f"trend_up={b(factors.get('trend_up'))} "
                f"trend_down={b(factors.get('trend_down'))} "
                f"ma_gate={b(ma_gate) if ma_gate is not None else '—'} "
                f"{('(' + ma_msg + ')') if ma_msg else ''}"
            ).rstrip(),
            (
                f"  atr={fnum(atr, 4)} stop_entry={fnum(stop_entry)} "
                f"trail_stop={fnum(trail_stop)} tp={fnum(tp_px)} "
                f"atr_trail={atr_trail} unreal_pct={fnum(unreal_pct, 2)}"
            ),
            (
                f"  bL={factors.get('buy_level')} sL={factors.get('sell_level')} "
                f"signal={factors.get('signal')} hits={factors.get('hits')} "
                f"dif={fnum(factors.get('dif'), 5)} dea={fnum(factors.get('dea'), 5)}"
            ),
        ]
        if reason:
            lines.append(f"  reason={reason}")
        return "\n".join(lines)
