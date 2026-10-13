# -*- coding: utf-8 -*-
"""自验：本机 SMTC 读取链路是否工作。

用 winsdk 自己的 MediaPlayer 播一段样本音频，制造一个真实的媒体会话，
然后用 SmtcTracker 去读。能读到说明 winsdk/SMTC 侧没问题，
用户端拿不到就只可能是浏览器/播放器不上报。
"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smtc_tracker import SmtcTracker, available

WAV = os.path.abspath("samples/e2e_speech.wav")
print("winsdk 可用:", available())
print("样本:", WAV, os.path.isfile(WAV))


async def main():
    from winsdk.windows.media.playback import MediaPlayer
    from winsdk.windows.media.core import MediaSource
    from winsdk.windows.storage import StorageFile

    f = await StorageFile.get_file_from_path_async(WAV)
    src = MediaSource.create_from_storage_file(f)
    mp = MediaPlayer()
    mp.source = src
    t = SmtcTracker()
    t.start()
    await asyncio.sleep(0.5)
    mp.play()
    for i in range(8):
        await asyncio.sleep(1)
        print(f"  t={i+1}s  标题={t.current_title()!r} "
              f"has_session={t.has_session()} "
              f"playing={t.playing()} pos={t.position()}")
    print("  describe:", t.describe())
    assert t.has_session(), "有会话在播，has_session 必须为 True"
    print("  会话识别 OK（即使标题为空）")
    try:
        mp.pause()
    except Exception:
        pass
    t.close()


try:
    asyncio.run(main())
except Exception as e:
    print("自验失败:", type(e).__name__, e)
