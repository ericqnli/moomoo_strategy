from moomoo import *
import pandas as pd
from datetime import datetime, timedelta

print("=== 历史K线测试（正确代码格式） ===")

quote_ctx = OpenQuoteContext()

code = "US.BRK.B"   # 关键：加上 US.

# 计算日期范围
end_date = datetime.now().strftime("%Y-%m-%d")
start_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")

print(f"请求代码: {code}，日期范围: {start_date} 到 {end_date}")

ret, data, _ = quote_ctx.request_history_kline(
    code,
    start=start_date,
    end=end_date,
    ktype=KLType.K_DAY,
    max_count=60
)

if ret == RET_OK:
    df = pd.DataFrame(data)
    print(f"✅ {code} 历史K线获取成功！共 {len(df)} 条")
    print("\n最近5条数据：")
    print(df[['time_key', 'close', 'volume']].tail(5))
else:
    print(f"❌ 获取失败: {data}")

quote_ctx.close()
print("测试完成")
