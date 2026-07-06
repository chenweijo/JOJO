import os, sys, base64, subprocess
os.environ['DASHSCOPE_API_KEY'] = base64.b64decode('c2std3MtSC5SWFlYSUhQLk9KcGIuTUVRQ0lFd2ZUOVo2M2R6M0YyRU94MjhUTFI2NmwwX3FhOHgzTHFWRjg3bUozVlp2QWlCejVxMWduYURtMHdCTkQ4U0QwNlIyRXNXY1ltVnZmR0ltNjJhdXZjWjlzQQ==').decode()

from dashscope.audio.tts_v2 import SpeechSynthesizer

TEXT = '大公鸡管家油污净，厨房专用强力去油配方，喷上去等几秒钟，油垢自己往下流。百洁布一擦，全部干净。意大利原装进口，百年清洁品牌。现在下单只要二十九块九到手两大瓶。'

voices = ['longanyang', 'longanhuan', 'longxiaochun', 'longxiaoxia', 'longanyun', 'longyingxiao', 'longyingjing', 'longyingling']

for v in voices:
    try:
        syn = SpeechSynthesizer(model='cosyvoice-v3-flash', voice=v)
        audio = syn.call(TEXT)
        out = f'/tmp/cosy_{v}.mp3'
        with open(out, 'wb') as f: f.write(audio)
        dur = subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',out]).decode().strip()
        print(f'OK: {v:20s} {len(audio)/1024:5.0f}KB {dur:>7}s')
    except Exception as e:
        print(f'FAIL: {v:20s} {str(e)[:60]}')
