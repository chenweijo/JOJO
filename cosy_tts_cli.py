#!/usr/bin/env python3
"""
大公鸡品牌 · CosyVoice 配音生成工具 (完整版 v2)
基于阿里百炼 DashScope Python SDK

用法:
  python cosy_tts_cli.py -v longanyang -t "文案" -o out.mp3
  python cosy_tts_cli.py -v longanyang -t "文案" -o out.mp3 -i promo_happy
  python cosy_tts_cli.py -v longanyang -t "文案" -o out.mp3 -i promo_happy -sr 1.3 -pr 1.1 -vol 70
  python cosy_tts_cli.py -v longanyang -t "文案" -o out.mp3 --timestamps out.srt     ← SRT字幕(剪映可用)
  python cosy_tts_cli.py -v longanyang -t "文案" -o out.mp3 --timestamps out.json    ← JSON原始时间戳
  python cosy_tts_cli.py --segments segments.json -o final.mp3 --timestamps final.srt
  python cosy_tts_cli.py --interactive
  python cosy_tts_cli.py --list

依赖:
  pip install dashscope websocket-client

API Key 设置:
  1. 环境变量 DASHSCOPE_API_KEY
  2. 同级 .env 文件: DASHSCOPE_API_KEY=***
"""

import os, sys, json, time, uuid, argparse, subprocess, threading
from pathlib import Path

# ============================================================
# 配置
# ============================================================

AVAILABLE_VOICES = {
    "longanyang":  "龙安洋 - 阳光大男孩",
    "longanhuan":  "龙安欢 - 欢脱元气女",
    "longanran":   "龙安燃 - 活泼质感女",
    "longanxuan":  "龙安宣 - 经典直播女",
    "longyingxun": "龙应询 - 年轻青涩男",
    "longyingjing":"龙应静 - 低调冷静女",
    "longyingling":"龙应聆 - 温和共情女",
    "longyingtao": "龙应桃 - 温柔淡定女",
    "longxiaochun":"龙小淳 - 知性积极女",
    "longxiaoxia": "龙小夏 - 沉稳权威女",
    "longanyun":   "龙安昀 - 居家暖男",
    "longanwen":   "龙安温 - 优雅知性女",
    "longanli":    "龙安莉 - 利落从容女",
    "longtian":    "龙天 - 磁性理智男",
    "longwan":     "龙婉 - 细腻柔声女",
    "longze":      "龙泽 - 温暖元气男",
    "longcheng":   "龙橙 - 智慧青年男",
    "longyan":     "龙颜 - 温暖春风女",
    "longhao":     "龙浩 - 多情忧郁男",
    "longanrou":   "龙安柔 - 温柔闺蜜女",
    "longsanshu":  "龙三叔 - 沉稳质感男",
    "longyuan":    "龙媛 - 温暖治愈女",
    "longyue":     "龙悦 - 温暖磁性女",
    "longyichen":  "龙逸尘 - 洒脱活力男",
}

INSTRUCT_PRESETS = {
    "neutral":     "你说话的情感是neutral。",
    "sad":         "你说话的情感是sad。",
    "happy":       "你说话的情感是happy。",
    "surprised":   "你说话的情感是surprised。",
    "angry":       "你说话的情感是angry。",
    "fearful":     "你说话的情感是fearful。",
    "disgusted":   "你说话的情感是disgusted。",
    "promo_happy":      "你正在进行广告促销，你说话的情感是happy。",
    "promo_surprised":  "你正在进行广告促销，你说话的情感是surprised。",
    "chat_neutral":     "你正在进行闲聊互动，你说话的情感是neutral。",
    "chat_happy":       "你正在进行闲聊互动，你说话的情感是happy。",
    "news_neutral":     "你正在进行新闻播报，你说话的情感是neutral。",
    "show_neutral":     "你正在进行脱口秀表演，你说话的情感是neutral。",
}

EMOTION_CURVE = {
    "pain":     "sad",
    "solution": "promo_happy",
    "trust":    "chat_neutral",
    "cta":      "promo_surprised",
}

# ============================================================
# API Key
# ============================================================

def get_api_key():
    key = os.environ.get("DASHSCOPE_API_KEY")
    if key:
        return key
    for base in [Path.cwd(), Path(__file__).parent]:
        env_file = base / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DASHSCOPE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return None

# ============================================================
# TimestampData
# ============================================================

class TimestampData:
    def __init__(self, text, begin_time_ms, end_time_ms):
        self.text = text
        self.begin_time = begin_time_ms
        self.end_time = end_time_ms
    def to_dict(self):
        return {"text": self.text, "begin_time": self.begin_time, "end_time": self.end_time}

def ms_to_srt_time(ms):
    s = ms // 1000
    ms_remain = ms % 1000
    m = s // 60
    s = s % 60
    h = m // 60
    m = m % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms_remain:03d}"

