# Narrative-Immersion（Hermes 叙境）

一个面向长视频/成片的 AI 互动叙事平台：支持上传视频、自动镜头分析、角色库沉淀、放映中问答与剧情干预、分支时间线生成与切换。

## 功能概览

- 上传视频并自动切分（1s/5s/10s/scene/story）
- 镜头分析与结构化标注（人物、地点、动作、对白等）
- 角色库管理（角色聚合、参照图、三视图、用户备注）
- 放映厅互动（剧情问答 + 叙事干预）
- 干预流水线（安全审查 -> 编剧/导演/分镜 -> 复用检索 -> 视频生成 -> 质检 -> 时间线分支）
- 分支时间线编辑（切入时刻、片段重排、删除生成段）
- 任务日志与生成资产沉淀（便于运营与排查）

## 项目结构

```text
.
├─ frontend/              # React + Vite 前端
├─ backend/               # FastAPI 后端
│  ├─ app/                # 业务代码（routes/services/models）
│  ├─ scripts/            # 调试/回填脚本
│  └─ storage/            # SQLite、上传文件、生成产物
└─ docs/                  # 产品文档
```

## 环境要求

- Python 3.9+
- Node.js 18+
- FFmpeg（需可在命令行中调用）

## 快速开始

### 1) 后端启动

```bash
cd backend
python -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

后端默认地址：`http://127.0.0.1:8765`

### 2) 前端启动

```bash
cd frontend
npm install
npm run dev
```

前端默认地址：`http://127.0.0.1:5173`

> 当前配置已支持局域网访问（Vite `host: true`），同网段设备可通过你的本机 IP + 端口访问。

## 核心配置（.env）

后端读取 `backend/.env`（前缀 `HERMES_`）。常用项：

- 基础服务：`HERMES_HOST`、`HERMES_PORT`、`HERMES_DB_URL`
- 对话模型：`HERMES_LLM_PROVIDER`、`HERMES_LLM_BASE_URL`、`HERMES_LLM_API_KEY`、`HERMES_LLM_MODEL`
- 视频生成：`HERMES_VIDEO_PROVIDER`、`HERMES_VIDEO_BASE_URL`、`HERMES_VIDEO_API_KEY`、`HERMES_VIDEO_MODEL`
- 叙事策略：`HERMES_INTERVENTION_NO_FALLBACK`、`HERMES_VIDEO_REQUIRE_CHARACTER_REFERENCE`

## 使用流程（最短路径）

1. 打开前端，进入「上传切分」，上传一条视频
2. 在「媒资库」确认镜头与角色（可补角色参照图/备注）
3. 进入「放映厅」播放并发送消息（问答或干预）
4. 在「干预任务」查看流水线与生成结果
5. 切换到新分支时间线继续播放

## 常见问题

### 1. 视频生成失败（模型返回 4xx）

- 检查 `HERMES_VIDEO_PROVIDER` 与对应 `BASE_URL/API_KEY` 是否匹配
- DashScope 文生视频仅支持 Wan 系列模型；对话模型不能直接用于视频接口
- MiniMax 真实出片通常需要角色参照图（取决于 `HERMES_VIDEO_REQUIRE_CHARACTER_REFERENCE`）

### 2. 干预后没有切到新时间线

- 查看 `/api/chat` 返回中的 `new_timeline_id`
- 在「干预任务」确认该任务是否 `done`

### 3. 前端能打开但接口报错

- 确保后端已运行在 `8765`
- 检查前端代理配置（`frontend/vite.config.ts` 的 `/api` 与 `/storage`）

## 文档

- 产品文档：`docs/产品文档.md`

## License

当前仓库未声明开源许可证，默认保留所有权利。若计划开源，建议补充 `LICENSE` 文件。