#!/usr/bin/env python3
"""
Qwen3.5-Omni 视频精细分析 v3
每 0.5 秒一个场景点，逐帧级细节描述
"""
import os, sys, json, base64, time, subprocess
from pathlib import Path

API_KEY = "***"
OUT = "/home/admin/.openclaw/workspace/media/dajigong/output/omni_v3"
RAW = "/home/admin/.openclaw/workspace/media/dajigong/raw"
FRAME_DIR = "/tmp/omni_frames_v3"

os.makedirs(OUT, exist_ok=True)
os.makedirs(FRAME_DIR, exist_ok=True)

# 要分析的素材
TARGETS = [
    ("clip_1.mp4", "clip_1", 5),
    ("clip_2.mp4", "clip_2", 5),
    ("clip_3.mp4", "clip_3", 5),
    ("clip_4.mp4", "clip_4", 5),
    ("dagongji_01_7146093875294194983.mp4", "dagongji_01", 15),
    ("dagongji_04_7299809851717274880.mp4", "dagongji_04", 29),
    ("dagongji_06_7572878520797744714.mp4", "dagongji_06", 38),
]

# 精准 prompt - 强制每 0.5s 一个点
SYSTEM_PROMPT = """你是一个视频场景分析师。对每一帧画面进行精确描述。

要求：
1. 严格按照请求中指定输出点（每0.5秒一个）输出分析结果，不要省略任何时间点
2. 每个时间点必须包含所有字段
3. 画面描述要具体：物体颜色、位置、动作细节、污渍位置和形态、使用什么工具
4. 文本元素：画面中出现的任何文字（产品标签/品牌名/指引文字）
5. suitable_for：判断这一段画面适合广告的哪个阶段（痛点/产品展示/使用演示/效果对比/CTA/多场景适用/信任背书），必须选一个或多个
6. 输出严格JSON格式"""

USER_PROMPT_TEMPLATE = """视频时长约{duration}秒，请按以下时间点输出分析：
{timeline}

每个时间点输出：
- time_s: 时间点
- scene: 场景名称（如"厨房不锈钢台面特写"）
- objects: 画面中所有可见物体（具体描述）
- text_elements: 画面中出现的所有文字
- action: 画面中发生什么事（详细描述动作、工具使用方式、前后变化）
- lighting: 光线条件
- camera: 镜头类型和角度
- mood: 氛围
- suitable_for: 痛点/产品展示/使用演示/效果对比/CTA/多场景适用/信任背书（选一个或多个，如果画面包括清洁前后对比必须标效果对比）

输出格式：
[
  {COLONtime_sCOLON0,COLONsceneCOLON"",COLONobjectsCOLON[],COLONtext_elementsCOLON"",COLONactionCOLON"",COLONlightingCOLON"",COLONcameraCOLON"",COLONmoodCOLON"",COLONsuitable_forCOLON""},
  ...
]"""

def generate_timeline_points(duration):
    """生成每0.5秒一个时间点"""
    points = []
    t = 0.0
    while t < duration:
        points.append(round(t, 1))
        t += 0.5
    # 确保最后一个点在duration
    if points[-1] < duration - 0.1:
        points.append(duration)
    return points

def read_video_as_base64(video_path, max_bytes=7*1024*1024):
    """读取视频为base64，如果太大就压缩"""
    data = open(video_path, 'rb').read()
    if len(data) <= max_bytes:
        return base64.b64encode(data).decode('utf-8')
    
    # 压缩
    compressed = f"/tmp/compressed_{Path(video_path).name}"
    subprocess.run([
        'ffmpeg', '-y', '-i', video_path,
        '-vf', 'scale=720:1280',
        '-crf', '28', '-c:v', 'libx264',
        '-preset', 'fast',
        compressed
    ], capture_output=True)
    
    data = open(compressed, 'rb').read()
    if len(data) <= max_bytes:
        return base64.b64encode(data).decode('utf-8')
    
    # 再压
    subprocess.run([
        'ffmpeg', '-y', '-i', video_path,
        '-vf', 'scale=480:854',
        '-crf', '30', '-c:v', 'libx264',
        '-preset', 'fast',
        compressed
    ], capture_output=True)
    data = open(compressed, 'rb').read()
    return base64.b64encode(data).decode('utf-8')