def export_json(all_words, output_path, segment_labels=None):
    data = {
        "total_words": len(all_words),
        "total_duration_ms": all_words[-1].end_time if all_words else 0,
        "words": [w.to_dict() for w in all_words],
    }
    if segment_labels:
        data["segments"] = segment_labels
    Path(output_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📄 JSON: {output_path}  ({len(all_words)} 字)")

def export_srt(all_words, output_path, chars_per_line=5):
    lines = []
    idx = 1
    for i in range(0, len(all_words), chars_per_line):
        chunk = all_words[i:i+chars_per_line]
        start = chunk[0].begin_time
        end = chunk[-1].end_time
        text = "".join(w.text for w in chunk)
        lines.append(str(idx))
        lines.append(f"{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}")
        lines.append(text)
        lines.append("")
        idx += 1
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  📄 SRT: {output_path}  ({idx-1} 条字幕)")

def export_markers(all_words, output_path, fps=30):
    markers = []
    for w in all_words:
        frame = int(w.begin_time / 1000 * fps)
        markers.append(f"{frame}\t{w.text}")
    Path(output_path).write_text("\n".join(markers), encoding="utf-8")
    print(f"  📄 Markers: {output_path}  ({len(markers)} 标记)")

# ============================================================
# 原生 WebSocket 调用（带时间戳）
# ============================================================

WORKSPACE_ID = "ws-radpbntajt3ag6ua"
WS_URL = f"wss://{WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"

def generate_with_timestamps(voice, text, instruct=None, model="cosyvoice-v3-flash",
                              speech_rate=None, pitch_rate=None, volume=None):
    """
    调用 CosyVoice 原生 WebSocket 生成音频 + 获取字级别时间戳
    返回: (audio_bytes, [TimestampData])
    """
    import websocket as _ws
    
    api_key = get_api_key()
    if not api_key:
        print("❌ 未找到 API Key")
        return None, []

    task_id = uuid.uuid4().hex[:32]
    audio_chunks = []
    collected_words = []
    done = [False]
    ws_ref = [None]

    def on_open(ws):
        ws_ref[0] = ws
        params = {
            "voice": voice,
            "volume": volume if volume is not None else 50,
            "text_type": "PlainText",
            "sample_rate": 22050,
            "rate": speech_rate if speech_rate is not None else 1.0,
            "format": "mp3",
            "pitch": pitch_rate if pitch_rate is not None else 1.0,
            "word_timestamp_enabled": True,
        }
        if instruct:
            params["instruction"] = INSTRUCT_PRESETS.get(instruct, instruct)

        cmd = json.dumps({
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "model": model,
                "task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
                "input": {"text": text},
                "parameters": params,
            }
        })
        ws.send(cmd)

    _last_audio = [0.0]
    def on_msg(ws, msg):
        if isinstance(msg, (bytes, bytearray)):
            audio_chunks.append(msg)
            _last_audio[0] = time.time()
            return

        data = json.loads(msg)
        event = data.get("header", {}).get("event", "?")

        if event == "result-generated":
            output = data.get("payload", {}).get("output", "")
            if isinstance(output, str):
                obj = json.loads(output)
            elif isinstance(output, dict):
                obj = output
            else:
                return
            for w in obj.get("sentence", {}).get("words", []):
                if w.get("text"):
                    exists = any(x["text"] == w["text"] and x["begin_time"] == w["begin_time"]
                                 for x in collected_words)
                    if not exists:
                        collected_words.append(w)
        elif event == "task-finished":
            done[0] = True
            ws.close()

    ws = _ws.WebSocketApp(WS_URL,
        header=[
            f"Authorization: Bearer {api_key}",
            f"X-DashScope-WorkSpace: {WORKSPACE_ID}",
            "User-Agent: tts-client/1.0",
        ],
        on_open=on_open, on_message=on_msg)

    t = threading.Thread(target=lambda: ws.run_forever(ping_interval=10, ping_timeout=5), daemon=True)
    t.start()

    # 等待音频接收完成（首次数据后2秒无新数据）
    timeout_t = time.time() + 25
    while time.time() < timeout_t:
        if not audio_chunks:
            time.sleep(0.3)
            continue
        if time.time() - _last_audio[0] >= 2.0:
            break
        time.sleep(0.1)

    # 发送 finish-task (仅当连接还开着)
    if ws_ref[0] and not done[0]:
        try:
            ws_ref[0].send(json.dumps({
                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {}}
            }))
            time.sleep(2)
        except:
            pass
    try:
        ws.close()
    except:
        pass

    collected_words.sort(key=lambda x: x["begin_time"])
    word_data = [TimestampData(w["text"], w["begin_time"], w["end_time"]) for w in collected_words]
    audio = b"".join(audio_chunks) if audio_chunks else None
    return audio, word_data

