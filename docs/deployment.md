# 首版运行与验收

## 当前实现

- 家长通过命令行创建；网页创建、停用孩子账户和重置密码。
- JSON 导入，账户词库隔离。
- 看中文输入英文、提示、不会、错词轮次、断点续学、幂等提交。
- 10 分钟、1/3/7/14/30 天间隔；最后阶段维持 30 天。
- 导入完整例句和译文、免费词典查词、词典音频、浏览器英语 TTS。
- 词典不能保证中文译文或完整例句，缺失可手工补充。首版没有接入文本生成或云端语音付费服务，不保证所有设备的 TTS 可用。
- 首版没有 Android、离线同步、自动备份定时任务。

## 环境和启动

需要 Python 3.11+、Node.js 22.12+ 或兼容当前 Vite 的较新版本。

在项目根目录执行：

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m backend.app.cli create-parent parent
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`。密码交互输入，不放在命令行历史中。停止服务使用 Ctrl+C。不要使用默认或重复密码。

如 Python 环境版本不同导致安装失败，优先使用 Python 3.12 或 3.13。

## systemd 本地服务

项目根目录执行：

```sh
sudo ./sop.sh service
```

需要 Python 3.11+、Node.js 22.12+、npm、systemd。脚本原地安装依赖、构建前端、生成并启用 `words.service`，不安装或修改 Nginx，不复制代码到 `/opt`。重复执行用于更新，会重启应用。`sop.sh start|restart` 在操作服务前补齐变化的锁定依赖，并按内容哈希决定是否构建；构建失败不停止旧服务。

- 服务工作目录为执行脚本的项目目录，运行用户/组为项目目录所有者。项目在 `/root` 时服务以 root 运行；更推荐独立非特权用户拥有的项目目录。代码由 systemd 只读保护，仅项目 `data/` 可写。
- 数据库固定为当前目录 `data/words.sqlite3`，整个 `/data/` 已被 Git 忽略，包括 WAL、SHM 和自动恢复备份。
- 配置文件 `/etc/remember-words.env`；已有 Host、HTTPS、子路径设置保留，`WORDS_DB` 更新为当前目录绝对路径。配置采用无引号 `KEY=value`，不执行 shell。
- 若旧 `WORDS_DB` 指向其他已存在数据库，脚本停服后用 SQLite backup 迁入，校验完整性并保留原库。新旧库同时存在时拒绝自动覆盖或选择，需人工核对；不要直接复制活跃数据库。
- 首次默认根路径、HTTP Cookie、本机 `127.0.0.1:18765` 监听。反代 HTTPS `/words/` 时首次使用 `sudo WORDS_BASE_PATH=/words WORDS_HTTPS=1 ./sop.sh service`；已有配置以环境文件为准。修改路径后运行 `sudo ./sop.sh restart` 同步前端构建。启动/重启从 `systemctl show` 获取实际 `Environment`、`EnvironmentFiles`，按顺序只读解析文件，不执行 shell、不输出配置值；沿用安装器的无引号 `KEY=value` 格式，不支持的格式会安全报错，不操作旧服务。
- 浏览器使用的域名/IP 必须加入 `WORDS_HOSTS`（不含端口，禁止 `*`）。后端只信任本机代理头。

```sh
./sop.sh status
sudo ./sop.sh start
sudo ./sop.sh restart
sudo ./sop.sh stop
journalctl -u words -n 100 --no-pager
```

`sop.sh` 检测到已安装 `words.service` 时直接转交 `systemctl`，保留其退出码；未安装时保留原后台脚本行为。首次创建家长账户，项目根目录执行（密码交互输入）：

```sh
.venv/bin/python -m backend.app.cli create-parent parent
```

### Nginx 手动配置（独立于安装脚本）

`deploy/nginx-words.conf` 仅为参考模板。自行备份 Nginx 配置后，在目标 HTTPS server 内加入该模板内容，执行 `nginx -t`，成功后 reload。现有服务器入口为 `https://home.yuchberrybbs.com:8182/words/`，`WORDS_HOSTS` 需包含 `home.yuchberrybbs.com`；安装脚本不会修改现有代理。

`proxy_pass http://127.0.0.1:18765;` 不带尾斜杠，保留 `/words/` 前缀。前端构建与后端运行都需要 `WORDS_BASE_PATH=/words`；HTTPS 在 Nginx 终止时设置 `WORDS_HTTPS=1`，无需给 Uvicorn 配证书。

```sh
curl --noproxy '*' https://home.yuchberrybbs.com:8182/words/api/health
```

`-k` 仅用于本机诊断，不代表客户端证书信任已验证。

## 局域网访问

查找主机局域网 IP，例如 `192.168.1.100`，再启动：

