# VeighNa框架的期权波动率交易模块

<p align="center">
  <img src ="https://vnpy.oss-cn-shanghai.aliyuncs.com/vnpy-logo.png"/>
</p>

<p align="center">
    <img src ="https://img.shields.io/badge/version-1.3.1-blueviolet.svg"/>
    <img src ="https://img.shields.io/badge/platform-windows|linux|macos-yellow.svg"/>
    <img src ="https://img.shields.io/badge/python-3.10|3.11|3.12|3.13-blue.svg" />
    <img src ="https://img.shields.io/github/license/vnpy/vnpy.svg?color=orange"/>
</p>

## 说明

针对期权波动率交易类策略设计的应用模块，支持波动率曲面实时跟踪、期权组合持仓希腊值风控、Delta自动对冲算法、电子眼波动率执行算法、情景分析压力测试等功能。

## 安装

安装环境推荐基于4.0.0版本以上的【[**VeighNa Studio**](https://www.vnpy.com)】。

直接使用pip命令：

```
pip install vnpy_optionmaster
```


或者下载源代码后，解压后在cmd中运行：

```
pip install .
```


## 修复分支的自动交易安全边界

- **行情新鲜度**：自动 Delta 对冲及电子眼使用期权与标的的实时 Tick；默认要求报价时间戳不超过 **30 秒**，缺失/过期/明显未来时间戳一律停止新增自动委托。交易网关和运行机器必须使用一致的时区及同步时钟；对不活跃合约可能采取保守停单。
- **风险失效**：当隐含波动率或 Greeks 不可计算时保留上次有效数值供显示，但标记为不可用于自动交易，不能将异常误解释为零敞口。
- **撤单和拒单的人工恢复**：先停止对应自动对冲/电子眼，确认真实委托终态、实际成交及最新持仓；在界面选择指定委托，输入操作员、原因并二次确认。只有审计文件成功写入才允许解除阻断，不会自动重启算法。
- **本地审计文件**：工作目录下的 `optionmaster_recovery_audit.jsonl` 为追加型记录，包含 UTC 时间、操作员、委托号、原因和状态，文件权限默认仅限所有者。备份、保留期限及访问控制由部署方负责，文件不得提交进 Git。
- **订阅异常**：VeighNa MainEngine 暂无统一的撤订接口。若初始化过程中网关订阅部分失败，本进程会阻止该组合盲目重试。需要停止并核对网关状态，重启网关与客户端后再初始化；不应仅依靠 Python 状态回滚。
- **环境和准入**：通用 wheel 只提供 Python 定价模型，独立 Cython 扩展需要按目标平台重新编译并验证。当前修复使用 Mac ARM64、Python 3.13 离线回归验证，**没有完成真实 CTP 成交/撤单乱序和 Windows 平台验收，不能据此直接准入实盘**。
- **2026-11 P0 行情与订单门禁**：自动 Delta 对冲与电子眼依赖每个合约的、来自人工核验交易所日历的精确连续竞价窗口，以及盘口有效性和涨跌停上下限。默认不携带任何官方时段配置；**没有合法配置或边界不确定时，自动新增委托一律禁止**。环境变量 `OPTIONMASTER_VERIFIED_SESSIONS_FILE` 必须指向绝对路径的 JSON，要求 `version=1`、`source.official_url`、`source.verified_by`、`source.verified_at`，以及按 `symbol` / `exchange` / `trading_day` 指定的 `phase=continuous`、带 `+08:00` 的 `start` / `end`。夜盘应采用实际交易日，时间窗为左闭右开。**测试文件 `tests/_session_test_support.py` 生成的合成窗口不能用于实盘**。
- **行情入站过滤**：只有在有效会话内、时间戳未过期、盘口可解释且同合约时间单调的 Tick 才能进入 `portfolio.update_tick()`；拒绝原因计数在 `OptionEngine.tick_rejections`。合法单边盘口保留可执行方向，但 Mid 不再取缺失侧零报价的平均值，估值和 Greeks 必须有可靠双边输入才能参与自动交易。
- **还需单独验收**：交易所日历数据获取和每次变更的人工核验、真实 CTP 委托与成交回报乱序、断线恢复（OM-06）、Windows 验证与生产准入（OM-08）。