# ============================================================
# 简单 SDK 模式（无时间戳）
# ============================================================

def generate(voice, text, instruct=None, model="cosyvoice-v3-flash",
             speech_rate=None, pitch_rate=None, volume=None):
    try:
        from dashscope.audio.tts_v2 import SpeechSynthesizer
    except ImportError:
        print("❌ 请先安装 dashscope: pip install dashscope")
        return None

    kwargs = {"voice": voice, "model": model}
    if speech_rate is not None: kwargs["speech_rate"] = speech_rate
    if pitch_rate is not None: kwargs["pitch_rate"] = pitch_rate
    if volume is not None: kwargs["volume"] = volume
    if instruct:
        inst_text = INSTRUCT_PRESETS.get(instruct, instruct)
        kwargs["instruction"] = inst_text
        print(f"  📢 指令: {inst_text}")

    print(f"  🎤 合成中 ({voice})...")
    syn = SpeechSynthesizer(**kwargs)
    audio = syn.call(text)
    if not audio:
        print("  ❌ 生成失败: 为空")
        return None
    return audio

# ============================================================
# 统一生成 & 保存
# ============================================================

def generate_and_save(voice, text, out_path, instruct=None, model="cosyvoice-v3-flash",
                      speech_rate=None, pitch_rate=None, volume=None, timestamps_out=None):
    if timestamps_out:
        print(f"  🎤 合成中 ({voice}) [带时间戳]...")
        audio, words = generate_with_timestamps(
            voice, text, instruct, model, speech_rate, pitch_rate, volume)
        if not audio:
            return False, []
    else:
        audio = generate(voice, text, instruct, model, speech_rate, pitch_rate, volume)
        words = []

    if not audio:
        return False, []

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(audio)

    dur_str = ""
    try:
        dur = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", out_path]).decode().strip()
        dur_str = f", {dur}s"
    except: pass

    print(f"  ✅ {out_path}  ({len(audio)//1024} KB{dur_str})")
    return True, words

# ============================================================
# 分段生成 + 合并 + 时间戳
# ============================================================