def call_omni(video_b64, prompt, system_prompt):
    """调用 Qwen-Omni API"""
    import dashscope
    from dashscope import MultiModalConversation
    
    messages = [
        {"role": "system", "content": [{"text": system_prompt}]},
        {"role": "user", "content": [
            {"video": f"data:video/mp4;base64,{video_b64}"},
            {"text": prompt}
        ]}
    ]
    
    response = MultiModalConversation.call(
        model='qwen3.5-omni-flash',
        messages=messages,
        result_format='message',
    )
    
    if response.status_code == 200:
        return response.output.choices[0].message.content[0]['text']
    else:
        raise Exception(f"API Error {response.status_code}: {response.message}")

def analyse_video(video_path, name, duration):
    print(f"\n{'='*60}")
    print(f"分析: {name} ({duration}s)")
    print(f"{'='*60}")
    
    # 生成时间点
    points = generate_timeline_points(duration)
    timeline_str = ', '.join([str(p) for p in points])
    print(f"共 {len(points)} 个时间点")
    
    # 读取视频
    print("读取视频...", flush=True)
    video_b64 = read_video_as_base64(video_path)
    print(f"视频大小: {len(video_b64)//1024}KB base64")
    
    # 调用 Omni
    user_prompt = USER_PROMPT_TEMPLATE.format(duration=duration, timeline=timeline_str)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"调用 Omni (第{attempt+1}次)...", flush=True)
            t0 = time.time()
            result = call_omni(video_b64, user_prompt, SYSTEM_PROMPT)
            dt = time.time() - t0
            print(f"完成 ({dt:.1f}s)", flush=True)
            
            # 解析 JSON
            # 找 JSON 部分
            json_start = result.find('[')
            json_end = result.rfind(']') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = result[json_start:json_end]
                data = json.loads(json_str)
            else:
                data = json.loads(result)
            
            # 保存结果
            output = {
                "filename": os.path.basename(video_path),
                "name": name,
                "duration": duration,
                "total_points": len(points),
                "scenes": data,
                "analysed_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
            out_file = os.path.join(OUT, f"{name}.json")
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            
            print(f"保存: {out_file}")
            print(f"场景点数: {len(data)}")
            return True
            
        except json.JSONDecodeError as e:
            print(f"JSON解析失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                # 存原始结果
                with open(os.path.join(OUT, f"{name}_raw.txt"), 'w') as f:
                    f.write(result)
                print(f"原始结果保存到 {name}_raw.txt")
                return False
        except Exception as e:
            print(f"失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                return False
    
    return False

# 主流程
if len(sys.argv) > 1:
    # 只跑指定的素材
    target_names = sys.argv[1:]
    targets = [t for t in TARGETS if t[1] in target_names or t[0] in target_names]
    if not targets:
        print(f"未找到指定素材: {target_names}")
        print(f"可用: {[t[1] for t in TARGETS]}")
        sys.exit(1)
else:
    targets = TARGETS

total = len(targets)
for i, (filename, name, duration) in enumerate(targets, 1):
    print(f"\n[{i}/{total}] {name}")
    video_path = os.path.join(RAW, filename)
    if not os.path.exists(video_path):
        print(f"文件不存在: {video_path}")
        continue
    
    success = analyse_video(video_path, name, duration)
    if success:
        print("✅ 成功")
    else:
        print("❌ 失败")
    
    # 避免限频
    if i < total:
        print("等待5秒...")
        time.sleep(5)

print(f"\n{'='*60}")
print("全部完成!")
print(f"输出目录: {OUT}")
