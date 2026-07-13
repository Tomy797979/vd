"""
tts.py — Giọng đọc AI miễn phí của Microsoft (edge-tts).

Chạy tốt trên GitHub Actions runner (mạng mở). Trên Hugging Face free thường
bị chặn tới server TTS của Microsoft — đó là lý do bản GitHub này dùng được
còn app cũ phải upload audio thủ công.

Dùng:
    from tts import synthesize, VOICES, resolve_voice
    ok, msg = synthesize("Be still and know...", "voice.mp3",
                         voice="en-US-GuyNeural", rate="-8%")
"""

import os
import asyncio


# ── Giọng tuyển chọn cho Path of the Heart (nữ 45–65, tông ấm/đáng tin) ──
# Cho phép người dùng gõ tên ngắn thân thiện thay vì ShortName đầy đủ.
VOICES = {
    # --- NỮ ---
    "aria":     {"name": "en-US-AriaNeural",     "gender": "Nữ",  "note": "Ấm, thân thiện, tự nhiên — khuyên dùng cho POH"},
    "jenny":    {"name": "en-US-JennyNeural",    "gender": "Nữ",  "note": "Dịu dàng, kể chuyện"},
    "michelle": {"name": "en-US-MichelleNeural", "gender": "Nữ",  "note": "Trưởng thành, điềm tĩnh"},
    "ana":      {"name": "en-US-AnaNeural",      "gender": "Nữ",  "note": "Trẻ hơn, nhẹ nhàng"},
    "sonia":    {"name": "en-GB-SoniaNeural",    "gender": "Nữ",  "note": "Giọng Anh-Anh, thanh lịch"},
    # --- NAM ---
    "guy":      {"name": "en-US-GuyNeural",      "gender": "Nam", "note": "Trầm ấm, đáng tin — khuyên dùng cho POH"},
    "davis":    {"name": "en-US-DavisNeural",    "gender": "Nam", "note": "Điềm đạm, nghiêm túc"},
    "tony":     {"name": "en-US-TonyNeural",     "gender": "Nam", "note": "Năng lượng, tươi"},
    "roger":    {"name": "en-US-RogerNeural",    "gender": "Nam", "note": "Trung tính, rõ ràng"},
    "ryan":     {"name": "en-GB-RyanNeural",     "gender": "Nam", "note": "Giọng Anh-Anh, ấm"},
}

DEFAULT_VOICE = "en-US-AriaNeural"


def resolve_voice(voice):
    """Nhận tên ngắn ('guy') HOẶC ShortName đầy đủ ('en-US-GuyNeural')."""
    if not voice:
        return DEFAULT_VOICE
    key = voice.strip().lower()
    if key in VOICES:
        return VOICES[key]["name"]
    return voice.strip()  # giả định đã là ShortName hợp lệ


def list_voices_text():
    lines = ["Giọng đọc Microsoft (miễn phí) — tuyển chọn cho POH:"]
    for key, v in VOICES.items():
        lines.append(f"  {key:9} → {v['name']:24} [{v['gender']}] {v['note']}")
    return "\n".join(lines)


def synthesize(text, out_path, voice="en-US-AriaNeural", rate="-8%", volume="+0%"):
    """
    Tạo file MP3 từ text bằng edge-tts. Trả (ok, msg).
    rate: ví dụ "-12%".."+4%" (âm = chậm hơn). volume: "+0%", "-10%"...
    """
    if not text or not text.strip():
        return False, "Nội dung đọc rỗng."
    try:
        import edge_tts
    except ImportError:
        return False, "Chưa cài edge-tts (thêm 'edge-tts' vào requirements.txt)."

    voice_name = resolve_voice(voice)

    async def _run():
        communicate = edge_tts.Communicate(text, voice=voice_name,
                                           rate=rate, volume=volume)
        await communicate.save(out_path)

    try:
        asyncio.run(_run())
    except Exception as e:
        return False, f"edge-tts lỗi (mạng có thể bị chặn): {str(e)[:200]}"

    if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
        return True, f"OK ({voice_name})"
    return False, "edge-tts không tạo được file."
