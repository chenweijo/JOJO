#!/usr/bin/env python3
"""
大公鸡广告全自动混剪管线 v2
从多个素材的 ffmpeg 镜头库里自动匹配画面 + TTS 配音 + 合成
"""
import os, sys, json, base64, time, subprocess, re, shutil
from pathlib import Path

WORKSPACE = "/home/admin/.openclaw/workspace/media/dajigong"
RAW = f"{WORKSPACE}/raw"
OUT = f"{WORKSPACE}/output/pipeline_v2"
TMP = "/tmp/dajigong_pipeline_v2"

os.makedirs(OUT, exist_ok=True)
os.makedirs(TMP, exist_ok=True)

# 读key
import base64
f = open('/home/admin/.secrets/tts_key.b64').read().strip()
if f.startswith('sk-'):
    API_KEY = f
else:
    API_KEY = base64.b64decode(f).decode().strip()
os.environ['DASHSCOPE_API_KEY'] = API_KEY
from dashscope import MultiModalConversation
from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

# 手动指定7个素材
MATERIALS = [
    "clip_1.mp4", "clip_2.mp4", "clip_3.mp4", "clip_4.mp4",
    "dagongji_01_7146093875294194983.mp4",
    "dagongji_04_7299809851717274880.mp4",
    "dagongji_06_7572878520797744714.mp4",
]

def log(msg):
    print(msg, flush=True)

# ===== 步骤1: 全素材场景检测 =====
def detect_all_scenes(videos, threshold=0.3):
    """对所有素材做场景检测，返回统一镜头库"""
    all_segments = []  # [(video_path, seg_idx, start, end), ...]
    
    for vp in videos:
        path = os.path.join(RAW, vp) if not os.path.isabs(vp) else vp
        if not os.path.exists(path):
            log(f"  跳过: {vp} (不存在)")
            continue
        
        dur = float(subprocess.run(
            ['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',path],
            capture_output=True,text=True).stdout.strip())
        
        # ffmpeg场景检测
        result = subprocess.run(
            ['ffmpeg','-i',path,
             '-vf',f"select='gt(scene,{threshold})',showinfo",
             '-vsync','vfr','-f','null','-'],
            capture_output=True,text=True,timeout=30)
        
        times = [0.0]
        for line in result.stderr.split('\n'):
            m = re.search(r'pts_time:([\d.]+)', line)
            if m:
                t = float(m.group(1))
                if t > 0 and t < dur - 0.2:
                    times.append(t)
        times.append(dur)
        
        # 最小段1秒
        segments = []
        seg_start = times[0]
        for t in times[1:]:
            if t - seg_start >= 1.0:
                segments.append((seg_start, t))
                seg_start = t
        if times[-1] - seg_start >= 0.5:
            segments.append((seg_start, times[-1]))
        
        for i, (s, e) in enumerate(segments):
            all_segments.append((path, i, s, e))
        
        log(f"  {Path(path).name}: {len(segments)}镜头 ({dur:.1f}s)")
    
    log(f"  总镜头数: {len(all_segments)}")
    return all_segments

def extract_frame(video, timestamp, out):
    subprocess.run(['ffmpeg','-y','-ss',str(timestamp),'-i',video,
        '-vframes','1','-q:v','2',out], capture_output=True, timeout=10)

