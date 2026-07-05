# PRD: 大公鸡品牌广告配音系统

> 版本：v1.0  
> 日期：2026-07-05  
> 编写：小KAI  
> 状态：草稿  

---

## 1. 项目背景

### 1.1 业务现状

广州瑞狐文化传媒为大公鸡（重油污清洁剂）品牌在巨量引擎投放信息流广告，日均投放含视频素材。目前视频配音依赖第三方供应商或人工录制口播，存在以下问题：

- 配音成本高：每批素材需外发或找真人录制
- 周期长：来回修改文案 → 配音 → 返工
- 批量困难：10+ 条素材时配音是瓶颈
- 版本管理差：不同投放渠道需不同配音版本，难以快速生成

### 1.2 技术基础

- 阿里云百炼 Qwen-TTS 实时语音合成 API **已验证可用**
- 服务器 8.148.226.250 已有 OpenMontage 视频合成管线（FFmpeg + API 服务）
- 自有素材库：50+ 条大公鸡品牌视频片段
- 投放端：巨量引擎 API（素材上传接口已验证）

### 1.3 目标

建立自动化的广告配音管线，实现**文案录入 → 配音生成 → 视频合成 → 素材输出**全链路自动化。

---

## 2. 产品范围

### 2.1 包含功能

| 模块 | 说明 |
|------|------|
| TTS 引擎服务 | 封装 Qwen-TTS WebSocket 调用，提供稳定 HTTP API |
| 配音生成 | 文本 → 音频文件，支持多音色、多格式 |
| 批量生产 | 多条文案批量配音，带并发控制 |
| 缓存机制 | 相同文案自动复用已生成音频，降低 API 成本 |
| 字幕生成 | 根据文案+语速估算时间轴，输出 SRT/ASS |
| 视频合成管线 | 配音 + 视频素材 → FFmpeg 合成广告视频 |
| Web 管理面板 | 文案输入、配音试听、批量任务管理 |
| 巨量引擎对接 | 导出素材 → 自动上传到巨量引擎素材库 |

### 2.2 不含功能

- 视频素材的 AI 生成（由 OpenMontage / Seedance 处理）
- 智能文案写作（只负责文案到配音的转换）
- 广告投放策略（由 OpenFox / 投放人员在巨量引擎平台完成）

---

## 3. 用户角色

| 角色 | 描述 | 核心需求 |
|------|------|----------|
| 投放专员 | 负责广告素材制作和投放 | 快速生成配音，操作简单，批量生产 |
| 运营主管 | 管理素材方向和品牌调性 | 音色统一、历史可查、素材质量可控 |
| 开发（我） | 维护系统运行 | API 稳定、有监控、易于扩展 |

---

## 4. 功能需求

### 4.1 TTS 引擎服务（核心）

#### 4.1.1 文本 → 音频

- **输入**：文案文本（1-500字）
- **输出**：音频文件（mp3 / wav / pcm）
- **参数**：

| 参数 | 类型 | 默认 | 可选值 |
|------|------|------|--------|
| text | string | 必填 | 1-500 字 |
| voice | string | Cherry | Cherry, Amber, Coco, Nova 等 |
| format | string | mp3 | mp3, wav, pcm |
| sample_rate | number | 24000 | 16000, 24000 |
| speed | number | 1.0 | 0.5-2.0（需额外处理） |
| mode | string | commit | commit, server_commit |

#### 4.1.2 缓存

- 以文案文本的 MD5 + voice + format 为缓存 key
- 缓存目录：`/var/tts-cache/`
- 相同文案直接返回原文件，无额外 API 调用
- 缓存支持手动清除

#### 4.1.3 并发与重连

- 最多 3 个并发 WebSocket 连接
- 连接断开自动重试（最多 3 次）
- 单次超时 30s

#### 4.1.4 API 接口

