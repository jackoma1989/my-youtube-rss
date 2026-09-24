# YouTube & 抖音 & B站频道自动转苹果播客 (YouTube, Douyin & Bilibili to Apple Podcasts Sync)

全自动、零服务器成本的 Serverless 流水线：利用 **GitHub Actions** 定时抓取指定 YouTube 频道、抖音创作者与 B站 UP 主的最新音视频，极速抽取原生高品质音频（YouTube 原生 M4A + WebVTT 字幕；抖音 原生高码率 MP3；B站 原生 174k AAC M4A + 官方 AI 字幕），自动上传至 **Cloudflare R2**（每月 10GB 免费存储且免出站流量费），并生成符合 Apple 官方规范的播客 RSS `feed.xml`。

可在 iPhone / iPad / Mac 的“播客 (Apple Podcasts)”或任何泛用型播客客户端中直接订阅，支持**实时歌词字幕滚动、后台锁屏播放、定时关闭与全设备进度同步**！

---

## 流程架构

```text
┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐
│    YouTube 目标频道    │  │      抖音 目标博主     │  │     Bilibili 目标UP    │
└───────────┬────────────┘  └───────────┬────────────┘  └───────────┬────────────┘
            │                           │                           │
            ▼                           ▼                           ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                       GitHub Actions (免费云端 Runner)                         │
├────────────────────┬───────────────────────────┬───────────────────────────────┤
│ YouTube 流水线:    │ 抖音流水线:               │ B站流水线:                    │
│ ├─► 提取原生 AAC   │ ├─► 抓取博主作品列表      │ ├─► 提取原生 174k AAC (.m4a)  │
│ ├─► 提取 WebVTT    │ ├─► 原生流式分块直连下载  │ ├─► 提取官方 AI 中文字幕 (.vtt)│
│ └─► 歌词式实时字幕 │ └─► 自动清理 R2 碎片      │ └─► 严格 1 集保留策略 (极轻量) │
├────────────────────┴───────────────────────────┴───────────────────────────────┤
│ 共同存储与分发:                                                                │
│ ├─► 上传至 Cloudflare R2 (支持 HTTP Range 206 断点续传与拖拽)                   │
│ ├─► 自动根据 max_episodes 清理超出限额的旧期数音频 (控制在 10GB 免费内)          │
│ ├─► 生成独立频道 RSS 源 (如 tim_hurricane.xml, geekerwan.xml, wangzhian.xml)   │
│ └─► (可选) Telegram 机器人实时向手机推送新剧集通知                              │
└───────────────────────────────────────┬────────────────────────────────────────┘
                                        │
                                        ▼
                       苹果播客 (Apple Podcasts iOS 17.4+)
                     [独立节目封面 + 锁屏断点播放 + 实时同步]
```

---

## 核心特性

1. **三平台全自动同步**：
   * **YouTube**：支持频道、播放列表，自动下载原生 AAC 音频并抓取高精度字幕（WebVTT 格式，支持 iOS 17.4+ 歌词式实时逐句高亮滚动播放）。
   * **抖音 (Douyin)**：支持任意公开博主主页，利用原生 API 签名机制精准获取最新作品；音频文件走火山引擎 CDN 分块直连下载，秒级传输。
   * **B站 (Bilibili)**：支持任意 UP 主主页空间，智能绕过慢速 PCDN、直连官方 UPOS 骨干 CDN 节点（单期 1 秒极速下载，9~12 MB/s）；利用原生 DASH 提取 174kbps 超高清 AAC 音频流（零转码损耗），并自动抓取官方 AI 中文字幕嵌入 `<podcast:transcript>`！
2. **智能保留与存储控制（默认保留最新 15 集）**：
   * 全平台（YouTube / 抖音 / B站）默认统一保留最近 **15 集** 精彩节目，支持自定义 `max_episodes`。
   * 自动清理 R2 历史超出期数音频与字幕，配合 Cloudflare R2 免费额度，零存储成本。
