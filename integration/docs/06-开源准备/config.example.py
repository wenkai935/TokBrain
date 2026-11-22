# 小七桥接配置样例（脱敏版）
# 复制为 config.py 后按你的环境修改。真实凭据请用环境变量或本地未入库文件注入，
# 不要把 token / api_key / 设备 ID 提交到 git。
import asyncio


async def before_wakeup(speaker, text, source):
    """
    处理收到的用户消息，并决定是否唤醒小智 AI

    - source: 唤醒来源
        - 'kws': 关键字唤醒
        - 'xiaoai': 小爱同学收到用户指令
    """
    if source == "kws":
        # 播放唤醒提示语
        await speaker.play(text="我在")
        # 返回 True 唤醒小智 AI
        return True

    if source == "xiaoai" and text == "召唤小智":
        # 打断原来的小爱同学
        await speaker.abort_xiaoai()
        # 等待 2 秒，让小爱 TTS 恢复可用
        await asyncio.sleep(2)
        # 播放唤醒提示语（如果你不使用自带的小爱 TTS，可以去掉上面的延时）
        await speaker.play(text="我在")
        # 唤醒小智 AI
        return True


async def after_wakeup(speaker):
    """
    退出唤醒状态
    """
    await speaker.play(text="好的，再见")


APP_CONFIG = {
    "wakeup": {
        # 自定义唤醒词列表（英文字母要全小写）
        "keywords": [
            "你好小七",  # 改成你自己的唤醒词，例如家人的称呼
        ],
        # 单独调整某个唤醒词的检测阈值（0-1，越小越容易唤醒，也越容易误唤醒），
        # 例如 {"你好小智": 0.1}；没列出的词使用默认值 0.2。
        "keyword_thresholds": {},
        # 唤醒检测保留的候选数，越大越不容易漏，也越耗 CPU（默认 8）。
        "max_active_paths": 16,
        # 同时运行几路唤醒检测，各路起点错开多少毫秒；任一路认出即唤醒。
        # 模型按 320 ms 一块处理音频，错开半块的两路漏掉的不是同一批；[0] 只用一路。
        "stream_offsets_ms": [0, 160],
        # 静音多久后自动退出唤醒（秒）
        "timeout": 20,
        # 对话中只说这些话（可带“好的”“谢谢”或唤醒词，如“你好小七，拜拜”）时马上退出唤醒，
        # 不再等超时；小七说话时可以先喊唤醒词打断再说。“关灯，拜拜”这样带着请求的，
        # 回答完再退出（见 README）。设成 [] 关闭。
        "exit_words": ["拜拜", "再见", "退出", "退下", "没事了", "不用了", "结束对话", "bye", "goodbye"],
        # 退出播报后的余音保护时间（ms），结束后清理唤醒识别状态。
        "exit_guard_ms": 300,
        # 语音识别结果回调
        "before_wakeup": before_wakeup,
        # 退出唤醒时的提示语（设置为空可关闭）
        "after_wakeup": after_wakeup,
    },
    "vad": {
        # 语音检测阈值（0-1，越小越灵敏）
        "threshold": 0.10,
        # 最小语音时长（ms）
        "min_speech_duration": 250,
        # 最小静默时长（ms）
        "min_silence_duration": 500,
        # 只说了很短一句（如“啊”“嗯”）时，多等一会儿再判定说完，
        # 这样“啊……国庆不是八天”不会被截成“啊”。
        "short_utterance_duration": 800,
        "short_utterance_silence": 1000,
        # 小七说完后只留这段时间避开余音，然后马上开始听（ms）
        "tts_end_guard_ms": 300,
        # 说完后服务端多久没有识别结果，就提示重说并重新开始听（秒）
        "no_reply_timeout": 5,
        "no_reply_prompt": "我没听清，再说一遍？",
    },
    "barge_in": {
        # 小七说话时喊唤醒词可以打断她，然后直接说新的问题。
        # LX06 走 lx06_capture 的硬件回采（ref_channel=6），不依赖下面的 echo_reference。
        "enabled": True,
        # 让音箱同时发回它正在播放的声音，桥接据此消除回声。需要本仓库的音箱客户端；
        # 旧客户端会忽略这个请求，照旧发送单声道录音（此时不能打断）。
        # LX06 由上面的 lx06_capture 单独配置，这里保持 False 不影响它。
        "echo_reference": False,
        # 打断后播放的提示音（音箱上的文件，留空则不播放）
        "prompt_tone": "/usr/share/common_sound/wakeup.opus",
        # 一口气说“你好小七，明天天气怎么样”时，唤醒词认出时已经过去一小段，
        # 从认出的位置往回多取这么多毫秒接着听，不丢开头。
        "keyword_tail_ms": 100,
        # 送去识别的声音再往前多取这么多毫秒（唤醒词结束到被认出最多晚约 0.4 s；
        # 带上一点“七”的尾音不影响识别）。
        "speech_lookback_ms": 350,
        # 刚放过声音的那一小段（播放中、停播后 echo_vad_hangover_ms 内），消除后剩下的小七的声音
        # 仍可能被当成说话；这段时间里说话检测用这个更高的阈值（真人说话约 1.0）。
        "echo_vad_threshold": 0.7,
        "echo_vad_hangover_ms": 300,
        # 打断时比较“回声消除后剩下的声音”和“消掉的回声”（dB）。设成数字后，
        # 低于它的唤醒当作小七自己的声音忽略；None 只记录在健康日志里。
        # 取 -24：LX06 实测真人正常音量落在 -19.2 ~ -21.6 dB（2026-10-28 02:05 实测），
        # 原先照搬 OH2P 的 -17 导致必须喊得更大声才能打断。
        # 若小七播报时出现“自己打断自己”，往上收紧到 -21；若仍嫌难打断，往下放到 -26。
        "near_end_min_db": -24,
        # 音箱录音：一个麦克风 + 播放回采声道（OH2P 的 hw:0,3 第 4 声道）
        "capture": {
            "pcm": "Capture",
            "channels": 4,
            "sample_rate": 48000,
            "mic_channel": 0,
            "ref_channel": 3,
            "mic_shift": 14,
            "ref_shift": 16,
        },
        "echo_canceller": {
            # 回声尾巴长度（帧，每帧 16 ms）；8 帧在 3 米、音量 75 时也能认出唤醒词
            "taps": 8,
            # 回声路径的平均时长（秒）
            "tau_s": 3.0,
            # 消除后放大多少倍。实测 SubbandEchoCanceller 净增益 1.0
            # （无播放时输出=输入），所以这一项纯粹是电平标定：
            # LX06 用 mic_shift 12 时说话 peak ≈ -11.9 dBFS，gain 2 → -5.9 dBFS，
            # 留 6 dB 余量；gain 4 会顶到 0 dBFS，响一点就削（实测 clip 0.73%）。
            # 与 mic_shift 反向联动：shift 回到 16 时这一项应为 32。
            "output_gain": 2,
        },
    },
    "music": {
        # 小七放本地音乐：“播放梁静茹的勇气”“放首歌”“暂停”“下一首”“继续播放”“这是什么歌”。
        # 音乐目录挂载到容器里的 library（见 deploy/xiaozhi/docker-compose.yml）。
        # 音箱用自带播放器从桥接拉歌；只在 OH2P 上验证过。
        "enabled": False,  # 设为 True 前先确认音箱机型（本地音乐只在 OH2P 验证过）
        "library": "/music",
        # 音箱从这个端口拉歌，xiaozhi 的 provider 和 Hermes 也从这里点歌
        "http_port": 4400,
        # 音箱访问桥接的地址，如 "http://192.168.1.10:4400"；留空则用音箱连桥接时的地址
        "base_url": "",
        # 放歌时喊“你好小七”，音乐降低多少分贝，对话结束再恢复；小七的声音不受影响
        "duck_db": 20,
        # 音乐比小七的声音低多少分贝（0 为一样响）
        "music_db": 0,
        # 能放的格式，同一首歌有多个文件时按这个顺序挑（音箱放不了 wma）
        "formats": ["mp3", "flac", "wav", "m4a"],
        # 不收进曲库的文件夹
        "exclude_dirs": ["Podcasts", "Lyrics", "Images", "Album Artwork"],
        # 同一位歌手的别名，例如 {"田馥甄": ["Hebe"]}
        "aliases": {},
        # 小七的回答，可以改；留空则不说话（例如 "stop": ""）
        "replies": {},
    },
    "tts_output": {
        # 可选："sherpa"（服务端音频流）或 "native_xiaomi"（音箱原生音色）
        # Docker 部署可通过 XIAOZHI_TTS_OUTPUT_MODE 环境变量覆盖。
        "mode": "native_xiaomi",
        "native_xiaomi": {
            # 第一段立即播放；后续文本按目标长度或短暂等待进行合并。
            "target_chars": 80,
            "max_chars": 120,
            "flush_delay_ms": 250,
            # 已合成但尚未播放的最大段数。
            "prefetch_segments": 2,
            "generate_timeout_ms": 5000,
            "play_timeout_ms": 10 * 60 * 1000,
            # miplayer 偶发不退出时，按音频时长动态终止播放。
            "play_min_timeout_ms": 15000,
            "play_timeout_grace_ms": 8000,
            "max_file_bytes": 8 * 1024 * 1024,
            "max_total_bytes": 8 * 1024 * 1024,
        },
    },
    "xiaozhi": {
        # 换成你自己的小智服务端地址（局域网 IP 或域名）
        "OTA_URL": "http://192.168.1.10:8003/xiaozhi/ota/",
        "WEBSOCKET_URL": "ws://192.168.1.10:8000/xiaozhi/v1/",
        "WEBSOCKET_ACCESS_TOKEN": "", #（可选）一般用不到这个值
        "VERIFICATION_CODE": "", # 首次对话时，验证码会在这里更新
        "DEVICE_ID": "", # 如果没有提示绑定设备，则将 DEVICE_ID 清空后，重启应用再次尝试
    },
}

