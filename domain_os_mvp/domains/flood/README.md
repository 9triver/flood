# Flood Domain World

该目录把珊瑚河防洪应急领域安装到 `domain_os_mvp`，用于验证 Domain OS 能否承载传感器、GIS 资产、专业模型、外部服务、领域产品和业务场景组成的完整领域世界。

## 资源空间

```text
/flood/shanhu
  /assets                         16 类 1666 个版本化 GIS 对象
  /sensors/stations               MQTT 边界水文站及当前指标
  /models/hydrodynamic/cnn-v2     水动力模型资源
  /services/routing/amap          高德路线服务资源
  /products/forecasts             版本化洪水预测
  /products/routes                版本化路线
  /products/assessments           Agent 结构化研判
  /scenarios/flood-emergency      防洪应急场景
```

静态对象来自仓库现有 `domains/flood/data/objects`，包括流域、河流、行政区、危险区、道路、桥梁、设施、转移单元和安置点等。对象和类型索引都能通过 `read(path)` 获取。

## 运行链路

```text
四边界站 MQTT 遥测
  -> Journal / World State
  -> 防洪应急确定性进程
  -> act(model, run_forecast)
  -> Forecast 产品
  -> Agent read(Forecast)
  -> act(amap, plan_route)
  -> Route 产品
  -> act(products/assessments, publish_assessment)
  -> Situation Assessment 产品
```

`FloodDomainClient` 是 Agent 侧 SDK。它没有新增内核接口，所有方法最终只调用 `read/history/watch/act/operation`。

## 真实适配与离线替身

- GIS 对象库使用仓库中的真实版本化数据。
- MQTT 使用正式 JSON 线格式；`PahoMqttIngress` 可连接真实 Broker。
- `ExistingCnnRunner` 可调用仓库中的 CNN 水动力模型。
- `AmapRouteRunner` 可调用高德 Web 服务。
- 默认装配使用快速水动力替身和直线路线替身，保证本地测试不需要模型权重、网络或密钥。产品中会明确标记 `is_surrogate=true`，不会伪装成真实专业结果。

## 验证

```bash
uv run python scripts/check_flood_domain_world.py
uv run python -m pytest -q tests/test_flood_domain_world.py
```

真实高德适配可通过 `--real-amap` 启用；真实 CNN 可通过 `--real-cnn` 启用，分别需要 `.env`/环境变量中的 Key 和完整模型权重。
