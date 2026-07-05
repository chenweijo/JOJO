#!/usr/bin/env python3
"""
大公鸡广告全自动混剪 v3 - Embedding 语义匹配版
"""
import os, sys, json, base64, time, subprocess, re
from pathlib import Path

WORKSPACE = "/home/admin/.openclaw/workspace/media/dajigong"
RAW = f"{WORKSPACE}/raw"
OUT = f"{WORKSPACE}/output/pipeline_v3"
TMP = "/tmp/dajigong_v3"

os.makedirs(OUT, exist_ok=True)
os.makedirs(TMP, exist_ok=True)

import base64 as b64lib
f = open('/home/admin/.secrets/tts_key.b64').read().strip()
API_KEY = f if f.startswith('sk-') else b64lib.b64decode(f).decode().strip()
os.environ['DASHSCOPE_API_KEY'] = API_KEY

from dashscope import MultiModalConversation, TextEmbedding
from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

MATERIALS = [
    "clip_1.mp4", "clip_2.mp4", "clip_3.mp4", "clip_4.mp4",
    "dagongji_01_7146093875294194983.mp4",
    "dagongji_04_7299809851717274880.mp4",
    "dagongji_06_7572878520797744714.mp4",
]

def log(msg): print(msg, flush=True)

# ===== Embedding =====
def get_embedding(text):
    resp = TextEmbedding.call(model='text-embedding-v3', input=[text])
    if resp.status_code == 200:
        return resp.output['embeddings'][0]['embedding']
    return None

# ===== 步骤1: 场景检测 =====
def detect_scenes(videos, threshold=0.3):
    all_segs = []
    for vp in videos:
        path = os.path.join(RAW, vp) if not os.path.isabs(vp) else vp
        if not os.path.exists(path): continue
        dur = float(subprocess.run(
            ['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',path],
            capture_output=True,text=True).stdout.strip())
        
        r = subprocess.run(['ffmpeg','-i',path,
            '-vf',f"select='gt(scene,{threshold})',showinfo",
            '-vsync','vfr','-f','null','-'],
            capture_output=True,text=True,timeout=30)
        
        times = [0.0]
        for line in r.stderr.split('\n'):
            m = re.search(r'pts_time:([\d.]+)', line)
            if m:
                t = float(m.group(1))
                if t > 0 and t < dur - 0.2: times.append(t)
        times.append(dur)
        
        segs = []; start = times[0]
        for t in times[1:]:
            if t - start >= 0.8:
                segs.append((start, t))
                start = t
        if times[-1] - start >= 0.5:
            segs.append((start, times[-1]))
        
        for i, (s, e) in enumerate(segs):
            all_segs.append((path, s, e))
        log(f"  {Path(path).name}: {len(segs)}段 ({dur:.1f}s)")
    
    log(f"  总: {len(all_segs)}段")
    return all_segs

# ===== 步骤2: 画面描述 + embedding =====
def describe_and_embed(segments):
    seg_data = []  # [{video, start, end, desc, embedding}]
    total = len(segments)
    
    for idx, (vp, s, e) in enumerate(segments):
        mid = (s+e)/2
        frame = os.path.join(TMP, f"f{idx}.jpg")
        subprocess.run(['ffmpeg','-y','-ss',str(mid),'-i',vp,
            '-vframes','1','-q:v','2',frame], capture_output=True, timeout=10)
        
        log(f"  [{idx+1}/{total}]描述+embedding: {Path(vp).name} @{s:.1f}s ({e-s:.1f}s)")
        
        # Qwen-VL 详细描述
        with open(frame, 'rb') as fh:
            b64 = b64lib.b64encode(fh.read()).decode('utf-8')
        
        desc = {"scene":"","objects":[],"action":"","mood":"","suitable_for":[]}
        try:
            resp = MultiModalConversation.call(
                model='qwen-vl-max',
                messages=[{"role":"user","content":[
                    {"image":f"data:image/jpeg;base64,{b64}"},
                    {"text":"""详细描述这个画面，JSON格式（不要markdown）:
{
  "scene":"场景名称（20字内，具体描述，如"不锈钢台面上正在刮除黄褐色油垢"）",
  "objects":["物体列表","白色喷雾瓶","戴手套的手","黑色刮刀","不锈钢台面"],
  "action":("详细描述动作过程，如"手拿黑色刮刀正在铲除台面上被喷湿的黄褐色粘稠油垢"",
  "mood":("氛围词"",
  "suitable_for":["痛点","产品展示","使用演示","效果对比","CTA"]
}"""}
                ]}],
                result_format='message',
            )
            if resp.status_code == 200:
                text = resp.output.choices[0].message.content[0]['text']
                ss = text.find('{'); ee = text.rfind('}')+1
                if ss >= 0:
                    desc = json.loads(text[ss:ee])
        except Exception as e:
            log(f"    VL错误: {e}")
        
        # 构建描述文本
        desc_text = f"{desc.get('scene','')} {desc.get('action','')} 物体:{','.join(desc.get('objects',[]))}"
        tags = '/'.join(desc.get('suitable_for',['痛点']))
        full_text = f"{desc_text} 适合:{tags}"
        
        # embedding
        emb = get_embedding(full_text)
        
        seg_data.append({
            "video": vp, "start": s, "end": e,
            "desc": desc, "embedding": emb,
            "desc_text": desc_text, "full_text": full_text
        })
        
        time.sleep(1)
    
    return seg_data

