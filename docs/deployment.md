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

不提供自动覆盖数据库的恢复命令，防止误操作。备份包含账户密码哈希和学习记录，应限制文件权限，不上传公开仓库。

## 当前限制

- 登录失败限速使用进程内记录，重启会清空；仅支持单 worker 部署。
- 多标签页同时操作时，冲突会返回错误，刷新恢复当前题目。
- 词库名默认使用导入时间，开始时间单独记录；尚未提供改名界面。
- 当前没有内容来源审校、生成服务、精确个人遗忘曲线或家长跨账户学习统计。
- 后端权限保护数据隔离，但不做防作弊系统。
