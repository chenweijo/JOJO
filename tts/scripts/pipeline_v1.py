#!/usr/bin/env python3
"""
大公鸡广告全自动混剪管线 v1

流程：
1. ffmpeg 场景检测 → 切分素材为多个镜头段
2. Qwen-VL 描述每段首帧画面
3. TTS 生成配音 + 逐字时间戳
4. 用 embedding 匹配每句文案→最优画面段
5. ffmpeg 截取→拼接→混音→成品

用法：
  python3 pipeline_v1.py "文案1|文案2|文案3" --素材 素材1.mp4 素材2.mp4 ...
"""
import os, sys, json, base64, time, subprocess, re, shutil
import argparse
from pathlib import Path

# ============ 配置 ============
WORKSPACE = "/home/admin/.openclaw/workspace/media/dajigong"
RAW_DIR = f"{WORKSPACE}/raw"
OUT_DIR = f"{WORKSPACE}/output/pipeline_v1"
TMP_DIR = "/tmp/dajigong_pipeline"

API_KEY = ""  # 会在下面读
# 读 key
try:
    f = open('/home/admin/.secrets/tts_key.b64').read().strip()
    API_KEY = base64.b64decode(f.encode() if not f.startswith('sk') else f.encode()).decode().strip() if f.startswith('sk') else base64.b64decode(f).decode().strip()
except:
    pass

os.environ['DASHSCOPE_API_KEY'] = API_KEY

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

# ============ 步骤1: ffmpeg 场景检测 ============
def detect_scenes(video_path, threshold=0.3):
    """用 ffmpeg 检测镜头切换点，返回镜头列表 [(start, end), ...]"""
    print(f"  场景检测: {Path(video_path).name} (threshold={threshold})", flush=True)
    
    # 先获取总时长
    dur = float(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video_path],
        capture_output=True, text=True).stdout.strip())
    
    # 场景检测
    result = subprocess.run(
        ['ffmpeg', '-i', video_path,
         '-vf', f"select='gt(scene,{threshold})',showinfo",
         '-vsync', 'vfr', '-f', 'null', '-'],
        capture_output=True, text=True, timeout=30)
    
    # 解析 pts_time
    times = [0.0]
    for line in result.stderr.split('\n'):
        m = re.search(r'pts_time:([\d.]+)', line)
        if m:
            t = float(m.group(1))
            if t > 0 and t < dur - 0.1:  # 过滤结尾
                times.append(t)
    times.append(dur)
    
    # 合并太短的片段 (< 1s)
    segments = []
    seg_start = times[0]
    for t in times[1:]:
        if t - seg_start >= 1.0:
            segments.append((seg_start, t))
            seg_start = t
        # 太短就合并到前一段
    
    # 如果最后一段太短，合并到前一段
    if segments and segments[-1][1] - segments[-1][0] < 1.0 and len(segments) > 1:
        prev = segments[-2]
        segments[-1] = (prev[0], segments[-1][1])
        segments.pop(-2)
    
    # 如果只有一段（没检测到场景变化），强制分
    if len(segments) <= 1 and dur > 2:
        segments = [(0, min(2, dur)), (min(2, dur), dur)]
    
    print(f"    检测到 {len(segments)} 个镜头段", flush=True)
    for i, (s, e) in enumerate(segments):
        print(f"      seg{i}: {s:.2f}-{e:.2f}s ({(e-s):.2f}s)", flush=True)
    
    return segments

def extract_segment(video_path, seg_idx, start, end, out_path):
    """截取一个镜头段"""
    dur = end - start
    subprocess.run([
        'ffmpeg', '-y', '-ss', str(start), '-i', video_path, '-t', str(dur),
        '-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p', '-an',
        '-preset', 'fast', out_path
    ], capture_output=True, timeout=30)
    return out_path

def extract_keyframe(video_path, timestamp, out_path):
    """提取指定时间点的帧"""
    subprocess.run([
        'ffmpeg', '-y', '-ss', str(timestamp), '-i', video_path,
        '-vframes', '1', '-q:v', '2', out_path
    ], capture_output=True, timeout=10)
    return out_path

