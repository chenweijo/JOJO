# 大公鸡清洁剂广告自动混剪管线

基于 Qwen-VL + TTS + Embedding 的全自动广告视频混剪系统。

## 文件说明

| 文件 | 说明 |
|---|---|
| `pipeline_v3.py` | 主力管线：ffmpeg场景检测 → Qwen-VL画面描述 → Embedding匹配 → TTS配音 → ffmpeg合成 |
| `pipeline_v2.py` | v2 版本：关键词匹配代替 embedding |
| `omni_analyse_v3.py` | 精细画面分析（每0.5s一个点） |
| `omni_v3.py` | Omni分析输出HTML格式化 |
| `cosy_tts_cli.py` | CosyVoice TTS 单句生成 |
| `PRD-QwenTTS配音系统.md` | 需求文档 |

## 流程

1. ffmpeg 场景检测切分镜头（threshold=0.3）
2. Qwen-VL/qwen-vl-max 描述每段首帧
3. text-embedding-v3 转语义向量
4. 余弦相似度匹配文案与画面
5. CosyVoice TTS 配音（带情绪递进）
6. ffmpeg 合成 + bgm 混音

## 依赖

- Python 3.10+
- dashscope SDK
- ffmpeg
- 密钥文件 `/home/admin/.secrets/tts_key.b64`

## 使用

```bash
python3 pipeline_v3.py "文案1|文案2|文案3..."
```