# ===== 步骤2: Qwen-VL 描述每段首帧 =====
def describe_frame(image_path, retries=2):
    for attempt in range(retries):
        try:
            with open(image_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
            
            resp = MultiModalConversation.call(
                model='qwen-vl-max',
                messages=[{"role":"user","content":[
                    {"image":f"data:image/jpeg;base64,{b64}"},
                    {"text":"""描述这个画面，输出JSON:
{
  "scene":"场景名(10字内)",
  "objects":["物体列表(带颜色材质)"],
  "text":"画面上的文字",
  "action":"正在发生什么",
  "mood":"氛围",
  "suitable_for":["痛点","产品展示","使用演示","效果对比","CTA"]
}
只输出JSON，markdown格式也不要。"""}
                ]}],
                result_format='message',
            )
            if resp.status_code == 200:
                text = resp.output.choices[0].message.content[0]['text']
                s = text.find('{'); e = text.rfind('}')+1
                if s >= 0:
                    return json.loads(text[s:e])
            log(f"    API error: {resp.message}")
        except Exception as e:
            log(f"    retry {attempt}: {e}")
            time.sleep(2)
    return {"scene":"未知","objects":[],"text":"","action":"","mood":"","suitable_for":["痛点"]}

def describe_all_segments(segments):
    """批量描述所有镜头"""
    descs = []
    total = len(segments)
    for idx, (vp, si, s, e) in enumerate(segments):
        mid = (s+e)/2
        frame = os.path.join(TMP, f"frame_{idx}.jpg")
        extract_frame(vp, mid, frame)
        log(f"  [{idx+1}/{total}] seg{idx}: {s:.1f}-{e:.1f}s")
        desc = describe_frame(frame)
        descs.append(desc)
        log(f"    → {desc.get('scene','?')}")
        time.sleep(1)  # 避免限频
    return descs

# ===== 步骤3: TTS 配音 =====
def generate_tts(text, voice, sr, out):
    audio = SpeechSynthesizer(
        model='cosyvoice-v3-flash', voice=voice,
        format=AudioFormat.MP3_48000HZ_MONO_256KBPS,
        speech_rate=sr, volume=85,
    ).call(text)
    if audio:
        open(out, 'wb').write(audio)
        dur = float(subprocess.run([
            'ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',out
        ], capture_output=True, text=True).stdout.strip())
        return dur
    return 0

# ===== 步骤4: 智能匹配 =====
def match_scripts(scripts_with_dur, segments, seg_descs):
    """从全镜头库为每句文案匹配最优画面"""
    pain_kw = ['油污','脏','垢','洗不干净','黄','黑','反胃','刷半天','陈年','顽固']
    product_kw = ['大公鸡','清洁剂','配方','喷','擦','一抹','泡沫']
    effect_kw = ['干净','亮','净','光洁','亮了','变了']
    cta_kw = ['下单','抢','囤','链接','活动','管仨月','马上']
    
    def score(text, desc):
        s = 0
        suitable = desc.get('suitable_for', [])
        action = desc.get('action','')
        objects = ' '.join(desc.get('objects',[]))
        
        # 痛点词
        if any(k in text for k in pain_kw):
            if '痛点' in suitable:
                s += 5
                if any(k in text for k in ['喷','擦','泡沫']):
                    s += 2
        # 产品词
        if any(k in text for k in product_kw):
            if any(t in suitable for t in ['产品展示','使用演示']):
                s += 5
        
        # 动作匹配
        if '喷' in text and ('喷' in action or '喷洒' in action):
            s += 3
        if '擦' in text and ('擦' in action or '擦拭' in action):
            s += 3
        if '铲' in text and ('铲' in action or '刮' in action):
            s += 3
        if '泡沫' in text and '泡沫' in action:
            s += 3
        
        # 效果
        if any(k in text for k in effect_kw):
            if '效果对比' in suitable:
                s += 5
        # CTA
        if any(k in text for k in cta_kw):
            if 'CTA' in suitable:
                s += 5
        
        return s
    
    used = set()
    results = []
    
    for idx, (text, need_dur) in enumerate(scripts_with_dur):
        # 从所有未用完的镜头里选分最高的
        candidates = []
        for seg_idx, seg_desc in enumerate(seg_descs):
            if seg_idx in used:
                continue
            _, _, s, e = segments[seg_idx]
            seg_dur = e - s
            sc = score(text, seg_desc)
            
            # 时长合适加分，不够减分
            if seg_dur >= need_dur:
                sc += 3
            elif seg_dur < need_dur - 1:
                sc -= 2
            
            candidates.append((sc, seg_idx, seg_dur))
        
        candidates.sort(key=lambda x: -x[0])
        
        if not candidates:
            log(f"  ⚠ 文案{idx}: 无可用镜头，跳过")
            continue
        
        best_sc, best_idx, best_dur = candidates[0]
        used.add(best_idx)
        
        # 如果时长不够要续接
        matched_segs = [(best_idx, 0, min(best_dur, need_dur))]
        remaining = need_dur - min(best_dur, need_dur)
        i = best_idx + 1
        while remaining > 0.5 and i < len(segments):
            if i not in used:
                _, _, si, ei = segments[i]
                sd = ei - si
                use = min(sd, remaining)
                matched_segs.append((i, 0, use))
                used.add(i)
                remaining -= use
            i += 1
        
        results.append((idx, best_idx, matched_segs))
        seg_names = [f"seg{m[0]}" for m in matched_segs]
        log(f"  文案{idx}({need_dur:.1f}s) → {','.join(seg_names)} (score={best_sc})")
    
    return results

# ===== 步骤5: 合成 =====
def composite(segments, seg_descs, matches, audio_files, output):
    log("  合成...")
    concat_items = []
    
    for script_idx, best_seg, matched_segs in matches:
        for seg_idx, offset, dur in matched_segs:
            vp, si, s, e = segments[seg_idx]
            start = s + offset
            actual_dur = min(dur, e - start)
            if actual_dur < 0.2: continue
            
            out_seg = os.path.join(TMP, f"clip_s{script_idx}_seg{seg_idx}.mp4")
            subprocess.run([
                'ffmpeg','-y','-ss',str(start),'-i',vp,
                '-t',str(actual_dur),
                '-vf','scale=720:1280,setsar=1',
                '-r','24','-c:v','libx264','-crf','23',
                '-pix_fmt','yuv420p','-an','-preset','fast',
                out_seg
            ], capture_output=True, timeout=30)
            concat_items.append(f"file '{out_seg}'")
    
    if not concat_items:
        log("  ⚠ 无可用画面")
        return None
    
    # 画面拼接
    list_file = os.path.join(TMP, "concat.txt")
    with open(list_file, 'w') as f:
        f.write('\n'.join(concat_items))
    
    video_out = os.path.join(TMP, "video.mp4")
    subprocess.run(['ffmpeg','-y','-f','concat','-safe','0',
        '-i',list_file,'-c','copy',video_out], capture_output=True, timeout=30)
    
    # 音频拼接
    audio_list = os.path.join(TMP, "audio_list.txt")
    with open(audio_list, 'w') as f:
        for af, ad, _, _ in audio_files:
            f.write(f"file '{af}'\n")
    
    audio_out = os.path.join(TMP, "audio.mp3")
    subprocess.run(['ffmpeg','-y','-f','concat','-safe','0',
        '-i',audio_list,'-c','copy',audio_out], capture_output=True, timeout=30)
    
    # 合并
    subprocess.run(['ffmpeg','-y','-i',video_out,'-i',audio_out,
        '-c:v','copy','-c:a','aac','-b:a','192k','-shortest',output],
        capture_output=True, timeout=30)
    
    fd = float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',output],
        capture_output=True,text=True).stdout.strip())
    log(f"  ✅ {output} ({fd:.1f}s)")
    return output

