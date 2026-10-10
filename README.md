# MyFans 下载器

一个面向中文用户的 [MyFans](https://myfans.jp) 视频与图片下载工具。提供本地网页界面，支持按创作者批量下载、按作品 ID 下载、清晰度选择、自动归档和下载状态记录。

> 本项目只用于下载你自己的账号已经有权访问的内容。请遵守 MyFans 服务条款、创作者授权范围及所在地法律。

## 功能

- 下载 2160p、1080p、720p、480p 和 360p 视频；
- 指定画质不存在时自动选择合适的可用画质；
- 下载普通作品、可访问的 Back Number 和套餐作品；
- 下载一个图片作品中的全部图片；
- 按免费、非免费或全部作品筛选；
- 按创作者自动整理视频和图片目录；
- 检查已有视频，避免重复下载损坏文件；
- 中文网页界面和实时下载日志；
- 记住最近输入的创作者、作品 ID 和下载选项；
- 支持自定义文件名和可选 JSON 元数据。

## Windows 一键启动

### 第一次使用

1. 双击 `SETUP.bat`，安装 Python 和项目依赖；
2. 确保电脑已经安装 FFmpeg；
3. 双击 `启动网页端.bat`；
4. 程序会启动本地服务并自动打开浏览器。

以后只需双击：

```text
启动网页端.bat
```

默认访问地址：

```text
http://127.0.0.1:5000
```

关闭启动程序的命令窗口即可停止网页服务。如果服务已经在运行，再次双击启动文件只会打开浏览器，不会重复启动服务。

## 设置登录令牌

下载付费或会员内容需要 MyFans 登录后的 `_mfans_token`。免费公开内容可以在没有令牌的情况下尝试下载。

1. 在浏览器中登录 [MyFans](https://myfans.jp)；
2. 打开浏览器开发者工具；
3. 在 Cookie 中找到 `_mfans_token`；
4. 打开下载器网页的“设置”；
5. 将 `_mfans_token` 的值粘贴到“MyFans 令牌”；
6. 点击“保存设置”。

设置页不会回显已经保存的令牌。令牌保存在本机的 `header.txt`，该文件已被 Git 忽略，不会随正常提交上传到 GitHub。

也可以复制 `header.example.txt` 为 `header.txt`，然后手动填写：

```text
authorization: Token token=YOUR_TOKEN_HERE
user-agent: Mozilla/5.0
```

请勿分享 `header.txt`，也不要把真实令牌粘贴到 Issue、日志或公开聊天中。

## 网页端使用方法

### 按创作者批量下载

1. 选择“视频”或“图片”；
2. 选择“按创作者批量下载”；
3. 输入创作者用户名，不需要输入 `@`；
4. 选择内容范围；
5. 下载视频时选择清晰度；
6. 点击“开始下载”。

### 按作品 ID 下载

1. 选择内容类型；
2. 选择“按作品 ID 下载”；
3. 输入作品 ID；
4. 点击“开始下载”。

网页会在当前浏览器中记住最近使用的创作者、作品 ID、内容类型、下载方式、筛选条件和清晰度。点击“清除输入记忆”可以删除这些浏览器本地记录。

## 下载目录

默认保存结构：

```text
downloads/
└─ 创作者用户名/
   ├─ videos/
   │  └─ 视频文件.mp4
   └─ images/
      └─ 图片文件.jpg
```

程序会使用 `ffprobe` 检查已有视频。损坏的文件会被删除并重新下载；检查正常的文件会跳过。

## 网页设置

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| 文件名模板 | `{creator}_{date}_{id}` | 控制下载文件名称 |
| 替换分隔符 | `_` | 替换文件名中的路径分隔符 |
| 同时下载作品数 | `3` | 同时处理多少个作品 |
| JSON 元数据 | 关闭 | 为每个媒体文件生成元数据 |
| MyFans 令牌 | 空 | 用于访问账号已解锁的内容 |

文件名模板支持以下变量：

| 变量 | 内容 |
| --- | --- |
| `{creator}` | 创作者用户名 |
| `{date}` | 作品发布日期 |
| `{title}` | 作品标题或正文第一行 |
| `{id}` | 作品 ID |

例如：

```ini
pattern = {date}_{title}_{id}
```

## 配置文件

基础配置位于 `config.ini`：

```ini
[Settings]
output_dir = downloads

[Filename]
pattern = {creator}_{date}_{id}
separator = _

[Threads]
threads = 3
```

网页设置会更新对应配置，同时保留其他配置项。登录令牌不会写入 `config.ini`。

## 其他启动方式

命令行菜单：

```powershell
python main.py
```

直接启动网页服务：

```powershell
python web_launcher.py
```

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

项目仍保留 Docker 相关文件，但本 Fork 当前主要维护和使用 Windows 本地网页端。

## 常见问题

### 网页没有自动打开

手动访问：

```text
http://127.0.0.1:5000
```

### 出现 401 或 403

登录令牌可能已经过期。重新登录 MyFans，获取新的 `_mfans_token` 并在设置页保存。

### 视频无法下载

确认 FFmpeg 已加入系统 `PATH`：

```powershell
ffmpeg -version
ffprobe -version
```

### 修改设置后没有生效

环境变量的优先级高于 `config.ini`。如果设置了 `FILENAME_PATTERN`、`THREAD_COUNT` 等环境变量，它们会覆盖网页保存的配置。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖网页路由、配置保存、下载状态、文件名、令牌解析、清晰度选择和 HLS 播放列表选择。真实 MyFans API、账号权限和长时间媒体下载仍需在实际环境中验证。

## 安全说明

- 网页服务默认只监听本机；
- 不要把端口 5000 直接暴露到公网；
- 不要提交 `header.txt`；
- 如果令牌意外上传到 GitHub，请立即更换登录会话；
- 下载状态保存在本机，不会上传到额外服务。

## 上游项目与致谢

本仓库 Fork 自 [FudgeRK/MyfansDownloader](https://github.com/FudgeRK/MyfansDownloader)，并在其基础上继续维护中文网页端与 Windows 使用体验。

感谢原项目及所有贡献者：[Shenggang](https://github.com/Shenggang)、[bluems](https://github.com/bluems)、[Serph91P](https://github.com/Serph91P)、[albertphil](https://github.com/albertphil)、[0xSho](https://github.com/mydcxiao)、[Foxtopus](https://github.com/Foxtopus)。

## 许可证

本项目使用 [GNU General Public License v3.0](LICENSE)。
