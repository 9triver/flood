# 高德避洪路线规划

`plan_evacuation_route` 只使用高德 Web 服务规划 `car` 或 `foot` 路线，并把高德返回的 GCJ-02 几何转换为对象库统一使用的 WGS84。驾车默认禁行水深为0.3米，步行默认为0.15米。步行规划使用高德路线规划 2.0，一次请求最多三条候选路线。

函数逐条检查候选路线与指定预测时刻中达到禁行水深的 `ForecastCell` 是否相交，并从安全候选中选择距离最短的一条。所有候选均不安全时返回 `no_safe_route`，不会保存违反洪水约束的路线。高德步行接口不支持传入自定义淹没面进行二次绕行，因此此时需要调整目的地、预测时刻或生成绕行点后分段规划。

高德 Web 服务配置在被 Git 忽略的 `.env` 中：

```dotenv
AMAP_WEB_SERVICE_KEY=your-web-service-key
```

规划结果会校验高德路线起终点与领域对象坐标的距离以及路线绕行倍率。默认最大端点距离为 800 米、最大绕行倍率为 10；超过限制时返回 `invalid_route`。动态规划结果保存在 `domains/flood/data/generated/routing/planned_routes.jsonl`，并通过 `Route` repository 与静态转移路线一起查询。