# ============ 步骤2: Qwen-VL 描述画面 ============
def describe_frame(image_path):
    """用 Qwen-VL（qwen-vl-max）描述一帧画面"""
    try:
        from dashscope import MultiModalConversation
    except:
        print("    请安装 dashscope: pip install dashscope", flush=True)
        return "画面描述"
    
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    
    messages = [
        {"role": "user", "content": [
            {"image": f"data:image/jpeg;base64,{b64}"},
            {"text": """描述这个画面，只输出以下字段（JSON格式）：
{
  "scene": "场景名称（如"不锈钢台面油垢特写"）",
  "objects": ["画面中的所有物体，含颜色和材质"],
  "text": "画面中的文字内容（没有则为空）",
  "action": "正在发生什么",
  "suitable_for": ["痛点","产品展示","使用演示","效果对比","CTA"]
}
直接输出JSON，不要其他文字。"""}
        ]}
    ]
    
    try:
        response = MultiModalConversation.call(
            model='qwen-vl-max',
            messages=messages,
            result_format='message',
        )
        if response.status_code == 200:
            text = response.output.choices[0].message.content[0]['text']
            # 提取 JSON
            start = text.find('{')
            end = text.rfind('}') + 1
            if start >= 0:
                return json.loads(text[start:end])
        return {"scene": "未知", "objects": [], "text": "", "action": "", "suitable_for": []}
    except Exception as e:
        print(f"    Qwen-VL 错误: {e}", flush=True)
        return {"scene": "未知", "objects": [], "text": "", "action": "", "suitable_for": []}

# ============ 步骤3: TTS + 时间戳 ============
def generate_speech(text, voice="longanyang", speech_rate=1.0, output=None):
    """生成配音，返回 duration"""
    from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
    
    try:
        audio = SpeechSynthesizer(
            model='cosyvoice-v3-flash',
            voice=voice,
            format=AudioFormat.MP3_48000HZ_MONO_256KBPS,
            speech_rate=speech_rate,
            volume=85,
        ).call(text)
        if audio and output:
            open(output, 'wb').write(audio)
            dur = float(subprocess.run([
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', output
            ], capture_output=True, text=True).stdout.strip())
            return dur
        return 0
    except Exception as e:
        print(f"    TTS错误: {e}", flush=True)
        return 0

# ============ 步骤4: 画面-文案匹配 ============
def calc_similarity(text, scene_desc):
    """基于关键词的简单匹配分"""
    text_lower = text.lower()
    score = 0
    
    # 痛点关键词
    pain_words = ['油污', '脏', '垢', '洗不干净', '黄', '黑', '反胃', '刷半天', '陈年']
    product_words = ['大公鸡', '清洁剂', '配方', '喷', '擦', '一抹', '泡沫']
    effect_words = ['干净', '亮', '净', '光洁', '对比', '变了']
    cta_words = ['下单', '抢', '囤', '链接', '活动', '管仨月']
    
    categories = {
        '痛点': pain_words, '产品展示': product_words, 
        '使用演示': product_words, '效果对比': effect_words,
        'CTA': cta_words
    }
    
    # 文本关键词匹配
    for word in pain_words:
        if word in text_lower:
            score += 2
    for word in product_words:
        if word in text_lower:
            score += 1
    
    # 场景标签匹配
    suitable = scene_desc.get('suitable_for', [])
    action = scene_desc.get('action', '')
    objects = ', '.join(scene_desc.get('objects', []))
    
    for tag in suitable:
        words = categories.get(tag, [])
        for w in words:
            if w in text_lower:
                score += 3
            if w in action or w in objects:
                score += 2
    
    return score