# Project playback coordination: acquiring the lease does not start recording.
from playback_gate import GATE as _voice_playback_gate
_voice_original_before_wakeup = APP_CONFIG['wakeup'].get('before_wakeup')
async def _voice_before_wakeup(speaker, text, source):
    await _voice_playback_gate.acquire_async()
    try:
        accepted = await _voice_original_before_wakeup(speaker, text, source) if callable(_voice_original_before_wakeup) else True
        if not accepted:
            _voice_playback_gate.release()
        return accepted
    except BaseException:
        _voice_playback_gate.release()
        raise
APP_CONFIG['wakeup']['before_wakeup'] = _voice_before_wakeup

# LX06 B 方案：麦克风取 ch0，回声参考取 ch6（硬件播放回采）。
# 两者不同 → on_input_packet 走 AEC 路径；原来 ref==mic 只是"两槽装同一路"的退化形态。
# mic_shift 12 让 S16 用满 13 位有效精度（shift 16 时 peak 仅 518，只用了 9 位）。
# 注意 shift 12 本身不会让声音变大：output_gain 已同步从 64 降到 2 把电平标回去。
APP_CONFIG['lx06_capture'] = {'enabled': True, 'capture': {
    'channels': 8,
    'mic_channel': 0,
    'ref_channel': 6,
    'mic_shift': 12,
    'ref_shift': 16,
}}