```
POST /api/tts/generate
Content-Type: application/json

{
  "text": "厨房油污太难洗？试试大公鸡重油污清洁剂，一喷一擦，油污全不见。",
  "voice": "Cherry",
  "format": "mp3",
  "sample_rate": 24000
}

Response 200:
{
  "success": true,
  "data": {
    "file_path": "/var/tts-cache/ab12cd34.mp3",
    "file_url": "http://8.148.226.250/tts/ab12cd34.mp3",
    "duration": 4.2,
    "text_hash": "ab12cd34ef56",
    "cached": false
  }
}

Response 400:
{
  "success": false,
  "error": "text 不能为空"
}

Response 502:
{
  "success": false,
  "error": "TTS 服务连接失败，已重试 3 次"
}
```

```
POST /api/tts/batch
Content-Type: application/json

{
  "items": [
    {
      "id": "ad_001",
      "text": "厨房油污太难洗？试试大公鸡重油污清洁剂。",
      "voice": "Cherry",
      "format": "mp3"
    },
    {
      "id": "ad_002",
      "text": "大公鸡一喷去油，不伤手不刺鼻。",
      "voice": "Cherry",
      "format": "mp3"
    }
  ],
  "concurrent": 2
}

Response 200:
{
  "success": true,
  "data": {
    "batch_id": "batch_20260705_001",
    "total": 2,
    "status": "processing"
  }
}
```

```
GET /api/tts/batch/:batch_id

Response 200:
{
  "success": true,
  "data": {
    "batch_id": "batch_20260705_001",
    "status": "done",          // pending / processing / done / partial_failed
    "total": 2,
    "done": 2,
    "failed": 0,
    "items": [
      {
        "id": "ad_001",
        "file_url": "http://8.148.226.250/tts/ab12cd34.mp3",
        "duration": 4.2,
        "status": "done"
      },
      {
        "id": "ad_002",
        "file_url": "http://8.148.226.250/tts/ef56ab78.mp3",
        "duration": 3.8,
        "status": "done"
      }
    ]
  }
}
```

#### 4.1.5 PHP SDK 调用示例

```php
<?php
// 在你的 PHP 项目中使用
function tts_generate($text, $voice = 'Cherry') {
    $ch = curl_init('http://localhost:3721/api/tts/generate');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => json_encode([
            'text' => $text,
            'voice' => $voice,
            'format' => 'mp3'
        ]),
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 30,
    ]);
    $res = json_decode(curl_exec($ch), true);
    curl_close($ch);
    return $res['data']['file_url'] ?? null;
}

$url = tts_generate('大公鸡一喷去油，不伤手不刺鼻。');
// → "http://8.148.226.250/tts/ab12cd34.mp3"
?>
```

### 4.2 视频合成管线

#### 4.2.1 自动配音 → 视频

接收配音音频 + 视频片段 + 字幕，输出最终广告视频。

```
POST /api/video/combine

{
  "audio_url": "http://8.148.226.250/tts/ab12cd34.mp3",
  "clips": [
    { "path": "/media/dajigong/raw/clip_1.mp4", "start": 0, "duration": 3 },
    { "path": "/media/dajigong/raw/clip_2.mp4", "start": 0, "duration": 2 },
    { "path": "/media/dajigong/raw/clip_4_clean.mp4", "start": 0, "duration": 2 },
    { "path": "/media/dajigong/templates/script_01.mp4", "start": 0, "duration": 2 }
  ],
  "subtitle_text": "厨房油污太难洗？试试大公鸡重油污清洁剂。",
  "output_format": "mp4",
  "watermark": false
}

Response:
{
  "success": true,
  "data": {
    "file_url": "http://8.148.226.250/output/ad_20260705_001.mp4",
    "duration": 9.0,
    "file_size": 2400000
  }
}
```

#### 4.2.2 9s 广告模板管线

基于已验证的广告结构自动合成：

| 阶段 | 内容 | 时长 | 素材来源 |
|------|------|------|----------|
| Hook | 痛点/问题引入 | 3.0s | clip_1（油污场景） |
| Product | 产品出现+功能介绍 | 1.9s | 产品特写素材 |
| Demo | 使用演示 | 1.1s | clip_4_clean（喷+擦） |
| Benefit | 效果展示 | 1.4s | clip_2（对比效果） |
| CTA | 行动号召 | 1.6s | 模板脚本04 |