# ===== 步骤3: TTS =====
def gen_tts(text, voice, sr, out):
    audio = SpeechSynthesizer(
        model='cosyvoice-v3-flash', voice=voice,
        format=AudioFormat.MP3_48000HZ_MONO_256KBPS,
        speech_rate=sr, volume=85,
    ).call(text)
    if audio:
        open(out, 'wb').write(audio)
        return float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',out],
            capture_output=True,text=True).stdout.strip())
    return 0

# ===== 步骤4: Embedding匹配 =====
def cos_sim(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na = sum(x*x for x in a)**0.5
    nb = sum(x*x for x in b)**0.5
    return dot/(na*nb) if na*nb > 0 else 0

def match_by_embedding(scripts, seg_data):
    """用语义相似度匹配每句文案到最合适的镜头"""
    log("  计算文案embedding...")
    script_embs = []
    for i, (text, dur) in enumerate(scripts):
        emb = get_embedding(f"画面：{text}")
        script_embs.append((text, dur, emb))
        log(f"    文案{i}: {text[:25]}...")
        time.sleep(0.5)
    
    used = set()
    results = []
    
    for idx, (text, need_dur, s_emb) in enumerate(script_embs):
        if s_emb is None:
            log(f"  文案{idx}: embedding失败，跳过")
            continue
        
        candidates = []
        for seg_idx, sd in enumerate(seg_data):
            if seg_idx in used: continue
            d_emb = sd["embedding"]
            if d_emb is None: continue
            sim = cos_sim(s_emb, d_emb)
            seg_len = sd["end"] - sd["start"]
            
            # 时长合适加分
            length_bonus = 0.1 if seg_len >= need_dur else -0.1
            if seg_len >= need_dur:
                length_bonus = 0.15
            elif seg_len >= need_dur * 0.6:
                length_bonus = 0.05
            
            candidates.append((sim + length_bonus, seg_idx, seg_len))
        
        candidates.sort(key=lambda x: -x[0])
        if not candidates:
            log(f"  文案{idx}: 无可选镜头")
            continue
        
        best_score, best_idx, best_len = candidates[0]
        used.add(best_idx)
        
        # 时长不够续接
        matched = [(best_idx, 0, min(best_len, need_dur))]
        remaining = need_dur - min(best_len, need_dur)
        i = best_idx + 1
        while remaining > 0.5 and i < len(seg_data):
            if i not in used:
                sd = seg_data[i]
                sl = sd["end"] - sd["start"]
                use = min(sl, remaining)
                matched.append((i, 0, use))
                used.add(i)
                remaining -= use
            i += 1
        
        results.append((idx, best_idx, matched, best_score))
        seg_names = [f"seg{m[0]}" for m in matched]
        log(f"  文案{idx}({need_dur:.1f}s) → {','.join(seg_names)} (sim={best_score:.3f})")
    
    return results

# ===== 步骤5: 合成 =====
def composite(segments, seg_data, matches, audio_files, output):
    log("  合成...")
    items = []
    
    for script_idx, best_seg, matched_segs, score in matches:
        for seg_idx, offset, dur in matched_segs:
            vp = seg_data[seg_idx]["video"]
            start = seg_data[seg_idx]["start"] + offset
            end = seg_data[seg_idx]["end"]
            actual = min(dur, end - start)
            if actual < 0.2: continue
            
            out_seg = os.path.join(TMP, f"c_s{script_idx}_seg{seg_idx}.mp4")
            subprocess.run([
                'ffmpeg','-y','-ss',str(start),'-i',vp,
                '-t',str(actual),
                '-vf','scale=720:1280,setsar=1',
                '-r','24','-c:v','libx264','-crf','23',
                '-pix_fmt','yuv420p','-an','-preset','fast',
                out_seg
            ], capture_output=True, timeout=30)
            items.append(f"file '{out_seg}'")
    
    if not items: return None
    
    with open(os.path.join(TMP, "clist.txt"), 'w') as f:
        f.write('\n'.join(items))
    video_out = os.path.join(TMP, "v.mp4")
    subprocess.run(['ffmpeg','-y','-f','concat','-safe','0',
        '-i', os.path.join(TMP, "clist.txt"), '-c','copy', video_out],
        capture_output=True, timeout=30)
    
    with open(os.path.join(TMP, "alist.txt"), 'w') as f:
        for af, _, _, _ in audio_files:
            f.write(f"file '{af}'\n")
    audio_out = os.path.join(TMP, "a.mp3")
    subprocess.run(['ffmpeg','-y','-f','concat','-safe','0',
        '-i', os.path.join(TMP, "alist.txt"), '-c','copy', audio_out],
        capture_output=True, timeout=30)
    
    subprocess.run(['ffmpeg','-y','-i',video_out,'-i',audio_out,
        '-c:v','copy','-c:a','aac','-b:a','192k','-shortest',output],
        capture_output=True, timeout=30)
    
    fd = float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',output],
        capture_output=True,text=True).stdout.strip())
    log(f"  ✅ {output} ({fd:.1f}s)")
    return output

