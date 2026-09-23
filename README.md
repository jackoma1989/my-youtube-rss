# YouTube & 抖音频道自动转苹果播客 (YouTube & Douyin to Apple Podcasts Sync)

全自动、零服务器成本的 Serverless 流水线：利用 **GitHub Actions** 定时抓取指定 YouTube 频道与抖音创作者的最新音视频，极速抽取原生高品质音频（YouTube 原生 M4A + WebVTT 字幕；抖音 原生高码率 MP3），自动上传至 **Cloudflare R2**（每月 10GB 免费存储且免出站流量费），并生成符合 Apple 官方规范的播客 RSS `feed.xml`。

可在 iPhone / iPad / Mac 的“播客 (Apple Podcasts)”或任何泛用型播客客户端中直接订阅，支持**实时歌词字幕滚动（YouTube）、后台锁屏播放、定时关闭与全设备进度同步**！

---

## 流程架构

```text
┌────────────────────────────────┐     ┌────────────────────────────────┐
│       YouTube 目标频道         │     │        抖音 目标博主           │
└───────────────┬────────────────┘     └───────────────┬────────────────┘
                │ (yt-dlp 定时检查)                    │ (双签名 API / TailSocks 隧道)
                ▼                                      ▼
┌───────────────────────────────────────────────────────────────────────┐
│                    GitHub Actions (免费云端 Runner)                   │
├──────────────────────────────────────┬────────────────────────────────┤
│ YouTube 流水线:                      │ 抖音流水线:                    │
│ ├─► 提取原生 AAC 音频流 (.m4a)       │ ├─► 抓取博主作品列表 (极速解析) │
│ ├─► 提取中/英 WebVTT 字幕 (.vtt)      │ ├─► 原生流式分块直连下载 (.mp3) │
│ └─► 生成 <podcast:transcript> 节点    │ └─► 自动清理 R2 未完成分片碎片  │
├──────────────────────────────────────┴────────────────────────────────┤
│ 共同存储与分发:                                                       │
│ ├─► 上传至 Cloudflare R2 (支持 HTTP Range 206 断点续传与拖拽)          │
│ ├─► 自动根据 max_episodes 清理超出限额的旧期数音频 (控制在 10GB 免费内) │
│ ├─► 生成独立频道 RSS 源 (如 geekerwan.xml, wangzhian.xml)              │
│ └─► (可选) Telegram 机器人实时向手机推送新剧集通知                     │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
                                   ▼
                   苹果播客 (Apple Podcasts iOS 17.4+)
                 [独立节目封面 + 锁屏断点播放 + 实时同步]
```

---

## 核心特性

1. **双平台全自动同步**：
   * **YouTube**：支持频道、播放列表，自动下载原生 AAC 音频并抓取高精度字幕（WebVTT 格式，支持 iOS 17.4+ 歌词式实时逐句高亮滚动播放）。
   * **抖音 (Douyin)**：支持任意公开博主主页，利用原生 API 签名机制精准获取最新作品；音频文件走火山引擎 CDN 分块直连下载，秒级传输。
2. **多频道独立分发**：每个频道拥有独立的 RSS 源文件（如 `geekerwan.xml`、`caijinglukou.xml`、`wangzhian.xml`），在播客客户端中可分别关注，互不串台。
3. **零服务器运行**：完全基于 GitHub Actions + Cloudflare R2，日常运行 0 费用。
4. **精准定时触发（避开 GitHub Cron 延迟）**：
   * GitHub 免费版的 Cron 定时器通常有 30~90 分钟的排队延迟。
   * 本项目提供本地 Windows 计划任务脚本，每天准时通过 GitHub REST API 唤醒工作流：
     * **YouTube 任务**：每天 **10:00** 与 **22:00**
     * **抖音任务**：每天 **10:30** 与 **22:30**（错峰 30 分钟，推送清晰不撞车）
5. **智能容错与自愈**：
   * 自动清理 R2 存储桶中因网络中断残留的“未完成分片（Ongoing Multipart Upload）”。
   * 剧集清单与链接自愈校准，防止域名或频道名变更引起校验失败。

---

## 快速上手与配置指南

### 第一步：准备 Cloudflare R2 存储桶