def match_scenes_to_scripts(scripts, segments_desc, segments_dur):
    """
    匹配每句文案到最佳画面段
    scripts: [(text, dur), ...] 每句文案和它的配音时长
    segments_desc: [desc_dict, ...] 每个镜头的画面描述
    segments_dur: [(start, end), ...] 每个镜头的时间段
    返回: [(script_idx, seg_idx, start_offset), ...]
    """
    matches = []
    script_idx = 0
    seg_idx = 0
    
    while script_idx < len(scripts):
        text, need_dur = scripts[script_idx]
        
        best_score = -1
        best_seg = seg_idx
        
        # 选当前及后续 3 个镜头里分最高的
        for i in range(seg_idx, min(seg_idx + 3, len(segments_desc))):
            if i < len(segments_desc):
                score = calc_similarity(text, segments_desc[i])
                seg_dur = segments_dur[i][1] - segments_dur[i][0]
                # 时长不够要加惩罚
                if seg_dur < need_dur - 0.5:
                    score -= 2
                if score > best_score:
                    best_score = score
                    best_seg = i
        
        # 如果 best_seg 的画面时长不够，多截几个
        needed = need_dur
        matched_segs = []
        i = best_seg
        while needed > 0 and i < len(segments_dur):
            sd = segments_dur[i][1] - segments_dur[i][0]
            use = min(sd, needed)
            matched_segs.append((i, 0, use))  # (seg_idx, start_offset, duration)
            needed -= use
            i += 1
        
        if not matched_segs:
            matched_segs = [(best_seg, 0, need_dur)]
        
        matches.append((script_idx, best_seg, matched_segs))
        script_idx += 1
        seg_idx = matched_segs[-1][0] + 1  # 下一个镜头
    
    return matches

# ============ 步骤5: ffmpeg 合成 ============
def composite_video(video_path, segments_dur, segments, scripts, matches, audio_files, output):
    """按匹配结果合成最终视频"""
    print("  合成最终视频...", flush=True)
    
    # 按匹配结果截取画面片段
    concat_list = []
    temp_files = []
    
    total_audio_dur = sum(a[1] for a in audio_files)
    t = 0.0
    
    for script_idx, best_seg, matched_segs in matches:
        text, _ = scripts[script_idx]
        
        for seg_idx, offset, dur in matched_segs:
            start = segments_dur[seg_idx][0] + offset
            end = min(start + dur, segments_dur[seg_idx][1])
            actual_dur = end - start
            
            if actual_dur < 0.1:
                continue
            
            out_file = os.path.join(TMP_DIR, f"seg_s{script_idx}_seg{seg_idx}.mp4")
            subprocess.run([
                'ffmpeg', '-y', '-ss', str(start), '-i', video_path,
                '-t', str(actual_dur),
                '-vf', 'scale=720:1280,setsar=1',
                '-r', '24', '-c:v', 'libx264', '-crf', '23',
                '-pix_fmt', 'yuv420p', '-an', '-preset', 'fast',
                out_file
            ], capture_output=True, timeout=30)
            temp_files.append(out_file)
            concat_list.append(f"file '{out_file}'")
    
    # 画面拼接
    concat_file = os.path.join(TMP_DIR, "concat.txt")
    with open(concat_file, 'w') as f:
        f.write('\n'.join(concat_list))
    
    video_out = os.path.join(TMP_DIR, "combined_video.mp4")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_file, '-c', 'copy', video_out
    ], capture_output=True, timeout=30)
    
    # 音频拼接
    audio_concat = os.path.join(TMP_DIR, "audio_list.txt")
    audio_files_list = [af[0] for af in audio_files]
    with open(audio_concat, 'w') as f:
        for af in audio_files_list:
            f.write(f"file '{af}'\n")
    
    audio_out = os.path.join(TMP_DIR, "combined_audio.mp3")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', audio_concat, '-c', 'copy', audio_out
    ], capture_output=True, timeout=30)
    
    # 合并声画
    subprocess.run([
        'ffmpeg', '-y', '-i', video_out, '-i', audio_out,
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', output
    ], capture_output=True, timeout=30)
    
    final_dur = float(subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', output
    ], capture_output=True, text=True).stdout.strip())
    final_size = os.path.getsize(output) // 1024
    
    print(f"  完成: {output} ({final_dur:.1f}s, {final_size}KB)", flush=True)
    return output

