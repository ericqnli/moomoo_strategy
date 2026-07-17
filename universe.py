# universe.py
# 股票来源：fixed | dynamic | watchlist | account

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from moomoo import (
    FinancialFilter,
    FinancialQuarter,
    Market,
    OpenSecTradeContext,
    RET_OK,
    SimpleFilter,
    SortDir,
    StockField,
    TrdEnv,
    TrdMarket,
    UserSecurityGroupType,
)


# 策略内部统一用裸代码（如 SPY）；行情接口用 US.SPY
_MARKET_PREFIXES = ("US.", "HK.", "SH.", "SZ.", "SG.", "JP.", "AU.", "CA.")


def normalize_code(code: Any) -> str:
    """US.SPY / SPY -> SPY；保留 BRK.B 这类中间点号。"""
    if code is None:
        return ""
    s = str(code).strip().upper()
    if not s:
        return ""
    for p in _MARKET_PREFIXES:
        if s.startswith(p):
            return s[len(p) :]
    return s


def to_full_code(code: str, default_market: str = "US") -> str:
    s = str(code).strip()
    if not s:
        return s
    upper = s.upper()
    # 已是 MARKET.CODE
    if "." in upper and upper.split(".", 1)[0] in {
        "US",
        "HK",
        "SH",
        "SZ",
        "SG",
        "JP",
        "AU",
        "CA",
    }:
        return upper
    mkt = (default_market or "US").upper()
    return f"{mkt}.{normalize_code(s)}"


def _resolve_enum(enum_cls, name: str, default=None):
    key = str(name or "").strip().upper()
    if not key:
        return default
    if hasattr(enum_cls, key):
        return getattr(enum_cls, key)
    # 兼容小写/混合
    for attr in dir(enum_cls):
        if attr.upper() == key and not attr.startswith("_"):
            return getattr(enum_cls, attr)
    return default


