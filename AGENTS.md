# 项目速查

面向家长与孩子的局域网单词记忆 Web 应用。中文 UI；看中文输入英文。Android 后续考虑 Capacitor，当前不做 APK、离线同步或公网注册。

## 技术与入口

- 后端：Python 3.11+，FastAPI、sqlite3、Argon2id；生产由后端同源提供前端构建产物。
- 前端：Vue 3 + TypeScript + Vite，原生 CSS/fetch；Node.js 22.12+。
- `backend/app/main.py`：API、会话/权限、JSON 导入、词典、静态服务。
- `backend/app/db.py`：schema、事务连接、数据库路径；`study.py`：判分、轮次、调度；`cli.py`：创建家长、在线备份。
- `frontend/src/App.vue`：页面和交互；`style.css`：样式；`utils.ts`：导入解析、错误展示。
- `sop.sh start|stop|status|restart`：环境检查、依赖安装、构建、后台进程管理。默认本机 `127.0.0.1:8000`，日志 `.run/server.log`；不自动加载 `.env`。
- 配置：`HOST`、`PORT`（脚本）；`WORDS_DB`、`WORDS_HOSTS`、`WORDS_HTTPS`（后端）；脚本 TLS 另需 `TLS_CERT`、`TLS_KEY`。

## 不可破坏的规则

- 家长从 CLI 创建，网页管理孩子；孩子资源按后端用户归属校验，不能靠前端隐藏。家长管理走显式受控操作。
- 首轮全部未学词，后续仅上一轮错词；查看答案、提示、不会都不能算独立答对。当前题不提前返回英文答案。
- 答案仅做 NFC、strip、casefold；不放宽拼写、内部空格和标点。
- 间隔：10 分钟、1/3/7/14/30 天；失败重新过关回到 10 分钟，最后阶段保持 30 天。同一批次每词最多升级一次。
- 每用户最多一个活动批次；队列落库，刷新可恢复；答题记录、调度和轮次推进同一事务。`attempts.item_id` 唯一，重复提交不重复推进。
- JSON：`word`、`meaning` 必填；`id` 不作主键；可选 `example`、`example_translation`。同词同义合并，同词异义拒绝并提示合并。请求 ≤2 MiB，每库 ≤5000 条；导入全成或全败。
- 时间存 UTC epoch，页面本地显示；当前词库默认以导入时间命名，开始时间另存。

## 安全与数据

- 会话 Cookie：HttpOnly、SameSite；修改请求校验 CSRF 和 Origin，Host 使用允许名单。HTTPS 开启 Secure；HTTP 只供可信局域网临时测试。
- SQL 参数绑定，密码 Argon2id，会话令牌只存哈希；不记录密码、Cookie、Key。单 worker；登录限速是进程内状态。
- SQLite 默认 `data/words.sqlite3`，外键 + WAL；schema 用 `PRAGMA user_version`。改 schema 时提供升级方案，不删库解决问题。
- 不提交 `.venv/`、`.run/`、`data/`、密钥、备份、node_modules、dist。测试不得污染正式库；备份用 SQLite backup，不能直接复制活跃数据库。
- 词典固定来源 Free Dictionary API；缺词、例句、译文或音频不保证。只发送单词，外部内容按纯文本处理；禁止任意 URL 抓取/代理。
- 完整例句和译文支持导入、手工补齐；单词音频失败/超时回退设备英语 TTS，例句用 TTS。设备可能无英语语音，不能把 URL 存在当成发音验收通过。当前没有生成服务或云端 TTS。

## 修改与验证

沿用现有小文件结构，标准库优先，不加无需求的分层或依赖。改 API 同步前端类型；改依赖同步锁文件。路径使用 pathlib，输出路径用 `/`。不擅自提交 Git 或覆盖用户数据。

```sh
.venv/bin/python -m unittest backend.tests.check_core -v
npm --prefix frontend test
npm --prefix frontend run build
bash -n sop.sh
```

后端检查使用临时 SQLite。非平凡逻辑补可运行检查；前端交互改动需浏览器验证，发音需实机。脚本测试使用临时 WORDS_DB 和空闲端口，结束停止服务，不停止其他程序。

## 文档

`README.md`：启动入口；`docs/deployment.md`：配置、备份恢复；`docs/dev_plan.md`：目标计划（不等于完成）；`docs/acceptance.md`：验证与缺口；`docs/sample_words.json`：样例。行为变化同步相关文档，不将未验收能力写成已完成。
