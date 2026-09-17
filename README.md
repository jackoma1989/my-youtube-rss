# YouTube 频道自动转苹果播客 (YouTube to Apple Podcasts Sync)

全自动、零服务器成本的 Serverless 流水线：利用 **GitHub Actions** 定时抓取指定 YouTube 频道的最新视频，使用 `yt-dlp` 极速抽取原生高品质 AAC 音频（M4A 格式），上传至 **Cloudflare R2**（每月 10GB 免费存储且完全免出站流量费），并生成符合 Apple 官方规范的播客 RSS `feed.xml`。

可在 iPhone / iPad / Mac 的“播客 (Apple Podcasts)”或任何泛用型播客客户端中直接订阅。

---

## 流程架构

```text
YouTube 目标频道
       │
       ▼ (定时检查新视频 / yt-dlp)
GitHub Actions (免费云端 Runner)
       │
       ├─► 提取原生 AAC 音频流 (140) -> 存储为 .m4a
       ├─► 上传至 Cloudflare R2 (支持 HTTP Range 断点续传)
       ├─► 自动清理超出保留上限的旧期数音频 (控制在 10GB 免费额度内)
       └─► 生成并更新标准的 feed.xml
               │
               ▼
   苹果播客 (Apple Podcasts) 输入 RSS URL 订阅收听
```

---

## 快速上手（5 分钟完成配置）

### 第一步：准备 Cloudflare R2 存储桶

1. 登录 [Cloudflare 控制台](https://dash.cloudflare.com/)，在左侧边栏进入 **R2 对象存储**。
2. 点击 **“创建存储桶 (Create Bucket)”**，命名存储桶（例如 `my-podcast`），点击创建。
3. **开启公开访问**：
   - 进入刚创建的 Bucket，选择 **设置 (Settings)** 选项卡。
   - 找到 **“公共访问 (Public Access)”**：
     - **推荐方式**：如果你有托管在 Cloudflare 的域名，点击“连接域 (Connect Domain)”，绑定二级域名（如 `podcast.yourdomain.com`）。
     - **快捷方式**：或者开启 **“R2.dev 子域 (Allow Access via R2.dev)”**，会生成形如 `https://pub-xxxxxx.r2.dev` 的公开链接。
4. **获取 API 凭证**：
   - 返回 R2 首页，点击右侧 **“管理 R2 API 令牌 (Manage R2 API Tokens)”**。
   - 点击 **“创建 API 令牌 (Create API token)”**，权限选择 **“对象读写 (Object Read & Write)”**，存储桶选择刚才创建的 Bucket。
   - 复制保存：
     - **访问密钥 ID (Access Key ID)**
     - **机密访问密钥 (Secret Access Key)**
     - **终结点中的账户 ID (Account ID)**（在终结点 URL `https://<Account_ID>.r2.cloudflarestorage.com` 中可直接看到）。

---

### 第二步：配置 GitHub 仓库与 Secrets

1. 将本项目代码推送到你自己的 GitHub 仓库（可以设为 Private 私有仓库）。
2. 在 GitHub 仓库页面，进入 **Settings** -> **Secrets and variables** -> **Actions**。
3. 点击 **New repository secret**，依次添加以下 6 个密钥：

| Secret 变量名 | 说明 | 示例 |
| :--- | :--- | :--- |
| `YOUTUBE_CHANNEL_URL` | 你要抓取的 YouTube 频道或播放列表链接 | `https://www.youtube.com/@ChannelName` |
| `R2_ACCOUNT_ID` | Cloudflare 账户 ID | `a1b2c3d4e5f6...` |
| `R2_ACCESS_KEY_ID` | R2 API Access Key ID | `0123456789abcdef...` |
| `R2_SECRET_ACCESS_KEY` | R2 API Secret Access Key | `abcdef0123456789...` |
| `R2_BUCKET_NAME` | R2 存储桶名称 | `my-podcast` |
| `R2_PUBLIC_URL` | R2 公开访问的基础 URL（**末尾不要带斜杠**） | `https://pub-xxxxxx.r2.dev` 或 `https://podcast.yourdomain.com` |
| `TELEGRAM_BOT_TOKEN` | （可选）Telegram Bot Token，新单集自动推送手机 | `8955999825:AAFR...` |
| `TELEGRAM_CHAT_ID` | （可选）Telegram 接收人 Chat ID | `5584552077` |

> [!TIP]
> 可选参数：可在 **Variables** 或 **Secrets** 中添加 `MAX_EPISODES`（默认 15，代表仅保留最新 15 期，旧期数自动从 R2 彻底删除，避免占满存储）。

---

### 第三步：手动触发首次运行

1. 进入 GitHub 仓库的 **Actions** 选项卡。
2. 在左侧列表中选择 **YouTube to Podcast Sync** 工作流。
3. 点击右侧的 **Run workflow** 按钮即可立即开始第一次同步。
4. 之后系统会自动按预设定时（每天北京时间 10:00 与 22:00 各一次）自动检查并同步更新。

---

### 第四步：在苹果播客中添加订阅

同步完成后，你的播客源地址就是：
```text
https://<你的R2域名>/feed.xml
```
（例如：`https://pub-xxxx.r2.dev/feed.xml` 或 `https://podcast.yourdomain.com/feed.xml`）

**在 Apple Podcasts 客户端中订阅：**
1. 打开 iPhone / iPad / Mac 上的 **“播客 (Podcasts)”** App。
2. 点击底部的 **“资料库 (Library)”**。
3. 点击右上角的 **“...”**（或“编辑”）。
4. 选择 **“通过 URL 关注节目... (Follow a Show by URL...)”**。
5. 粘贴上述 `feed.xml` 的完整链接，点击关注。

现在，新视频发布后就会自动以播客的形式推送到你的苹果设备，支持锁屏播放、定时关闭、断点续传、CarPlay 以及 Apple Watch 同步！

---

## 本地测试运行

如果你想在本地机器上先测试抓取或生成效果：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制环境变量模板
cp .env.example .env
# 编辑 .env 文件，填入你的频道地址

# 3. 运行本地模拟测试 (DRY RUN 模式，不上传云端)
python -m podcast_sync.main --dry-run --channel-url "https://www.youtube.com/@Google" --max-episodes 2

# 查看本地生成的播客文件
cat output/feed.xml
```

---

## 常见问题与技术特点

* **为什么选 M4A (AAC) 而不是 MP3？**
  YouTube 官方原生为视频配备了 AAC 128kbps 音频流（format `140`）。脚本直接抽取原始数据流并打包入 M4A 容器，整个过程无需任何 CPU 转码，仅需几秒钟即可完成，且 0 音质损失，对移动设备播放器极其友好。
* **为什么使用 Cloudflare R2？**
  Apple Podcasts 严格要求音频服务提供商支持 `HTTP Range Requests`（206 Partial Content，用于进度拖拽与流式播放）。很多免费网盘直链不支持此特性，而 Cloudflare R2 原生支持，且每月享有 10GB 免费存储空间，出站流量永久免费。