3. **多频道独立分发**：每个频道拥有独立的 RSS 源文件（如 `tim_hurricane.xml`、`geekerwan.xml`、`wangzhian.xml`），在播客客户端中可分别关注，互不串台。
4. **零服务器运行**：完全基于 GitHub Actions + Cloudflare R2，日常运行 0 费用。
5. **本地 iCloud 同步归档**：在本地 PC 上运行时，若系统存在 `X:\`（iCloud Drive），会自动按创作者归档音频，并在手机“文件”App 中即刻收听。
6. **精准定时触发（避开 GitHub Cron 延迟）**：
   * 本项目提供本地 Windows 计划任务脚本，每天准时通过 GitHub REST API 唤醒工作流。
7. **智能容错与自愈**：
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

本项目支持三种平台的频道配置：

#### 1. B站频道：[`bilibili_channels.json`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/bilibili_channels.json)（推荐）
在代码根目录的 `bilibili_channels.json` 中登记需要订阅的 UP 主（默认 `max_episodes: 15` 自动保留最新 15 集）：
```json
[
  {
    "id": "dianyingzuitop",
    "name": "电影最TOP",
    "mid": "17819768",
    "url": "https://space.bilibili.com/17819768",
    "max_episodes": 15,
    "category": "TV & Film",
    "language": "zh-cn",
    "description": "电影最TOP Bilibili 音频播客。专注于优质影视作品深度解说与剖析。",
    "icloud_backup": true,
    "enabled": true
  },
  {
    "id": "tim_hurricane",
    "name": "影视飓风",
    "mid": "946974",
    "url": "https://space.bilibili.com/946974",
    "max_episodes": 15,
    "category": "Technology",
    "language": "zh-cn",
    "description": "影视飓风 Bilibili 音频播客。无限进步！",
    "icloud_backup": true,
    "enabled": true
  }
]
```

#### 2. 抖音频道：[`douyin_channels.json`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/douyin_channels.json)（推荐）
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

#### 3. YouTube 频道：[`channels.json`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/channels.json)（推荐）
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

| Secret 变量名 | 适用平台 | 说明 | 示例 |
| :--- | :--- | :--- | :--- |
| `R2_ACCOUNT_ID` | 通用 | Cloudflare 账户 ID | `a1b2c3d4e5f6...` |
| `R2_ACCESS_KEY_ID` | 通用 | R2 API Access Key ID | `0123456789abcdef...` |
| `R2_SECRET_ACCESS_KEY` | 通用 | R2 API Secret Access Key | `abcdef0123456789...` |
| `R2_BUCKET_NAME` | 通用 | R2 存储桶名称 | `youtuberss` |
| `R2_PUBLIC_URL` | 通用 | R2 公开访问的基础 URL（**末尾不带斜杠**） | `https://podcast.yourdomain.com` |
| `BILI_COOKIES` | B站 | B站登录 Cookie（用于提取 174k 高清音频流） | `buvid3=...; SESSDATA=...; bili_jct=...` |
| `DOUYIN_COOKIE` | 抖音 | 抖音网页端 Cookie（用于请求博主帖子列表接口） | `passport_csrf_token=...; sessionid=...;` |
| `TAILSCALE_AUTH_KEY` | B站 / 抖音 | Tailscale 预授权密钥（格式：`tskey-auth-...`），用于访问国内出口节点 | `tskey-auth-kXXXXXX-XXXXXXXX` |
| `TAILSCALE_EXIT_NODE` | B站 / 抖音 | （可选）Tailscale 出口节点 IP/主机名（默认为家里 NAS） | `100.107.43.2` |
| `TELEGRAM_BOT_TOKEN` | 通用 | （可选）Telegram Bot Token，新单集自动推送手机 | `8955999825:AAFR...` |
| `TELEGRAM_CHAT_ID` | 通用 | （可选）Telegram 接收人 Chat ID | `5584552077` |

---

### 第四步：本地 Windows 计划任务配置（免 GitHub 延迟）

在本地电脑上打开 PowerShell，运行以下命令即可完成本地计划任务注册：

```powershell
# 1. 注册计划任务：准时通过 GitHub API 触发
.\scripts\setup_schedule_task.ps1 -Action Create

# 2. 检查计划任务运行状态
Get-ScheduledTask | Where-Object { $_.TaskName -like "*PodcastSyncTrigger*" } | Select-Object TaskName, State
```

| 任务名称 | 执行时间 | 调度的 GitHub Actions 工作流 |
| :--- | :--- | :--- |
| `YouTubePodcastSyncTrigger` | 每天 `10:00` & `22:00` | `podcast_sync.yml` |
| `DouyinPodcastSyncTrigger` | 每天 `10:30` & `22:30` | `douyin_sync.yml` |
| `BilibiliPodcastSyncTrigger` | 每天 `11:00` & `23:00` | `bilibili_sync.yml` |

---

### 第五步：在苹果播客中添加订阅

每个频道拥有独一无二的 RSS 地址：
```text
https://<你的R2域名>/<channel_id>.xml
```
例如：
* **电影最TOP (B站)**：`https://podcast.yourdomain.com/dianyingzuitop.xml`
* **影视飓风 (B站)**：`https://podcast.yourdomain.com/tim_hurricane.xml`
* **极客湾 (抖音)**：`https://podcast.yourdomain.com/geekerwan.xml`
* **路口大爷 (抖音)**：`https://podcast.yourdomain.com/caijinglukou.xml`
* **王局拍案 (YouTube)**：`https://podcast.yourdomain.com/wangzhian.xml`

**在 Apple Podcasts 客户端中订阅：**
1. 打开 iPhone / iPad / Mac 上的 **“播客 (Podcasts)”** App。
2. 点击底部的 **“资料库 (Library)”**。
3. 点击右上角的 **“...”**（或“编辑”）。
4. 选择 **“通过 URL 关注节目... (Follow a Show by URL...)”**。
5. 粘贴上述 XML 链接并关注即可。

---

## 运维工具与脚本

在 [`scripts/`](file:///c:/Users/JackoMA/Documents/antigravity/fervent-nobel/scripts) 目录下提供了维护小工具：
* **`run_bilibili_sync.py`**：B站播客同步程序，支持 `--dry-run` 和 `--force`。
* **`run_douyin_sync.py`**：抖音播客同步主程序。
* **`scripts/diagnose_tailscale.py`**：Tailscale 连通性探测工具，诊断 Direct 直连 vs DERP 中继、网络延迟与实际代理吞吐量。
* **`scripts/run_bilibili_sync.bat`**：Windows 本地一键运行 B站同步脚本。
* **`scripts/clean_multipart.py`**：检测并强制清理 R2 存储桶中所有滞留的未完成分片上传（已集成在主同步脚本启动项中自动执行）。
* **`scripts/migrate_channel.py`**：多线程服务端直拷工具，用于在 R2 中秒级重命名/迁移频道。
* **`scripts/trigger_podcast_sync.ps1`**：远程 REST API 触发器，支持 `-Workflow bilibili`、`-Workflow douyin`、`-Workflow youtube` 或 `-Workflow all`。