def generate_segments(segments, output_path, model="cosyvoice-v3-flash",
                      timestamps_out=None, fps=30):
    temp_files = []
    all_words = []
    segment_labels = []
    total_offset_ms = 0

    for i, seg in enumerate(segments):
        label = seg.get("label", f"seg_{i}")
        voice = seg.get("voice", "longanyang")
        instruct = seg.get("instruct")
        text = seg["text"]
        sr = seg.get("speech_rate")
        pr = seg.get("pitch_rate")
        vol = seg.get("volume")

        print(f"\n--- 段{i+1}: {label} ---")
        temp = f"/tmp/tts_seg_{i}_{int(time.time()*1000)}.mp3"

        ok, words = generate_and_save(
            voice, text, temp, instruct, model, sr, pr, vol,
            timestamps_out=timestamps_out)

        if not ok:
            print(f"  ❌ 跳过")
            continue

        temp_files.append(temp)
        for w in words:
            w.begin_time += total_offset_ms
            w.end_time += total_offset_ms
        all_words.extend(words)
        if words:
            seg_end = words[-1].end_time
            segment_labels.append({
                "label": label,
                "start_ms": total_offset_ms,
                "end_ms": seg_end,
            })
            total_offset_ms = seg_end

    if not temp_files:
        print("❌ 全失败")
        return False

    print(f"\n--- 合并 {len(temp_files)} 段 ---")
    list_file = f"/tmp/tts_concat_{int(time.time())}.txt"
    with open(list_file, "w") as f:
        for tf in temp_files:
            f.write(f"file '{tf}'\n")

    try:
        subprocess.check_output([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", output_path], stderr=subprocess.DEVNULL)
        dur = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", output_path]).decode().strip()
        sz = os.path.getsize(output_path) // 1024
        print(f"✅ {output_path}  ({sz} KB, {dur}s)")

        if timestamps_out and all_words:
            _export(all_words, timestamps_out, segment_labels, fps)

        for tf in temp_files: Path(tf).unlink(missing_ok=True)
        Path(list_file).unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"❌ 合并失败: {e}")
        return False

def _export(all_words, timestamps_out, segment_labels=None, fps=30):
    p = str(timestamps_out)
    if p.endswith(".srt"): export_srt(all_words, p)
    elif p.endswith(".json"): export_json(all_words, p, segment_labels)
    elif p.endswith(".csv"): export_markers(all_words, p, fps)
    else:
        export_json(all_words, p, segment_labels)
    print(f"  🎯 {len(all_words)} 字, {all_words[-1].end_time/1000:.2f}s" if all_words else "")

# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        description="大公鸡 · CosyVoice 配音 (带字级别时间戳)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""使用示例:
  python cosy_tts_cli.py -t "文案" -v longanyang -o out.mp3
  python cosy_tts_cli.py -t "文案" -v longanyang -o out.mp3 -i promo_happy -sr 1.3
  python cosy_tts_cli.py -t "文案" -v longanyang -o out.mp3 --timestamps out.srt
  python cosy_tts_cli.py --segments seg.json -o final.mp3 --timestamps final.srt
  python cosy_tts_cli.py --list""")
    p.add_argument("-v", "--voice", default="longanyang")
    p.add_argument("-t", "--text")
    p.add_argument("-o", "--out", default="output.mp3")
    p.add_argument("-i", "--instruct")
    p.add_argument("-sr", "--speech-rate", type=float)
    p.add_argument("-pr", "--pitch-rate", type=float)
    p.add_argument("-vol", "--volume", type=int)
    p.add_argument("--model", default="cosyvoice-v3-flash")
    p.add_argument("--list", action="store_true")
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--segments")
    p.add_argument("--emotion-curve")
    p.add_argument("--timestamps", "-ts")
    p.add_argument("--fps", type=int, default=30)
    return p

def list_all():
    print("\n=== 音色 ===")
    for v, d in AVAILABLE_VOICES.items(): print(f"  {v:20s} {d}")
    print("\n=== 指令预设 ===")
    for k, v in INSTRUCT_PRESETS.items(): print(f"  {k:20s} ↳ {v}")
    print("\n=== 情绪曲线 ===")
    for k, v in EMOTION_CURVE.items(): print(f"  {k:10s} → {v}")
    print("\n=== 参数 ===")
    print("  -sr/--speech-rate   0.5~2.0 (语速)")
    print("  -pr/--pitch-rate    0.5~2.0 (音调)")
    print("  -vol/--volume       0~100 (音量)")
    print("\n=== 时间戳输出 ===")
    print("  --timestamps .srt  剪映/PR 字幕")
    print("  --timestamps .json 原始每字时间戳")
    print("  --timestamps .csv  帧标记 (FPS 默认 30)")

def main():
    args = build_parser().parse_args()

    if not get_api_key():
        print("❌ 未设置 DASHSCOPE_API_KEY")
        sys.exit(1)

    if args.list: list_all(); return
    if args.interactive: interactive(); return

    if args.segments or args.emotion_curve:
        source = args.segments or args.emotion_curve
        data = json.loads(Path(source).read_text(encoding="utf-8"))
        if args.segments:
            segs = data.get("segments", data if isinstance(data, list) else [])
        else:
            dv = data.get("voice", args.voice)
            segs = data.get("segments", [])
            for s in segs:
                s.setdefault("voice", dv)
                if s.get("mood"):
                    s["instruct"] = EMOTION_CURVE.get(s["mood"], s.get("instruct"))
        generate_segments(segs, args.out, args.model, args.timestamps, args.fps)
        return

    if not args.text:
        print("❌ 需要 --text")
        sys.exit(1)

    ok, words = generate_and_save(
        args.voice, args.text, args.out, args.instruct, args.model,
        args.speech_rate, args.pitch_rate, args.volume, args.timestamps)

    if ok and words and args.timestamps:
        _export(words, args.timestamps, fps=args.fps)

def interactive():
    import websocket as _ws
    voice, instruct, text, use_ts = "longanyang", None, "", False
    print("\n大公鸡 · CosyVoice 交互模式\n")
    while True:
        print(f"\n🎤 {voice} | 指令:{instruct or '无'} | 文本:{len(text)}字 | TS:{'开' if use_ts else '关'}")
        cmd = input("> ").strip()
        if cmd in ("q","quit","exit"): break
        if cmd == "list": list_all(); continue
        if cmd == "ts": use_ts = not use_ts; continue
        if cmd == "text":
            print("  输入 (空行结束):")
            lines = []
            while True:
                l = input("  ")
                if not l: break
                lines.append(l)
            text = "\n".join(lines)
            continue
        if cmd.startswith("voice="):
            v = cmd.split("=",1)[1].strip()
            if v in AVAILABLE_VOICES: voice = v
            continue
        if cmd.startswith("instruct="):
            v = cmd.split("=",1)[1].strip()
            instruct = v if v and v != "none" else None
            continue
        if cmd == "go":
            if not text: print("  先输入文本"); continue
            ts = int(time.time())
            out = f"tts_{voice}_{ts}.mp3"
            to = f"tts_{voice}_{ts}.srt" if use_ts else None
            generate_and_save(voice, text, out, instruct, timestamps_out=to)
            continue
        print("  voice=xxx | instruct=xxx | text | ts | go | list | q")

if __name__ == "__main__":
    main()