# ===== CLI =====
def run(scripts_text, threshold=0.3, voice='longanyang'):
    log(f"=== 大公鸡广告全自动混剪 v2 ===")
    
    # 解析文案
    scripts = [s.strip() for s in scripts_text.split('|') if s.strip()]
    log(f"\n文案({len(scripts)}句):")
    for i,s in enumerate(scripts): log(f"  {i}: {s}")
    
    # 步骤1: 场景检测
    log(f"\n[1/5] 全素材场景检测...")
    segments = detect_all_scenes(MATERIALS, threshold)
    
    # 步骤2: 画面描述
    log(f"\n[2/5] Qwen-VL 描述每个镜头...")
    seg_descs = describe_all_segments(segments)
    
    # 步骤3: TTS
    log(f"\n[3/5] TTS 配音...")
    rates = [0.85, 0.8, 1.0, 1.1, 1.0, 1.15, 1.2, 1.3, 1.35, 1.4]
    audio_segs = []
    for i, s in enumerate(scripts):
        fp = os.path.join(TMP, f"tts_{i}.mp3")
        sr = rates[i] if i < len(rates) else 1.0
        log(f"  TTS {i}: sr={sr}")
        dur = generate_tts(s, voice, sr, fp)
        if dur > 0:
            audio_segs.append((fp, dur, s, sr))
            log(f"    {dur:.2f}s")
        time.sleep(1)
    
    total_dur = sum(a[1] for a in audio_segs)
    log(f"  配音总长: {total_dur:.2f}s")
    
    # 步骤4: 匹配
    log(f"\n[4/5] 画面-文案自动匹配...")
    scripts_dur = [(s, d) for _, d, s, _ in audio_segs]
    matches = match_scripts(scripts_dur, segments, seg_descs)
    
    # 步骤5: 合成
    log(f"\n[5/5] ffmpeg 合成...")
    ts = time.strftime("%Y%m%d_%H%M%S")
    output = os.path.join(OUT, f"dajigong_ad_pipeline_{ts}.mp4")
    
    r = composite(segments, seg_descs, matches, audio_segs, output)
    
    if r:
        web = f"dajigong_ad_pipeline_{ts}.mp4"
        subprocess.run(['sudo','cp',r,f'/var/www/html/{web}'], capture_output=True)
        log(f"\n✅ Web: http://8.148.226.250/{web}")
    
    log(f"\n完成!")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("scripts", help="文案，用|分隔")
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--voice", default="longanyang")
    args = ap.parse_args()
    run(args.scripts, args.threshold, args.voice)