```sh
export WORDS_HOSTS='localhost,127.0.0.1,192.168.1.100'
.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

手机和电脑连接相同可信局域网，访问 `http://192.168.1.100:8000`。只开放局域网防火墙，不配置路由器公网端口转发。IP 变化时更新 WORDS_HOSTS；不要用 `*` 取消 Host 校验。

**安全警告：HTTP 不加密密码或会话，仅供可信局域网临时试用。正式使用推荐 HTTPS，并让手机信任服务器证书。不要在公共 Wi-Fi 中使用 HTTP 登录。**

可直接让 Uvicorn 使用已准备的 TLS 证书：

```sh
export WORDS_HTTPS=1
.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile /absolute/path/key.pem --ssl-certfile /absolute/path/cert.pem
```

证书需要覆盖访问的主机名或 IP，并由客户端信任。WORDS_HTTPS 仅控制 Cookie 的 Secure 属性，不会自动启用 TLS。环境变量需由 shell 导出，程序不自动加载 `.env`。

## 开发模式

两个终端分别运行：

```sh
.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000
```

```sh
npm --prefix frontend run dev
```

Vite 代理 `/api` 到后端。修改后端监听环境或构建前端后，重启服务确保静态资源挂载更新。

### 重启构建检查

两种运行模式共用构建检查与 `.run/lock` 管理操作锁，锁覆盖依赖准备、构建和重启。`status` 不构建；`stop` 不准备依赖。不会修改 systemd 的监听或环境配置。

输入 SHA-256 覆盖 `frontend/src`、`public`（若有）、`index.html`、`package.json`、锁文件、`tsconfig*.json`、`vite.config.*`、前端 `.env*`、`backend/app/**/*.py`、Python 依赖文件、构建脚本及有效 `WORDS_BASE_PATH`。未提交内容变化同样有效；后端代码变化也触发构建。构建时显式传入有效路径，覆盖 Vite 本地环境文件中的同名设置。

同时校验 `frontend/dist` 全部文件内容；缺少 `index.html`、文件增删或手动构建覆盖都会重建。指纹保存在 `.run/build-input.hash`、`.run/build-dist.hash`；首次或删除缓存后重建。先构建到 `.run/build-dist`，成功才发布到 `frontend/dist`，失败不改旧产物、不执行停止/重启。原子性只覆盖构建失败保护；成功发布与服务重启不是跨进程事务。不要并行手动运行 npm 构建或修改源码。

隔离检查（临时项目、mock npm/systemctl，不操作真实服务或数据库）：

```sh
python3 -m unittest backend.tests.check_sop -v
bash -n sop.sh
```

## 示例 JSON

```json
[
  {
    "word": "hello",
    "meaning": "你好",
    "example": "She said hello to her new teacher.",
    "example_translation": "她向新老师问好。"
  },
  {
    "word": "world",
    "meaning": "世界",
    "example": "We want to learn more about the world.",
    "example_translation": "我们想更多地了解这个世界。"
  }
]
```

JSON、XLSX 和 Google Drive 导入均先预览，再由用户确认。重复单词按 NFC、首尾空格与大小写归组，用户选择保留项或全部排除，最终提交不得含重复词。不同词库允许同词，记忆进度独立。

XLSX 只支持 `.xlsx`，第一行必须有 `word`（兼容 `words`）、`meaning`；`id` 可选且忽略，可选例句列 `example`、`example_translation`。扫描全部工作表，有效页可多选，选中页合并为一个词库进行预览与跨页去重；空表、说明页、缺列页或内容错误页跳过并列出原因，不阻塞其他有效页。全部页面均不可用时才报错。上传限制 2 MiB、每表 5000 条；接口整体请求也限制 2 MiB，接近上限时需预留 JSON 包装空间。ZIP 展开限制 20 MiB，拒绝 XML DTD 与实体。

Drive 支持 Google Sheets 分享链接、Drive `/file/d/ID` 和 `/open?id=ID`。先尝试公开 XLSX 下载；公开访问失败才回退私有读取，格式错误和缺少列不会触发私有读取。私有回退需要家长登录，并在启动服务前设置 `WORDS_GOG_PRIVATE=1`，服务器已安装并授权 `gog`。实际命令为 `gog drive download ID --no-input --out PATH`；Sheets 另加 `--format=xlsx`。此授权可访问服务器 Google 账户文件，只应授权可信家长；不开放给孩子。未配置、无权限、超时或格式不对都向用户报错。程序不执行用户传入命令或任意下载 URL。

首次学习覆盖整个词库；暂停后恢复活动批次。一个孩子同时只有一个活动批次，点击其他词库学习也会恢复该批次。已完成首轮的词只能在到期复习入口再次学习，避免重复刷新阶段。

## 发音与例句

