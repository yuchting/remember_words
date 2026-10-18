# 单词记忆

面向家长与孩子的局域网 Web 应用。Vue 3 + TypeScript + FastAPI + SQLite；中文提示，输入英文。

## 功能

- 家长创建和管理孩子账户，词库、答题历史、复习进度互相隔离。
- JSON、XLSX、Google Sheets / Drive 导入：先预览，处理重复词，再确认入库。
- 表格扫描全部工作表：有效页可多选，显示词数及前 5 个单词；无效页提示原因。选中页合并为一个词库，跨页统一去重。
- 首轮覆盖未学词，后续只练错词；支持暂停续学、实体键盘与页面软键盘。
- 当次过关后按 10 分钟、1/3/7/14/30 天复习；失败重学，最后保持 30 天维护间隔。
- 词库改名、明细、删除、JSON 导出；单词释义与例句编辑、删除、记忆重置、真实答题趋势。
- 免费词典解释与单词音频、设备 TTS 朗读例句。免费来源可能缺失，例句和译文可导入或手工补齐。

## 启动

环境：macOS / Linux、Bash、Python 3.11+、Node.js 22.12+、npm。

```sh
./sop.sh start
./sop.sh status
./sop.sh restart
./sop.sh stop
```

首次启动自动创建虚拟环境、安装锁定依赖、构建前端，并引导创建家长账户。之后登录网页创建孩子账户。

- 本机访问：**http://127.0.0.1:18765**。
- 其他设备：使用 `http://服务器局域网IP:18765`，不要将 `0.0.0.0` 当作访问地址。
- 默认监听所有 IPv4 网卡 `0.0.0.0:18765`；自动探测本机 IP 配置 Host 校验。
- 日志：`.run/server.log`；默认数据库：`data/words.sqlite3`。
- 启动、重启会构建前端；不停止占用端口的其他程序。

**HTTP 不加密密码和会话，仅在可信局域网临时使用。不要配置公网端口转发；正式使用推荐 HTTPS。**

### 自定义配置

```sh
HOST=0.0.0.0 PORT=18765 \
WORDS_HOSTS=localhost,127.0.0.1,192.168.1.100 \
./sop.sh restart
```

| 变量 | 用途 |
|---|---|
| `HOST` / `PORT` | 脚本监听地址、端口；仅本机使用可设 `HOST=127.0.0.1` |
| `WORDS_HOSTS` | 允许访问的服务器 IP / 域名，逗号分隔；不是客户端 IP 名单 |
| `WORDS_DB` | 自定义 SQLite 路径 |
| `PYTHON` | 创建虚拟环境时使用的 Python 命令 |
| `WORDS_HTTPS` | `1` 启用 Secure Cookie；脚本还需 `TLS_CERT`、`TLS_KEY` 才开启 TLS |
| `WORDS_GOG_PRIVATE` | `1` 允许家长通过已授权的 `gog` 回退读取私有 Google 文件 |

脚本不自动加载 `.env`，也不保存自定义环境变量；重启时需再次传入。TLS、手动启动、备份恢复见[部署文档](docs/deployment.md)。

## 导入格式

```json
[
  {"word": "hello", "meaning": "你好"},
  {"word": "world", "meaning": "世界"}
]
```

- JSON 必填 `word`、`meaning`，可选 `example`、`example_translation`；导入 `id` 不作为数据库主键。
- XLSX 第一行必须有 `word`（兼容 `words`）、`meaning`，可带上述例句列；不支持 `.xls`，不猜测纠正拼错的表头。
- 重复检查限本次导入，忽略单词大小写、首尾空白；用户选择保留一条或整组排除。不同词库可有同词。
- 请求和文件不超过 2 MiB，每库最多 5000 个词。词库默认以导入时间命名，可修改。
- Google 链接先公开下载；无法公开访问时才尝试家长授权的 `gog`。格式错误不回退私有读取。服务器需预先安装并授权 `gog`，私有下载尚需真实授权联调。
- 删除会移除相应历史；重置词条保留内容、清除该词答题历史与进度。修改释义、删除、重置会结束涉及该词的活动批次，操作前有确认。

## 开发检查

```sh
.venv/bin/python -m unittest backend.tests.check_core backend.tests.check_management backend.tests.check_sources -v
npm --prefix frontend test
npm --prefix frontend run build
bash -n sop.sh
```

测试使用临时数据库。发音依赖网络与设备英语语音包，必须实机验收；复习阶段和答题趋势不代表精确记忆概率。当前不支持 Android APK、离线同步、生成式例句或云端 TTS。

## 文档

- [部署、手动启动、备份恢复](docs/deployment.md)
- [示例词库](docs/sample_words.json)
- [验收记录与已知限制](docs/acceptance.md)
- [开发目标计划](docs/dev_plan.md)（不代表全部完成）
- [AI 开发速查](AGENTS.md)
