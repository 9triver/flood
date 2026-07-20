# OSM 避洪路线规划

系统使用 GraphHopper 11 读取珊瑚河周边 OSM 道路网络，并提供 `car` 和 `foot` 两种路由模式。`plan_evacuation_route` 会把指定预测时刻中达到禁行水深的 `ForecastCell` 聚合为空间区域，并通过 GraphHopper 请求级 custom model 排除这些区域内的道路。驾车默认禁行水深为0.3米，步行默认为0.15米。

规划结果还会校验起终点到 OSM 道路的吸附距离和路线绕行倍率。默认最大吸附距离为 800 米、最大绕行倍率为 10；超过限制时返回 `invalid_route`，不会把不可信路线写入对象库。

## 启动 GraphHopper

本机需要 Java 17 或更高版本。首次运行：

```bash
.venv/bin/python scripts/graphhopper.py prepare
.venv/bin/python scripts/graphhopper.py serve
```

第一次启动会导入 OSM XML 并在 `local/routing/graph-cache` 建立路由图，后续启动直接读取缓存。服务监听 `http://127.0.0.1:8989`，状态检查：

```bash
.venv/bin/python scripts/graphhopper.py status
```

应用默认连接上述地址。远端部署可在 `.env` 中设置：

```dotenv
GRAPHHOPPER_URL=http://127.0.0.1:8989
GRAPHHOPPER_TIMEOUT_SECONDS=20
```

## 数据边界

路由网络由 `domains/flood/data/sources/osm_roads_shanhu.json` 构建，目前覆盖约 `111.04-111.48E, 24.22-24.59N`。更新 OSM 源数据后执行：

```bash
.venv/bin/python scripts/graphhopper.py prepare --force-osm
rm -rf local/routing/graph-cache
```

删除图缓存是为了让 GraphHopper 重新导入更新后的路网。动态规划结果保存在 `domains/flood/data/generated/routing/planned_routes.jsonl`，并通过 `Route` repository 与静态转移路线一起查询。
