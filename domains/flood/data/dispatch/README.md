# 龙潭水库调度模型

计算核心从 `flood-updates-20261006-194945/flood/Scheduling model/reservoir_dispatch.py`
移植到 `domains/flood/runtime/reservoir_engine.py`，业务适配入口为同目录的
`reservoir_dispatch.py::simulate_reservoir_dispatch()`。运行和测试不依赖原更新包目录。

原始引擎文件的 SHA-256：

```text
a60a043aa30a23e4b9fd928904c9336a23f7a8de64a569937fa17644e3511ef8
```

数值函数、规则优先级和约束沿用原实现；命令行入口移至
`scripts/reservoir_dispatch.py`，参数文件名统一为小写。业务适配层保留原有
`series`、`release_m3s`、`end_level_m`、`reason`、`constraint` 等字段，并支持
`DispatchSettings` 指定 `RULE`、`OUTFLOW`、`LEVEL` 模式及逐时目标。

为支持从预测起点续算，计算入口另接受可选的精确初始库容和原全过程入流峰值，
以延续原水量状态及超标准洪水判定。默认整段运行语义保持不变。Agent 的24小时
候选方案试算说明见 `docs/水库调度试算.md`。

## 参数与时间

- 水位、泄流曲线、安全泄量、校核洪峰和规则均读取本目录的 `.dat` 文件。
- `dispatch_run_parameters.dat` 的初始水位供应用默认设置使用；其中的日期、
  49 个时段及步长用于独立 CSV 命令的示例窗口校验，不限制业务演进数据。
- 应用根据所选降雨过程计算入库流量和步长，默认预见期为 25 步（含当前步）。
  独立命令未传 `--forecast-steps` 时使用所有剩余时段。
- 应用场景由 `DispatchSettings.mode` 选择模式；独立命令从
  `dispatch_control_mode.dat` 读取模式。两者均使用 `dispatch_rules.dat`。
- 每时段目标优先于场景统一目标；留空使用场景目标。人工模式必须为每步提供
  对应目标；`LEVEL` 的目标表示时段末水位。

## 保留的业务语义

- 超标准洪水按整段输入洪峰判定，超过 679 m³/s 时整段进入应急判断，即使洪峰
  不在当前预见期内。人工模式默认允许应急调度接管。
- 可用水量下限为库容曲线最低库容，非汛限库容；允许利用汛限以下的存量水。
- `max_release_m3s` 同时替换原泄流曲线能力和下游安全泄量，预演、实际控制与
  应急状态均遵守此值。原曲线能力另存为 `design_available_release_m3s`；实际
  下泄仍受目标和可用水量约束。超过 139.59 m³/s 的设计参考值时保留提醒。
- 坝顶高程 253.30 m 用于记录；应急水位判据仍为校核水位 248.91 m。引擎不含
  漫顶、溃坝计算，也不把坝顶作为库水位的硬截断。
- 曲线范围外按原算法线性外推，`curve_extrapolated` 包含实际路径与预演路径；
  `design_level_exceeded` 结合当前水位与全能力下泄预演最高水位判断。
- `reservoir_outlet_flow_m3s` 和 CNN 的 `upstream` 边界均采用实际下泄流量；
  上游来水单独保留为 `reservoir_inflow_m3s`。

## 独立试算与回归验证

在项目根目录执行：

```bash
.venv/bin/python scripts/reservoir_dispatch.py \
  tests/fixtures/reservoir_dispatch/dispatch_input.csv \
  /tmp/longtan-dispatch.csv --outlet-capacity 20

.venv/bin/python -m unittest \
  tests.test_reservoir_engine tests.test_reservoir_dispatch \
  tests.test_dispatch_telemetry tests.test_boundary_flow
```

49 时段的参考输入和结果保存在 `tests/fixtures/reservoir_dispatch/`，保留原数值，
仅将时间规范为独立命令要求的格式。其他测试覆盖三种模式、人工目标受限、
水量平衡、上限覆盖、安全接管以及边界流量与预测窗口的一致性。