#### 4.2.3 字幕生成

根据文案字数和语速（默认 4 字/秒）估算时间轴：

```
输入："厨房油污太难洗？试试大公鸡重油污清洁剂。一喷一擦，油污全不见。"
      （23字，约 5.8s）

输出 SRT:
1
00:00:00,000 --> 00:00:02,750
厨房油污太难洗？
试试大公鸡重油污清洁剂。

2
00:00:02,750 --> 00:00:05,800
一喷一擦，油污全不见。
```

字幕样式：底部居中，白字黑边（白色 #FFFFFF，黑色描边），字号适配竖屏 1080×1920。

### 4.3 Web 管理面板

#### 4.3.1 配音生成页

- 文案输入框（200 字以内，显示字数统计）
- 音色下拉选择器
- 试听按钮 → 直接播放
- 下载按钮 → 下载 mp3
- "加入批量"按钮 → 暂存到批量任务列表

#### 4.3.2 批量生产页

- 批量文案列表（textarea / CSV 上传）
- 每行一条文案，支持格式化 `文案标题: 文案内容`
- 音色选择（统一或逐条设置）
- "开始批量"按钮 → 异步执行
- 实时进度条（总数/已完成/失败数）
- 完成后列表显示每个文件的试听+下载+视频合成入口

#### 4.3.3 视频合成页

- 选择配音文件
- 选择视频素材（支持拖拽排序）
- 字幕开关 + 字幕样式预览
- 合成按钮
- 预览 + 下载

#### 4.3.4 历史记录

- 按日期筛选
- 显示文案摘要、音色、时长、状态
- 支持重新下载和重新合成
- 搜索

#### 4.3.5 设置

- 缓存管理（查看缓存大小、清除缓存）
- 默认音色
- TTS 连接状态

### 4.4 巨量引擎素材对接（后续）

- 从视频合成页面直接 "投放至巨量引擎"
- 调用素材上传 API，返回 material_id
- 记录到素材管理表

---

## 5. 技术方案

### 5.1 架构图

```
┌─────────────────────────────────────────────────────────┐
│  Nginx (8.148.226.250:80/443)                           │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │ Web 管理面板 (Vue)    │  │ 静态资源 /tts/ /output/  │ │
│  └──────────┬───────────┘  └──────────────────────────┘ │
└─────────────┼───────────────────────────────────────────┘
              │ proxy_pass /api/tts/*  →  :3721
              │ proxy_pass /api/video/* →  :8000
              │
┌─────────────▼───────────────────────────────────────────┐
│  TTS Service (Node.js, :3721)                           │
│  - Express HTTP 服务                                      │
│  - WebSocket 管理器（长连接池）                              │
│  - 缓存模块（文件系统）                                      │
│  - 批量任务队列（内存队列）                                    │
│  - 字幕生成器                                              │
└─────────────┬───────────────────────────────────────────┘
              │ WebSocket
┌─────────────▼───────────────────────────────────────────┐
│  阿里云百炼 Qwen-TTS                                     │
│  wss://ws-radpbntajt3ag6ua.cn-beijing.maas.aliyuncs.com  │
│  /api-ws/v1/realtime?model=qwen3-tts-flash-realtime     │
└─────────────────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────┐
│  OpenMontage API (:8000) + FFmpeg                        │
│  视频合成渲染引擎                                          │
└─────────────────────────────────────────────────────────┘
```

### 5.2 技术选型

| 层 | 技术 | 理由 |
|----|------|------|
| TTS 服务 | Node.js | WebSocket 原生支持，异步非阻塞适合长连接 |
| HTTP 框架 | Express | 轻量，你服务器已有运行环境 |
| Web 面板 | Vue 3 (CDN) | 无构建步骤，单 HTML 文件 |
| API 服务 | Nginx 反向代理 | 已有，统一域名和端口 |
| 缓存 | 文件系统 | 轻量，无额外依赖 |
| 视频渲染 | FFmpeg + OpenMontage | 已有，验证过 |