class UniverseProvider:
    """
    按 config['universe'] 解析股票列表。

    modes:
      - fixed:     固定列表（universe.symbols 或顶层 symbols）
      - dynamic:   moomoo 条件选股 get_stock_filter
                   **仅做基本面/行业/估值粗筛，不包含技术信号**
                   （市值、PE、行业 plate、价格流动性等）
      - watchlist: 账号自选股分组 get_user_security
      - account:   账号持仓 position_list_query
    """

    VALID_MODES = ("fixed", "dynamic", "watchlist", "account")

    def __init__(self, config: Dict[str, Any], quote_ctx, trade_ctx=None):
        self.config = config or {}
        self.quote_ctx = quote_ctx
        self.trade_ctx = trade_ctx
        self._owns_trade_ctx = False

        u = self._universe_cfg()
        self.mode = str(u.get("mode") or self.config.get("universe_mode") or "fixed").lower()
        if self.mode not in self.VALID_MODES:
            print(f"[Universe] 未知 mode={self.mode!r}，回退 fixed")
            self.mode = "fixed"

        self.refresh_sec = int(u.get("refresh_sec", 300) or 0)
        self.max_symbols = int(u.get("max_symbols", 50) or 50)
        self.include_held = bool(u.get("include_held", True))
        self.only_stocks = bool(u.get("only_stocks", True))
        self.default_market = str(u.get("default_market") or "US").upper()

        self._cached: List[str] = []
        self._last_fetch_ts: float = 0.0
        self._last_error: Optional[str] = None

        if self.mode == "account" and self.trade_ctx is None:
            self.trade_ctx = self._open_trade_ctx(u.get("account") or {})
            self._owns_trade_ctx = self.trade_ctx is not None

    def _universe_cfg(self) -> Dict[str, Any]:
        u = self.config.get("universe")
        if isinstance(u, dict):
            return u
        return {}

    def _open_trade_ctx(self, acc_cfg: Dict[str, Any]):
        host = str(acc_cfg.get("host") or "127.0.0.1")
        port = int(acc_cfg.get("port") or 11111)
        mkt_name = str(acc_cfg.get("trd_market") or "US").upper()
        trd_market = _resolve_enum(TrdMarket, mkt_name, TrdMarket.US)
        try:
            ctx = OpenSecTradeContext(
                filter_trdmarket=trd_market,
                host=host,
                port=port,
            )
            print(f"[Universe] OpenSecTradeContext 已连接 {host}:{port} market={mkt_name}")
            return ctx
        except Exception as e:
            print(f"[Universe] 打开交易上下文失败: {e}")
            return None

    # ---------- public ----------

    def get_symbols(self, held_codes: Optional[Sequence[str]] = None, force: bool = False) -> List[str]:
        """
        返回本轮应扫描的裸代码列表（去重、保序）。
        held_codes: 本地账本已有持仓，include_held 时并入。
        """
        need_refresh = (
            force
            or not self._cached
            or (self.mode != "fixed" and self.refresh_sec > 0 and (time.time() - self._last_fetch_ts) >= self.refresh_sec)
            or (self.mode != "fixed" and self.refresh_sec <= 0)
        )

        if need_refresh:
            try:
                fetched = self._fetch()
                self._last_error = None
            except Exception as e:
                self._last_error = str(e)
                print(f"[Universe] 拉取失败 mode={self.mode}: {e}")
                fetched = list(self._cached) if self._cached else self._fallback_fixed()

            fetched = self._post_process(fetched)
            if fetched:
                self._cached = fetched
                self._last_fetch_ts = time.time()
            elif not self._cached:
                self._cached = self._fallback_fixed()
                self._last_fetch_ts = time.time()

        result = list(self._cached)
        if self.include_held and held_codes:
            for c in held_codes:
                n = normalize_code(c)
                if n and n not in result:
                    result.append(n)
        return result

    def list_watchlist_groups(self) -> List[str]:
        """调试用：列出自选股分组名。"""
        ret, data = self.quote_ctx.get_user_security_group(UserSecurityGroupType.ALL)
        if ret != RET_OK:
            print(f"[Universe] get_user_security_group 失败: {data}")
            return []
        if data is None or len(data) == 0:
            return []
        return [str(x) for x in data["group_name"].tolist()]

    def summary(self) -> str:
        err = f" err={self._last_error}" if self._last_error else ""
        return f"mode={self.mode} n={len(self._cached)} refresh={self.refresh_sec}s{err}"

    def close(self):
        if self._owns_trade_ctx and self.trade_ctx is not None:
            try:
                self.trade_ctx.close()
            except Exception as e:
                print(f"[Universe] 关闭 trade_ctx 失败: {e}")
            finally:
                self.trade_ctx = None
                self._owns_trade_ctx = False

    # ---------- fetchers ----------

    def _fetch(self) -> List[str]:
        if self.mode == "fixed":
            return self._fetch_fixed()
        if self.mode == "watchlist":
            return self._fetch_watchlist()
        if self.mode == "account":
            return self._fetch_account()
        if self.mode == "dynamic":
            return self._fetch_dynamic()
        return self._fetch_fixed()

    def _fallback_fixed(self) -> List[str]:
        codes = self._fetch_fixed()
        if codes:
            print(f"[Universe] 使用 fixed 兜底: {codes}")
        else:
            print("[Universe] 警告: 股票列表为空")
        return codes

    def _fetch_fixed(self) -> List[str]:
        u = self._universe_cfg()
        raw = u.get("symbols")
        if raw is None:
            raw = self.config.get("symbols") or []
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        return [normalize_code(c) for c in raw if normalize_code(c)]

    def _fetch_watchlist(self) -> List[str]:
        u = self._universe_cfg()
        group = str(u.get("watchlist_group") or "全部")
        ret, data = self.quote_ctx.get_user_security(group)
        if ret != RET_OK:
            raise RuntimeError(f"get_user_security({group!r}) 失败: {data}")
        if data is None or len(data) == 0:
            print(f"[Universe] 自选分组 {group!r} 为空")
            return []

        codes: List[str] = []
        for _, row in data.iterrows():
            if self.only_stocks:
                st = str(row.get("stock_type", "") or "").upper()
                # STOCK / ETF 等；跳过期权等
                if st and st not in ("STOCK", "ETF", "IDX", "INDEX", "BOND", "WRNT", ""):
                    # 空串放行；明确非股票类型再跳
                    if "OPTION" in st or st in ("DRVT", "FUTURE", "FUT"):
                        continue
            code = normalize_code(row.get("code"))
            if code:
                codes.append(code)
        print(f"[{datetime.now()}] [Universe] watchlist group={group!r} -> {len(codes)} 只")
        return codes

    def _fetch_account(self) -> List[str]:
        if self.trade_ctx is None:
            raise RuntimeError("account 模式需要交易上下文（OpenD 已登录）")

        u = self._universe_cfg()
        acc = u.get("account") or {}
        env_name = str(acc.get("trd_env") or "SIMULATE").upper()
        trd_env = _resolve_enum(TrdEnv, env_name, TrdEnv.SIMULATE)
        acc_id = int(acc.get("acc_id") or 0)
        acc_index = int(acc.get("acc_index") or 0)
        pos_mkt = _resolve_enum(TrdMarket, str(acc.get("position_market") or "NONE").upper(), TrdMarket.NONE)

        ret, data = self.trade_ctx.position_list_query(
            trd_env=trd_env,
            acc_id=acc_id,
            acc_index=acc_index,
            position_market=pos_mkt,
            refresh_cache=True,
        )
        if ret != RET_OK:
            raise RuntimeError(f"position_list_query 失败: {data}")
        if data is None or len(data) == 0:
            print("[Universe] 账号持仓为空")
            return []

        codes: List[str] = []
        for _, row in data.iterrows():
            qty = float(row.get("qty") or 0)
            if qty == 0:
                continue
            code = normalize_code(row.get("code"))
            if code:
                codes.append(code)
        print(f"[{datetime.now()}] [Universe] account positions -> {len(codes)} 只 env={env_name}")
        return codes

    def _fetch_dynamic(self) -> List[str]:
        """
        基本面动态池：市值 / 估值(PE等) / 行业(plate) / 价格流动性。
        不在此层做 MACD/RSI/背离等技术信号。
        """
        u = self._universe_cfg()
        dyn = u.get("dynamic") or {}
        market = _resolve_enum(Market, str(dyn.get("market") or self.default_market), Market.US)
        begin = int(dyn.get("begin") or 0)
        num = int(dyn.get("num") or self.max_symbols or 50)
        num = max(1, min(num, 200))

        filter_list = self._build_filters(dyn.get("filters") or [])
        plates = self._resolve_plate_codes(dyn)

        all_codes: List[str] = []
        total_all_count = 0
        # 单次 API 只支持一个 plate；多行业时分别查询再合并
        plate_iter: List[Optional[str]] = plates if plates else [None]
        for plate in plate_iter:
            ret, ls = self.quote_ctx.get_stock_filter(
                market=market,
                filter_list=filter_list if filter_list else None,
                plate_code=plate,
                begin=begin,
                num=num,
            )
            if ret != RET_OK:
                raise RuntimeError(f"get_stock_filter 失败 plate={plate!r}: {ls}")
            if not isinstance(ls, (list, tuple)) or len(ls) < 3:
                raise RuntimeError(f"get_stock_filter 返回异常: {ls}")

            _last_page, all_count, ret_list = ls[0], ls[1], ls[2]
            total_all_count += int(all_count or 0)
            for item in ret_list or []:
                code = normalize_code(
                    getattr(item, "stock_code", None) or getattr(item, "code", None)
                )
                if code:
                    all_codes.append(code)

        summary = self._format_filter_summary(dyn, filter_list, plates)
        print(
            f"[{datetime.now()}] [Universe] dynamic(基本面) -> {len(all_codes)} 只 "
            f"(api_all_count≈{total_all_count}) | {summary}"
        )
        return all_codes

    def _resolve_plate_codes(self, dyn: Dict[str, Any]) -> List[str]:
        """行业/板块：plate_code 单值，或 plate_codes 列表（多行业并集）。"""
        codes: List[str] = []
        single = dyn.get("plate_code")
        if single not in (None, "", "null"):
            codes.append(str(single))
        multi = dyn.get("plate_codes") or []
        if isinstance(multi, (list, tuple)):
            for p in multi:
                if p not in (None, "", "null"):
                    codes.append(str(p))
        # 去重保序
        seen: Set[str] = set()
        out: List[str] = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def _format_filter_summary(
        self,
        dyn: Dict[str, Any],
        filter_list: list,
        plates: List[str],
    ) -> str:
        parts = [f"market={dyn.get('market') or self.default_market}"]
        if plates:
            parts.append(f"plates={plates}")
        else:
            parts.append("plates=ALL")
        for f in filter_list:
            field = getattr(f, "stock_field", None)
            name = getattr(field, "name", None) or str(field)
            lo = getattr(f, "filter_min", None)
            hi = getattr(f, "filter_max", None)
            parts.append(f"{name}:[{lo},{hi}]")
        return " ".join(parts)

    def _build_filters(self, filters_cfg: Sequence[Dict[str, Any]]) -> list:
        """
        构建 moomoo 筛选条件。

        type:
          - simple:    报价/估值类（CUR_PRICE, MARKET_VAL, PE_TTM, PB_RATE ...）
          - financial: 财报类，需 quarter（ROE 相关、利润增速等）

        注意：多个条件里最多只能有一个设置 sort（moomoo API 限制）。
        """
        out = []
        sort_used = False
        for raw in filters_cfg or []:
            if not isinstance(raw, dict):
                continue
            ftype = str(raw.get("type") or "simple").lower()
            if ftype not in ("simple", "financial"):
                print(
                    f"[Universe] dynamic 仅支持 simple/financial（基本面），"
                    f"跳过 type={ftype}"
                )
                continue

            field_name = str(raw.get("field") or "CUR_PRICE").upper()
            field = _resolve_enum(StockField, field_name, None)
            if field is None:
                print(f"[Universe] 未知 StockField: {field_name}，跳过")
                continue

            if ftype == "financial":
                ff = FinancialFilter()
                ff.stock_field = field
                ff.is_no_filter = bool(raw.get("is_no_filter", False))
                q_name = str(raw.get("quarter") or "ANNUAL").upper()
                ff.quarter = _resolve_enum(FinancialQuarter, q_name, FinancialQuarter.ANNUAL)
                if raw.get("min") is not None:
                    ff.filter_min = float(raw["min"])
                if raw.get("max") is not None:
                    ff.filter_max = float(raw["max"])
                sort = raw.get("sort")
                if sort:
                    if sort_used:
                        print(f"[Universe] 已有排序条件，忽略 {field_name}.sort={sort}")
                    else:
                        sd = _resolve_enum(SortDir, str(sort).upper(), None)
                        if sd is not None:
                            ff.sort = sd
                            sort_used = True
                out.append(ff)
                continue

            sf = SimpleFilter()
            sf.stock_field = field
            sf.is_no_filter = bool(raw.get("is_no_filter", False))
            if raw.get("min") is not None:
                sf.filter_min = float(raw["min"])
            if raw.get("max") is not None:
                sf.filter_max = float(raw["max"])
            sort = raw.get("sort")
            if sort:
                if sort_used:
                    print(f"[Universe] 已有排序条件，忽略 {field_name}.sort={sort}")
                else:
                    sd = _resolve_enum(SortDir, str(sort).upper(), None)
                    if sd is not None:
                        sf.sort = sd
                        sort_used = True
            out.append(sf)
        return out

    def _post_process(self, codes: Sequence[str]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for c in codes:
            n = normalize_code(c)
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(n)
            if self.max_symbols > 0 and len(out) >= self.max_symbols:
                break
        return out