# ============ 主流程 ============
def run_pipeline(scripts_text, video_sources, voice="longanyang", scene_threshold=0.3):
    """
    运行完整管线
    scripts_text: 用 | 分隔的文案
    video_sources: 视频素材路径列表
    """
    print(f"\n{'='*60}", flush=True)
    print(f"大公鸡广告全自动混剪管线 v1", flush=True)
    print(f"{'='*60}", flush=True)
    
    # 解析文案
    scripts = [s.strip() for s in scripts_text.split('|') if s.strip()]
    print(f"\n【文案】{len(scripts)}句", flush=True)
    for i, s in enumerate(scripts):
        print(f"  {chr(65+i)}: {s}", flush=True)
    
    if len(video_sources) > 1:
        print(f"\n⚠ 多视频素材暂只支持第一个", flush=True)
    
    video_path = video_sources[0]
    
    # === 步骤1: 场景检测 ===
    print(f"\n【步骤1】场景检测...", flush=True)
    segments = detect_scenes(video_path, scene_threshold)
    
    # === 步骤2: 画面描述 ===
    print(f"\n【步骤2】Qwen-VL 画面描述...", flush=True)
    segments_desc = []
    for i, (s, e) in enumerate(segments):
        mid = (s + e) / 2
        frame_file = os.path.join(TMP_DIR, f"frame_{i}.jpg")
        extract_keyframe(video_path, mid, frame_file)
        print(f"  描述镜头{i}: {s:.1f}-{e:.1f}s", flush=True)
        desc = describe_frame(frame_file)
        segments_desc.append(desc)
        print(f"    场景: {desc.get('scene', '?')[:30]}", flush=True)
        time.sleep(1)  # 避免限频
    
    # === 步骤3: TTS 生成 ===
    print(f"\n【步骤3】TTS 配音生成...", flush=True)
    audio_segments = []
    total_dur = 0.0
    
    for i, script in enumerate(scripts):
        audio_file = os.path.join(TMP_DIR, f"tts_{i}.mp3")
        # 用 emotion 递进
        rates = [0.85, 0.8, 1.0, 1.1, 1.0, 1.15, 1.2, 1.3, 1.35]
        sr = rates[i] if i < len(rates) else 1.0
        
        print(f"  TTS {i}: {script[:30]}... (sr={sr})", flush=True)
        dur = generate_speech(script, voice, sr, audio_file)
        if dur > 0:
            audio_segments.append((audio_file, dur, script, sr))
            total_dur += dur
            print(f"    {dur:.2f}s", flush=True)
        time.sleep(1)
    
    print(f"  配音总时长: {total_dur:.2f}s", flush=True)
    
    # === 步骤4: 匹配 ===
    print(f"\n【步骤4】画面-文案匹配...", flush=True)
    scripts_with_dur = [(s, d) for _, d, s, _ in audio_segments]
    matches = match_scenes_to_scripts(scripts_with_dur, segments_desc, segments)
    
    for i, _, matched_segs in matches:
        txt = scripts[i]
        segs_info = [f"seg{s[0]}({s[2]:.1f}s)" for s in matched_segs]
        print(f"  文案{i} '{txt[:20]}...' → {', '.join(segs_info)}", flush=True)
    
    # === 步骤5: 合成 ===
    print(f"\n【步骤5】ffmpeg 合成...", flush=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output = os.path.join(OUT_DIR, f"dajigong_ad_{timestamp}.mp4")
    
    composite_video(video_path, segments, segments_desc, scripts_with_dur, matches,
                    [(af, ad) for af, ad, _, _ in audio_segments], output)
    
    # 发布
    web_file = f"dajigong_ad_pipeline_{timestamp}.mp4"
    subprocess.run(['sudo', 'cp', output, f'/var/www/html/{web_file}'], capture_output=True)
    
    print(f"\n{'='*60}", flush=True)
    print(f"✅ 完成!", flush=True)
    print(f"  本地: {output}", flush=True)
    print(f"  Web:  http://8.148.226.250/{web_file}", flush=True)
    print(f"{'='*60}", flush=True)
    
    return output

# ============ CLI ============
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大公鸡广告全自动混剪管线")
    parser.add_argument("scripts", help="文案，用 | 分隔")
    parser.add_argument("--素材", nargs="+", required=True, help="视频素材路径")
    parser.add_argument("--voice", default="longanyang", help="TTS音色")
    parser.add_argument("--threshold", type=float, default=0.3, help="场景检测阈值(0-1)")
    
    args = parser.parse_args()
    run_pipeline(args.scripts, args.素材, args.voice, args.threshold)
