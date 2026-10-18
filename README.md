# 单词记忆

局域网 Web 应用：Vue 3 + TypeScript + FastAPI + SQLite。

- 家长创建孩子账户，数据互相隔离。
- 导入 JSON，看中文输入英文；首轮全部，后续只练错词。
- 间隔复习、实体和页面软键盘、完整例句导入与编辑。
- 免费词典查词、单词发音、例句朗读；缺失内容明确提示。

## 脚本启动（推荐）

```sh
./sop.sh start
./sop.sh status
./sop.sh restart
./sop.sh stop
```

`start`、`restart` 自动检查 Python 3.11+、Node.js 22.12+、npm，按锁文件补齐依赖并构建前端；首次引导创建家长账户。默认后台监听 `127.0.0.1:8000`，日志在 `.run/server.log`。重启先准备构建，成功后才停止旧服务。停止仅操作脚本记录且身份匹配的进程，不抢占其他程序端口。

局域网示例（替换实际 IP）：

```sh
HOST=0.0.0.0 WORDS_HOSTS=localhost,127.0.0.1,192.168.1.100 ./sop.sh start
```

脚本不自动读取 `.env`；自定义 `HOST`、`PORT`、`WORDS_DB` 等变量在重启时仍需传入。HTTPS 设置见部署文档；脚本启用 HTTPS 时需同时设置 `WORDS_HTTPS=1`、`TLS_CERT`、`TLS_KEY`。

## 手动启动

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m backend.app.cli create-parent parent
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`，使用刚创建的家长账户登录。

- [运行、局域网部署、备份恢复](docs/deployment.md)
- [详细开发计划](docs/dev_plan.md)
- [可直接导入的示例词库](docs/sample_words.json)
- [首版验收与未完成项](docs/acceptance.md)

免费词典不保证完整例句、译文或音频。首版通过导入与手工编辑补齐例句，发音可回退到设备英语 TTS；未接入生成服务或云端语音。

HTTP 不加密登录信息。局域网临时测试外，推荐配置 HTTPS，详见部署文档。