# ===== CLI =====
def run(scripts_text, threshold=0.3, voice='longanyang'):
    log("=== 大公鸡广告全自动混剪 v3 (Embedding) ===")
    scripts = [s.strip() for s in scripts_text.split('|') if s.strip()]
    log(f"\n文案({len(scripts)}句):")
    for i,s in enumerate(scripts): log(f"  {i}: {s}")
    
    # 1
    log(f"\n[1/5] 全素材场景检测...")
    segs = detect_scenes(MATERIALS, threshold)
    
    # 2
    log(f"\n[2/5] Qwen-VL描述 + Embedding...")
    seg_data = describe_and_embed(segs)
    
    # 3
    log(f"\n[3/5] TTS...")
    rates = [0.85, 0.8, 1.0, 1.1, 1.0, 1.15, 1.2, 1.3, 1.35, 1.4]
    audio_segs = []
    for i, s in enumerate(scripts):
        fp = os.path.join(TMP, f"tts_{i}.mp3")
        sr = rates[i] if i < len(rates) else 1.0
        log(f"  TTS {i}: sr={sr}")
        dur = gen_tts(s, voice, sr, fp)
        if dur > 0:
            audio_segs.append((fp, dur, s, sr))
            log(f"    {dur:.2f}s")
        time.sleep(1)
    
    total = sum(a[1] for a in audio_segs)
    log(f"  总长: {total:.2f}s")
    
    # 4
    log(f"\n[4/5] Embedding匹配...")
    scripts_dur = [(s, d) for _, d, s, _ in audio_segs]
    matches = match_by_embedding(scripts_dur, seg_data)
    
    # 5
    log(f"\n[5/5] 合成...")
    ts = time.strftime("%Y%m%d_%H%M%S")
    output = os.path.join(OUT, f"dajigong_ad_v3_{ts}.mp4")
    r = composite(segs, seg_data, matches, audio_segs, output)
    
    if r:
        web = f"dajigong_ad_v3_{ts}.mp4"
        subprocess.run(['sudo','cp',r,f'/var/www/html/{web}'], capture_output=True)
        log(f"\n✅ Web: http://8.148.226.250/{web}")
    
    log("\n完成!")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("scripts")
    ap.add_argument("--threshold", type=float, default=0.3)
    ap.add_argument("--voice", default="longanyang")
    args = ap.parse_args()
    run(args.scripts, args.threshold, args.voice)
