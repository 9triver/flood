# 珊瑚河洪水应急预警智能体

本项目以珊瑚河流域 GIS 为中心，将边界流量演进、CNN 水动力预测、淹没影响分析、避洪路线规划和 OAG 智能体交互组织在同一个运行工作空间中。

## 环境准备

需要安装 Git、Git LFS 和 [uv](https://docs.astral.sh/uv/)。首次获取项目时必须同时拉取 `agent` 子模块和 CNN 权重：

```bash
git clone --recurse-submodules git@github.com:9triver/flood.git
cd flood
git lfs install
git lfs pull
uv sync
```

若仓库已经存在，可执行：

```bash
git submodule update --init --recursive
git lfs pull
uv sync
```

## 运行配置

`.env` 不进入 Git。根据 [.env.example](.env.example) 创建本地 `.env`，至少配置：

```dotenv
LLM_API_KEY=your-key
LLM_API_URL=http://your-openai-compatible-service/v1
LLM_MODEL=your-model
AMAP_WEB_SERVICE_KEY=your-amap-web-service-key
```

运行完整系统前可执行检查：

```bash
uv run python scripts/check_runtime.py --profile full
```

只检查不依赖 LLM 和高德密钥的 HTTP/GIS 基础服务：

```bash
uv run python scripts/check_runtime.py --profile server
```

## 启动

```bash
uv run python server/app.py --host 127.0.0.1 --port 8765
```

访问 <http://127.0.0.1:8765>。

## 数据边界

仓库内包含运行所需的领域对象库、mock 边界流量、CNN 网格、配置和 Git LFS 权重。以下内容是本地状态，不进入 Git：

- `local/runtime/flood/`：演进 workspace、预测、路线和可重建缓存。
- `.oag_data/`：Agent 会话与 trace。
- `local/source_data/`：用于重新生成对象库的原始资料，日常运行不读取。
- `.env`：LLM 和高德密钥。

`local/runtime/flood/cache/hydrodynamic/mesh.sqlite` 会在首次访问水动力网格时由仓库内的 `GT.txt` 自动重建。

## 验证

```bash
uv run python -m unittest discover -s tests -q
uv run pytest agent/tests -q
node --check server/static/app.js
```