- 提交答案或揭示提示后，可以查词补全；提前揭示算本题未独立答对。
- Free Dictionary API 提供英文解释、音标，以及可能缺失的音频和例句，中文译文不保证提供。本次环境使用应用同款 httpx 请求返回 HTTP 200；urllib 请求曾返回 HTTP 403，外部服务行为可能随客户端或网络变化。页面保留失败提示与手工补全。
- 播放优先使用允许来源的词典 HTTPS 音频，其次使用设备英语 TTS。本次词典音频独立请求曾超时，JSON 成功不代表音频一定可播放；需要在实际设备验收回退行为。
- 浏览器无英语语音时，在操作系统安装英语语音包并重新打开浏览器。不同设备音色、网络需求不同。本次自动化浏览器可调用 speechSynthesis，但英语 voice 数量为 0，因此不宣称实际朗读已验收。
- 首版未申请云端服务 Key，不进行生成式补全。例句应由家长核对，保证与中文释义一致。

## 自动检查

```sh
.venv/bin/python -m unittest backend.tests.check_core backend.tests.check_management backend.tests.check_sources -v
npm --prefix frontend test
npm --prefix frontend run build
```

7 项自动检查覆盖导入回滚、账户权限、CSRF、Host/来源/请求大小限制、错词递归、提示处理、重复提交、调度、词典故障、手工例句权限和会话撤销。浏览器已验证登录、JSON 导入、错词第二轮、完成页、鼠标软键盘输入；390px 视口学习页无横向溢出。手机音频、TTS、真实触摸交互仍需实机验收，不能由自动检查代替。测试数据使用临时数据库，不写入正式库。测试客户端当前有 httpx 弃用警告，不影响通过；后续跟随 Starlette 调整测试依赖。

## 个人数据备份

所有登录用户可导出自己的学习数据，格式为 `remember_words_user` JSON v1，包含词库、词条、答题历史与复习阶段，不包含账户名、密码、登录会话。个人恢复绑定当前账户，文件里的内部 ID 只用于映射关联，不保留为数据库 ID。

恢复覆盖当前账户全部学习数据，不合并、不影响其他账户、不改账户名和密码；已保存的活动批次按取消处理。操作要求重新输入密码、风险勾选、输入“确认覆盖我的学习数据”和二次确认。覆盖前自动保存个人 JSON 到数据库目录的 `backups/`；失败整笔回滚。请求限 2 MiB，备份过大需联系主机管理员处理。个人与整库文件不能混用。

## 网页整库导出与恢复

家长管理页支持整库操作，要求再次输入当前家长密码。导出不含会话，但包含全部账户密码哈希和学习数据，必须安全保存。

恢复是**覆盖，不是合并**。当前所有账户、词库、答题历史和进度被替换，所有用户退出；必须知道备份内家长账户的密码。界面要求风险勾选、输入“确认覆盖整个数据库”和二次确认。只能上传当前 schema 的 SQLite 数据库，上限 50 MiB；无有效家长、完整性错误、外键错误、额外表或触发器等会拒绝。

恢复前自动将当前库在线备份至 `数据库所在目录/backups/`，不向网页公开绝对路径。恢复失败若自动回退也失败，应停止服务，保留数据库及自动备份，按照下方主机恢复步骤处理，不删除文件。

**仅支持单 worker、单应用进程访问同一库。** API gate 在恢复时阻止其他请求执行；不可通过多个 Uvicorn 实例绕过此限制。恢复前让孩子暂停学习；长时间外部请求可能延迟恢复开始。

## 备份

默认数据库路径为 `data/words.sqlite3`。可用 WORDS_DB 指定其他位置。

```sh
.venv/bin/python -m backend.app.cli backup /absolute/path/backups/words-2026-10-18.sqlite3
```

采用 SQLite 在线备份，不直接复制正在使用的数据库。目标必须为新文件，防止覆盖。

### 恢复步骤

1. 停止所有应用进程。
2. 使用备份命令另存当前数据库，保留回退副本。
3. 在独立目录验证备份 `PRAGMA integrity_check` 返回 `ok`。
4. 确认数据后，将备份放到新的路径，通过 WORDS_DB 指向该文件。
5. 启动服务，检查账户、词库、学习批次。确认成功前不要删除原数据库及其 WAL/SHM 文件。

CLI 不提供自动覆盖数据库的恢复命令；网页恢复使用上述严格确认流程。备份包含账户密码哈希和学习记录，应限制文件权限，不上传公开仓库。

## 当前限制

- 登录失败限速使用进程内记录，重启会清空；仅支持单 worker 部署。
- 多标签页同时操作时，冲突会返回错误，刷新恢复当前题目。
- 词库名默认使用导入时间，开始时间单独记录；尚未提供改名界面。
- 当前没有内容来源审校、生成服务、精确个人遗忘曲线或家长跨账户学习统计。
- 后端权限保护数据隔离，但不做防作弊系统。
