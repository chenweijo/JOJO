#!/usr/bin/env python3
"""
Qwen3.5-Omni 视频精细分析 v3
每 0.5 秒一个场景点，详细描述
"""
import os, sys, json, base64, time, subprocess
from pathlib import Path

API_KEY = "***"  # 会在下面读取
OUT = "/home/admin/.openclaw/workspace/media/dajigong/output/omni_v3"
RAW = "/home/admin/.openclaw/workspace/media/dajigong/raw"

os.makedirs(OUT, exist_ok=True)

# 读取API Key
f = open('/home/admin/.secrets/tts_key.b64').read().strip()
API_KEY = base64.b64decode(f.encode() if not f.startswith('sk') else f.encode()).decode().strip() if f.startswith('sk') else base64.b64decode(f).decode().strip()
os.environ['DASHSCOPE_API_KEY'] = API_KEY

TARGETS = [
    ("clip_1.mp4", "clip_1", 5),
    ("clip_2.mp4", "clip_2", 5),
    ("clip_3.mp4", "clip_3", 5),
    ("clip_4.mp4", "clip_4", 5),
    ("dagongji_01_7146093875294194983.mp4", "dagongji_01", 15),
    ("dagongji_04_7299809851717274880.mp4", "dagongji_04", 29),
    ("dagongji_06_7572878520797744714.mp4", "dagongji_06", 38),
]

SYSTEM_PROMPT = """你是一个专业的视频场景分析师。你的任务是对视频中每一个指定时间点的画面进行精确、详细的描述。

要求：
1. 必须对请求中指定的每一个时间点都输出分析结果，一个都不能少
2. 每个时间点的描述要具体：物体颜色/材质/位置、动作细节、污渍的形态位置、使用的工具
3. 文本元素：画面中出现的任何文字（产品名称、标签、说明文字、品牌名）
4. suitable_for：判断这段画面最适合广告的哪个阶段（痛点/产品展示/使用演示/效果对比/CTA/多场景适用/信任背书），可以选多个
5. 输出严格的JSON格式，不要添加任何非JSON内容"""

def make_user_prompt(duration):
    # 生成时间点列表
    points = []
    t = 0.0
    while t < duration:
        points.append(round(t, 1))
        t += 0.5
    if points[-1] < duration - 0.1:
        points.append(round(duration, 1))
    timeline = ", ".join([str(p) for p in points])
    
    prompt = f"""视频时长约{duration}秒，请按以下时间点输出分析：
{timeline}

每个时间点输出一个JSON对象，包含以下字段：
- time_s: 时间点（数字）
- scene: 场景名称（描述性的，如"布满黄褐色油垢的不锈钢抽油烟机滤网特写"）
- objects: 画面中所有可见物体列表，每个物体描述颜色和材质（如["白色喷雾瓶（红色喷头）","戴透明手套的手","黄褐色粘稠油污","不锈钢台面"]）
- text_elements: 画面中出现的所有文字内容（如瓶身标签上的文字，没有则为空字符串）
- action: 画面中发生的事（详细描述动作、工具使用方式、前后变化、污渍变化过程）
- lighting: 光线条件（如"明亮的室内顶光，金属表面有反光"）
- camera: 镜头类型和角度（如"固定俯拍近景特写"）
- mood: 氛围（如"清洁高效"）
- suitable_for: 数组，适合广告阶段（可选值：痛点/产品展示/使用演示/效果对比/CTA/多场景适用/信任背书。如果画面包括清洁前后对比必须包含效果对比）

输出格式示例：
[{{"time_s":0,"scene":"场景名","objects":["物体A","物体B"],"text_elements":"","action":"动作描述","lighting":"光线","camera":"镜头","mood":"氛围","suitable_for":["痛点"]}}]

输出纯JSON数组，不要添加任何非JSON的说明文字。"""
    return prompt, points

def read_video_b64(video_path, max_b64_bytes=5*1024*1024):
    compressed = f"/tmp/comp_{Path(video_path).name}"
    subprocess.run(['ffmpeg','-y','-i',video_path,'-vf','scale=480:854',
        '-crf','28','-c:v','libx264','-preset','fast','-an',compressed],
        capture_output=True)
    data = open(compressed, 'rb').read()
    b64 = base64.b64encode(data).decode('utf-8')
    if len(b64) <= max_b64_bytes:
        return b64
    subprocess.run(['ffmpeg','-y','-i',video_path,'-vf','scale=360:640',
        '-crf','30','-c:v','libx264','-preset','fast','-an',compressed],
        capture_output=True)
    data = open(compressed, 'rb').read()
    return base64.b64encode(data).decode('utf-8')

def call_omni(video_b64, prompt):
    from dashscope import MultiModalConversation
    messages = [
        {"role": "system", "content": [{"text": SYSTEM_PROMPT}]},
        {"role": "user", "content": [{"video": video_b64}, {"text": prompt}]}
    ]
    response = MultiModalConversation.call(
        model='qwen3.5-omni-flash',
        messages=messages,
        result_format='message',
    )
    if response.status_code == 200:
        return response.output.choices[0].message.content[0]['text']
    raise Exception(f"API Error {response.status_code}: {response.message}")

def analyse(filename, name, duration):
    print(f"\n===== {name} ({duration}s) =====", flush=True)
    video_path = os.path.join(RAW, filename)
    if not os.path.exists(video_path):
        print(f"  ❌ 文件不存在")
        return False
    
    prompt, points = make_user_prompt(duration)
    print(f"  时间点: {len(points)}个", flush=True)
    
    print(f"  读取视频...", flush=True)
    video_b64 = read_video_b64(video_path)
    print(f"  Base64: {len(video_b64)//1024}KB", flush=True)
    
    for attempt in range(3):
        try:
            print(f"  调用Omni (第{attempt+1}次)...", flush=True)
            t0 = time.time()
            result = call_omni(video_b64, prompt)
            dt = time.time() - t0
            print(f"  响应 ({dt:.1f}s)", flush=True)
            
            # 解析JSON
            start = result.find('[')
            end = result.rfind(']') + 1
            if start >= 0 and end > start:
                json_str = result[start:end]
            else:
                json_str = result
            data = json.loads(json_str)
            
            break
        except json.JSONDecodeError as e:
            print(f"  JSON解析失败: {e}", flush=True)
            if attempt < 2:
                time.sleep(3)
                continue
            raw_file = os.path.join(OUT, f"{name}_raw.txt")
            with open(raw_file, 'w') as f:
                f.write(result)
            print(f"  原始结果保存到 {name}_raw.txt", flush=True)
            return False
        except Exception as e:
            print(f"  失败: {e}", flush=True)
            if attempt < 2:
                time.sleep(5)
                continue
            return False
    
    # 保存
    output = {
        "filename": filename,
        "name": name,
        "duration": duration,
        "total_points": len(points),
        "analysed": len(data),
        "scenes": data,
        "analysed_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    out_file = os.path.join(OUT, f"{name}.json")
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 保存 {out_file} ({len(data)}个场景点)", flush=True)
    return True

# 主流程
targets = TARGETS
if len(sys.argv) > 1:
    targets = [t for t in TARGETS if t[1] in sys.argv[1:] or t[0].replace('.mp4','') in sys.argv[1:]]

for i, (filename, name, duration) in enumerate(targets, 1):
    print(f"\n[{i}/{len(targets)}]", end="")
    analyse(filename, name, duration)
    if i < len(targets):
        print("  等5秒...", flush=True)
        time.sleep(5)

print(f"\n✅ 完成! 输出: {OUT}")