### 5.3 部署位置

| 资产 | 路径 |
|------|------|
| TTS 服务 | `/home/admin/tts-service/` |
| 音频缓存 | `/var/tts-cache/` |
| 输出视频 | `/var/www/html/tts-output/` |
| Web 面板 | `/var/www/html/tts-admin/` |
| Systemd 服务 | `tts-service.service` |

### 5.4 端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| TTS API | 3721 | Nginx 反向代理 /api/tts/* |
| OpenMontage | 8000 | 已有，/api/tts/combine 调此引擎 |
| Nginx | 80/443 | 已有，统一入口 |

### 5.5 错误处理

```
TTS 服务错误码：

- TTS_WS_CONNECT_FAIL  → WebSocket 连接失败（重试 3 次后报错）
- TTS_WS_AUTH_FAIL     → API Key 鉴权失败（检查 key 权限和地域）
- TTS_TEXT_TOO_LONG    → 文案超过 500 字限制
- TTS_RESPONSE_TIMEOUT → 服务端 30s 未响应
- TTS_AUDIO_TOO_SHORT  → 生成音频过短（<0.5s，可能文案无效）
- TTS_CACHE_FULL       → 缓存目录超过 2GB
```

---

## 6. 性能要求

| 指标 | 目标 |
|------|------|
| 单次配音响应 | < 5s（含缓存命中 < 100ms） |
| 批量 10 条配音 | < 30s |
| 9s 视频合成 | < 15s |
| 并发处理 | 支持 2 个批量同时运行 |
| 缓存上限 | 2GB，超过自动清理最旧文件 |
| TTS 服务可用性 | > 99%（阿里云 API 稳定性 + 本地故障转移） |

---

## 7. 验证标准

### 7.1 配音质量

- 自然度：无明显机械感，停顿合理
- 准确度：多音字可配置，数字正确朗读
- 一致性：同音色下多次生成音质统一

### 7.2 投放能力

- 合成视频可正常上传至巨量引擎（格式/码率合规）
- 音频清晰度满足投放审核要求
- 配音+画面无不同步

---

## 8. 实施计划

| 阶段 | 内容 | 预计工时 | 交付物 |
|------|------|----------|--------|
| P0 | TTS 服务（Node.js HTTP API + 缓存 + 批量） | 2h | 服务部署，PHP 可调 |
| P1 | 字幕生成 + 视频合成管线 | 3h | 配音→字幕→视频全流程 |
| P2 | Web 管理面板 | 3h | 可视化操作界面 |
| P3 | 巨量引擎对接 | 2h | 一键投放 |

---

## 9. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| API Key 过期或额度用完 | 低 | 高 | 缓存兜底 + 飞书告警 |
| 阿里云 TTS 模型变更 | 低 | 中 | 参数配置化，快速切换 |
| 服务器资源不足 | 中 | 中 | 控制在 2 并发，避免同时跑渲染 |
| 配音效果不达标 | 低 | 中 | 预置多音色供选择+试听 |

---

## 10. 附录

### 10.1 Qwen-TTS 已确认参数

```
URL:  wss://ws-radpbntajt3ag6ua.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime
Auth: Authorization: Bearer sk-ws-xxx
Mode: commit / server_commit
格式: mp3 / wav / pcm
音色: Cherry 已验证可用，其他音色待测试
```

### 10.2 参考文档

- [阿里云 Qwen-TTS 实时语音合成用户指南](https://help.aliyun.com/zh/model-studio/realtime-tts-user-guide)
- [Qwen-TTS 客户端事件](https://help.aliyun.com/zh/model-studio/qwen-tts-realtime-client-events)
- [Qwen-TTS 服务端事件](https://help.aliyun.com/zh/model-studio/qwen-tts-realtime-server-events)