1. 登录 [Cloudflare 控制台](https://dash.cloudflare.com/)，在左侧边栏进入 **R2 对象存储**。
2. 点击 **“创建存储桶 (Create Bucket)”**，命名存储桶（例如 `youtuberss`），点击创建。
3. **开启公开访问**：
   * 进入刚创建的 Bucket，选择 **设置 (Settings)** 选项卡。
   * 找到 **“公共访问 (Public Access)”**：
     * **推荐方式**：绑定自定义域名（如 `podcast.yourdomain.com`）。
     * **快捷方式**：开启 **“R2.dev 子域 (Allow Access via R2.dev)”**（生成形如 `https://pub-xxxxxx.r2.dev` 的链接）。
4. **（强烈推荐）添加分片自动清理规则**：
   * 在存储桶设置页向下滚动到 **Object lifecycle rules (对象生命周期规则)** -> 点击 **Add rule**。
   * 规则类型勾选 **Abort incomplete multipart uploads (中止未完成分片上传)**，天数设为 **1 day** 并保存。
5. **获取 API 凭证**：
   * 返回 R2 首页，点击右侧 **“管理 R2 API 令牌 (Manage R2 API Tokens)”** -> **“创建 API 令牌”**。
   * 权限选择 **“对象读写 (Object Read & Write)”**，存储桶选择刚才创建的 Bucket。
   * 记录保存：
     * **访问密钥 ID (Access Key ID)**
     * **机密访问密钥 (Secret Access Key)**
     * **终结点中的账户 ID (Account ID)**

---

### 第二步：配置频道清单

本项目支持两种频道配置方式（可混合使用）：

#### 1. 抖音频道：[`douyin_channels.json`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/douyin_channels.json)（推荐）
在代码根目录的 `douyin_channels.json` 中登记需要订阅的博主：
```json
[
  {
    "id": "caijinglukou",
    "name": "路口大爷",
    "url": "https://www.douyin.com/user/MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c",
    "max_episodes": 15,
    "category": "Business",
    "language": "zh-cn",
    "enabled": true
  },
  {
    "id": "geekerwan",
    "name": "极客湾Geekerwan",
    "url": "https://www.douyin.com/user/MS4wLjABAAAAXXVrVvbTDOhdGW_LzlSDkdN2hZT_CRXmzzafGN-GIbkV6skPAaH2uANSFru076ZT",
    "max_episodes": 15,
    "category": "Technology",
    "language": "zh-cn",
    "enabled": true
  }
]
```

#### 2. YouTube 频道：[`channels.json`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/channels.json)（推荐）
在代码根目录的 `channels.json` 中配置 YouTube 频道：
```json
[
  {
    "id": "wangzhian",
    "name": "王局拍案",
    "url": "https://www.youtube.com/@wangzhian",
    "max_episodes": 15,
    "category": "News",
    "language": "zh-cn"
  }
]
```

---

### 第三步：配置 GitHub Secrets

在 GitHub 仓库页面，进入 **Settings** -> **Secrets and variables** -> **Actions**，点击 **New repository secret** 添加以下变量：

| Secret 变量名 | 说明 | 示例 |
| :--- | :--- | :--- |
| `R2_ACCOUNT_ID` | Cloudflare 账户 ID | `a1b2c3d4e5f6...` |
| `R2_ACCESS_KEY_ID` | R2 API Access Key ID | `0123456789abcdef...` |
| `R2_SECRET_ACCESS_KEY` | R2 API Secret Access Key | `abcdef0123456789...` |
| `R2_BUCKET_NAME` | R2 存储桶名称 | `youtuberss` |
| `R2_PUBLIC_URL` | R2 公开访问的基础 URL（**末尾不带斜杠**） | `https://podcast.yourdomain.com` |
| `DOUYIN_COOKIE` | 抖音网页端 Cookie（用于请求博主帖子列表接口） | `passport_csrf_token=...; sessionid=...;` |
| `TAILSCALE_AUTH_KEY` | Tailscale 预授权密钥（格式：`tskey-auth-...`），用于访问国内出口节点 | `tskey-auth-kXXXXXX-XXXXXXXX` |
| `TAILSCALE_EXIT_NODE` | （可选）Tailscale 出口节点 IP/主机名（默认为家里 NAS） | `100.107.43.2` |
| `TELEGRAM_BOT_TOKEN` | （可选）Telegram Bot Token，新单集自动推送手机 | `8955999825:AAFR...` |
| `TELEGRAM_CHAT_ID` | （可选）Telegram 接收人 Chat ID | `5584552077` |

> [!TIP]
> **不想把频道公开提交到 Git？**
> 你可以直接在 GitHub Secrets 中配置 `DOUYIN_CHANNEL_URL_1` 与 `DOUYIN_CHANNEL_ID_1=geekerwan`、`YOUTUBE_CHANNEL_URL_1` 等，工作流会自动识别并采用自定义 ID。

---

### 第四步：本地 Windows 计划任务配置（免 GitHub 延迟）

在本地电脑上打开 PowerShell，运行以下命令即可完成本地计划任务注册：

```powershell
# 1. 注册抖音任务：每天 10:30 与 22:30 准时通过 GitHub API 触发
.\scripts\setup_schedule_task.ps1 -Action Create

# 2. 检查计划任务运行状态
Get-ScheduledTask | Where-Object { $_.TaskName -like "*PodcastSyncTrigger*" } | Select-Object TaskName, State
```

| 任务名称 | 执行时间 | 调度的 GitHub Actions 工作流 |
| :--- | :--- | :--- |
| `YouTubePodcastSyncTrigger` | 每天 `10:00` & `22:00` | `podcast_sync.yml` |
| `DouyinPodcastSyncTrigger` | 每天 `10:30` & `22:30` | `douyin_sync.yml` |

---

### 第五步：在苹果播客中添加订阅

每个频道拥有独一无二的 RSS 地址：
```text
https://<你的R2域名>/<channel_id>.xml
```
例如：
* 极客湾：`https://podcast.yourdomain.com/geekerwan.xml`
* 路口大爷：`https://podcast.yourdomain.com/caijinglukou.xml`
* 王局拍案：`https://podcast.yourdomain.com/wangzhian.xml`

**在 Apple Podcasts 客户端中订阅：**
1. 打开 iPhone / iPad / Mac 上的 **“播客 (Podcasts)”** App。
2. 点击底部的 **“资料库 (Library)”**。
3. 点击右上角的 **“...”**（或“编辑”）。
4. 选择 **“通过 URL 关注节目... (Follow a Show by URL...)”**。
5. 粘贴上述 XML 链接并关注即可。

---

## 运维工具与脚本

在 [`scripts/`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/scripts) 目录下提供了维护小工具：
* **`scripts/clean_multipart.py`**：检测并强制清理 R2 存储桶中所有滞留的未完成分片上传（已集成在主同步脚本启动项中自动执行）。
* **`scripts/migrate_channel.py`**：多线程服务端直拷工具，用于在 R2 中秒级重命名/迁移频道（如将 `douyin_2` 迁移为 `geekerwan`），无须重新下载。
* **`scripts/trigger_podcast_sync.ps1`**：远程 REST API 触发器，支持 `-Workflow podcast_sync.yml` 或 `-Workflow douyin_sync.yml` 或 `-Workflow all`。
