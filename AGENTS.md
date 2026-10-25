# AI 开发速查

局域网家庭单词记忆应用：中文提示、英文输入；家长管理孩子账户。用户操作与配置见 `README.md`，本文件只保留开发约束。当前不做公网注册、APK、离线同步或通用生成服务。云端 TTS 支持 Azure/Google，由家长后台配置主备服务。

## 栈与文件

- Python 3.11+ / FastAPI / sqlite3 / Argon2id；Vue 3 / TypeScript / Vite / 原生 CSS、fetch；Node.js 22.12+。
- `backend/app/main.py`：认证、API、导入校验、词典、静态入口；生产同源提供 `frontend/dist`。
- `db.py`：schema、事务连接；`study.py`：判分、队列、调度；`management.py`：词库/词条 CRUD、导出、历史、重置；`import_sources.py`：XLSX、Google 下载；`cli.py`：家长初始化、在线备份。
- `frontend/src/App.vue`：页面与交互；`utils.ts`：导入及错误工具；`style.css`：响应式样式。
- `sop.sh start|stop|status|restart`：环境检查、依赖、构建、后台进程。默认 `0.0.0.0:18765`，自动探测本机 IP 配置 Host 校验；日志 `.run/server.log`。不加载 `.env`，不持久保存启动环境变量。
- 依赖：`backend/requirements.txt` + `requirements.lock`、`frontend/package.json` + `package-lock.json`。配置含 `HOST`、`PORT`、`WORDS_DB`、`WORDS_HOSTS`、`WORDS_HTTPS`、`TLS_CERT`、`TLS_KEY`、`WORDS_GOG_PRIVATE`。

## 业务不变量

- 家长由 CLI 创建，网页管理孩子；所有资源由后端校验归属，家长管理权限必须显式授予，不能靠前端隐藏。
- 学校指定中文释义与英文拼写，严格判分；跨库同词允许重复、各自进度。支持一天多次、隔天继续、考前速记，不限每次题量。单库学习与跨多库综合复习共享逐词记忆状态；涉及同一词库不得新建重叠任务。综合复习只建一个固定队列任务，默认纳入发起时 stage>=0 且到期词，可显式包含未学／尚未过关词，可跨 2～3 天恢复；任务外新增到期仅提示重新发起，不静默追加。首轮覆盖待学集合，之后仅上一轮错词及至多一次的流畅巩固。提示、揭示答案、不会均不算独立回忆成功；当前题响应不提前下发拼写。
- 判分仅 NFC + strip + casefold，不放宽内部空格、标点、拼写。队列持久化；答题、调度、轮次推进同一事务；`attempts.item_id` 唯一，重复请求不重复推进。
- 调度按真实经过时间、逐次提交保存，不等整轮结束；返回页面只暂停，不结算或撤销。立即曝光后重输不算长期回忆；输入流畅度 `hard` 仅辅助、不改变 `correct`，最多一次额外流畅巩固，禁止无限循环。调度为基于实际间隔的保守启发式，非概率模型：首次独立答对 600 秒，达到既有间隔后成功按实际间隔的 2 倍增长、最多 30 天；近期重复不放大间隔或延后原到期，失败缩至原间隔 1/4（600 秒～1 天），`hard` 缩至 600 秒。未独立答对的新词保持未学；阶段为兼容展示。FSRS 未来评估，不宣称科学校准或长期效果已验证。
- 导入先预览、重复词逐组选一或全排除；最终接口拒绝重复。重复范围为本次输入，不跨词库。`word`、`meaning` 必填，`id` 不作主键，可选例句/译文；请求 ≤2 MiB，词库 ≤5000 条，事务全成或全败。
- XLSX 首行要求 `word`/`words`、`meaning`；逐页扫描，坏页报告原因，有效页多选合并预览、跨页去重；列表显示前 5 个词。不得静默纠正拼错表头或跳过坏行。
- Google 先公开下载，公开不可访问才私有回退；仅家长且 `WORDS_GOG_PRIVATE=1` 可调用已授权 `gog`。格式错误不回退；固定 argv、无 shell、超时。只允许已校验 Google 来源及受限下载重定向，不提供任意 URL 代理。
- 修改释义、删除、重置取消涉及词条的活动批次，旧请求返回冲突；重置删除该词历史并设未学，不影响其他词。例句编辑不取消批次。词库删除清理相关外键，空词库仍可管理。
- 时间存 UTC epoch、本地显示；词库默认按导入时间命名，开始时间另存。

## 安全与数据

- Cookie HttpOnly、SameSite；修改请求校验 CSRF、Origin；Host 允许名单指服务器地址，不是客户端名单。HTTPS 设置 Secure，HTTP 仅可信局域网临时使用。
- SQL 参数绑定；密码 Argon2id，会话令牌只存哈希；日志不得含密码、令牌、Key。单 worker，登录限速为进程内状态。
- SQLite 默认 `data/words.sqlite3`，外键 + WAL，`PRAGMA user_version` 管 schema；改结构提供升级方案，禁止删库解决问题。备份用 SQLite backup，不复制活跃数据库文件。
- 个人备份 `user_backup.py`：所有用户仅导出/覆盖自己的学习数据，不包含账户名/密码/会话；文件内部 ID 重新映射，不能跨账户写入。恢复单事务、先自动备份、取消恢复批次，不改账户、不影响他人；2 MiB 上限和严重确认。
- 整库管理 `database_admin.py`：仅家长、再次验证密码；恢复是覆盖非合并，前端严重警告+勾选+中文确认+二次确认，后端校验确认标记。50 MiB 上限；校验 schema/完整性/外键/可用家长，恢复前自动备份，所有会话失效；不执行上传 SQL。main 的单 worker API gate 确保恢复无在途写入；不得绕开。
- 不提交数据库、备份、密钥、`.env`、`.venv/`、`.run/`、node_modules、dist、测试缓存。测试用临时 WORDS_DB，禁止污染正式库。
- 词典固定 Free Dictionary API；只发送单词，返回内容按纯文本展示。例句/译文支持导入和手工补齐。未启用云端朗读时，音频错误或加载超时回退设备英语 TTS；启用后按 Azure/Google 主备配置合成，设备回退须显式开启。密钥独立存储，不进入数据库备份；音频缓存上限 100 MiB，家长可查看容量与清空。无英语语音需提示，URL 存在不等于播放已验收。

## 修改与检查

保持小结构、标准库优先，不加无需求分层。改 API 同步前端类型；依赖变更同步锁文件；路径用 pathlib，输出路径用 `/`。保留用户工作，不擅自提交、推送或覆盖数据。

```sh
.venv/bin/python -m unittest backend.tests.check_core backend.tests.check_management backend.tests.check_sources backend.tests.check_database_admin backend.tests.check_user_backup backend.tests.check_sop backend.tests.check_speech backend.tests.check_statistics backend.tests.check_multi_study backend.tests.check_time_study -v
npm --prefix frontend test
npm --prefix frontend run build
bash -n sop.sh
```

新增功能或修复须同步新增或更新测试，覆盖正常、边界和失败路径；交互需浏览器验证，发音需目标设备，未验证项须注明。脚本测试用空闲端口、临时库，结束清理自己的服务，不停止其他程序。`sop.sh` 默认端口变更不能假定无冲突。

文档分工：`README.md` 用户入口；`docs/deployment.md` 运维；`docs/acceptance.md` 真实验证及缺口；`docs/dev_plan.md` 目标而非完成清单；`docs/sample_words.json` 样例。行为变化同步文档，不把 mock 或构建通过写成真实联网/实机通过。
