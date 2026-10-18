# AI 开发速查

局域网家庭单词记忆应用：中文提示、英文输入；家长管理孩子账户。用户操作与配置见 `README.md`，本文件只保留开发约束。当前不做公网注册、APK、离线同步、生成服务或云端 TTS。

## 栈与文件

- Python 3.11+ / FastAPI / sqlite3 / Argon2id；Vue 3 / TypeScript / Vite / 原生 CSS、fetch；Node.js 22.12+。
- `backend/app/main.py`：认证、API、导入校验、词典、静态入口；生产同源提供 `frontend/dist`。
- `db.py`：schema、事务连接；`study.py`：判分、队列、调度；`management.py`：词库/词条 CRUD、导出、历史、重置；`import_sources.py`：XLSX、Google 下载；`cli.py`：家长初始化、在线备份。
- `frontend/src/App.vue`：页面与交互；`utils.ts`：导入及错误工具；`style.css`：响应式样式。
- `sop.sh start|stop|status|restart`：环境检查、依赖、构建、后台进程。默认 `0.0.0.0:18765`，自动探测本机 IP 配置 Host 校验；日志 `.run/server.log`。不加载 `.env`，不持久保存启动环境变量。
- 依赖：`backend/requirements.txt` + `requirements.lock`、`frontend/package.json` + `package-lock.json`。配置含 `HOST`、`PORT`、`WORDS_DB`、`WORDS_HOSTS`、`WORDS_HTTPS`、`TLS_CERT`、`TLS_KEY`、`WORDS_GOG_PRIVATE`。

## 业务不变量

- 家长由 CLI 创建，网页管理孩子；所有资源由后端校验归属，家长管理权限必须显式授予，不能靠前端隐藏。
- 每用户最多一个活动学习批次；首轮覆盖未学词，之后仅上一轮错词。提示、揭示答案、不会均不能算独立回忆成功；当前题响应不提前下发拼写。
- 判分仅 NFC + strip + casefold，不放宽内部空格、标点、拼写。队列持久化；答题、调度、轮次推进同一事务；`attempts.item_id` 唯一，重复请求不重复推进。
- 间隔秒数：`600,86400,259200,604800,1209600,2592000`；失败重新过关回到阶段 0，最后保持阶段 5；每词每批次最多升级一次。阶段不是精确记忆概率。
- 导入先预览、重复词逐组选一或全排除；最终接口拒绝重复。重复范围为本次输入，不跨词库。`word`、`meaning` 必填，`id` 不作主键，可选例句/译文；请求 ≤2 MiB，词库 ≤5000 条，事务全成或全败。
- XLSX 首行要求 `word`/`words`、`meaning`；逐页扫描，坏页报告原因，有效页多选合并预览、跨页去重；列表显示前 5 个词。不得静默纠正拼错表头或跳过坏行。
- Google 先公开下载，公开不可访问才私有回退；仅家长且 `WORDS_GOG_PRIVATE=1` 可调用已授权 `gog`。格式错误不回退；固定 argv、无 shell、超时。只允许已校验 Google 来源及受限下载重定向，不提供任意 URL 代理。
- 修改释义、删除、重置取消涉及词条的活动批次，旧请求返回冲突；重置删除该词历史并设未学，不影响其他词。例句编辑不取消批次。词库删除清理相关外键，空词库仍可管理。
- 时间存 UTC epoch、本地显示；词库默认按导入时间命名，开始时间另存。

## 安全与数据

- Cookie HttpOnly、SameSite；修改请求校验 CSRF、Origin；Host 允许名单指服务器地址，不是客户端名单。HTTPS 设置 Secure，HTTP 仅可信局域网临时使用。
- SQL 参数绑定；密码 Argon2id，会话令牌只存哈希；日志不得含密码、令牌、Key。单 worker，登录限速为进程内状态。
- SQLite 默认 `data/words.sqlite3`，外键 + WAL，`PRAGMA user_version` 管 schema；改结构提供升级方案，禁止删库解决问题。备份用 SQLite backup，不复制活跃数据库文件。
- 不提交数据库、备份、密钥、`.env`、`.venv/`、`.run/`、node_modules、dist、测试缓存。测试用临时 WORDS_DB，禁止污染正式库。
- 词典固定 Free Dictionary API；只发送单词，返回内容按纯文本展示。例句/译文支持导入和手工补齐。音频错误或加载超时回退设备英语 TTS；无英语语音需提示，URL 存在不等于播放已验收。

## 修改与检查

保持小结构、标准库优先，不加无需求分层。改 API 同步前端类型；依赖变更同步锁文件；路径用 pathlib，输出路径用 `/`。保留用户工作，不擅自提交、推送或覆盖数据。

```sh
.venv/bin/python -m unittest backend.tests.check_core backend.tests.check_management backend.tests.check_sources -v
npm --prefix frontend test
npm --prefix frontend run build
bash -n sop.sh
```

非平凡逻辑留可运行检查；交互需浏览器验证，发音需目标设备。脚本测试用空闲端口、临时库，结束清理自己的服务，不停止其他程序。`sop.sh` 默认端口变更不能假定无冲突。

文档分工：`README.md` 用户入口；`docs/deployment.md` 运维；`docs/acceptance.md` 真实验证及缺口；`docs/dev_plan.md` 目标而非完成清单；`docs/sample_words.json` 样例。行为变化同步文档，不把 mock 或构建通过写成真实联网/实机通过。
