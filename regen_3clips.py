"""Regenerate the 3 broken vet-page audio clips with edge-tts th-TH-PremwadeeNeural,
re-encode to the deploy spec (32 kb/s mono 24 kHz), verify durations are real speech."""
import edge_tts, asyncio, subprocess, os, sys

OUT = "C:/Users/HUAWEI/Desktop/englishscool-scripts/tts-fix"
PHRASES = [
    ("how-long-like-this-naan-khae-nai-tts-f1", "เป็นมานานแค่ไหนคะ"),
    ("how-many-times-vomit-uak-tts-f1", "น้องอ้วกกี่ครั้งแล้วคะ"),
    ("brought-vaccine-book-samut-maa-tts-f1", "เอาสมุดวัคซีนมาด้วยไหมคะ"),
]

async def gen(name, text):
    raw = os.path.join(OUT, name + ".raw.mp3")
    final = os.path.join(OUT, name + ".mp3")
    for attempt in range(1, 6):
        try:
            await edge_tts.Communicate(text, "th-TH-PremwadeeNeural").save(raw)
            break
        except Exception as e:
            if attempt == 5:
                raise
            print(f"  retry {attempt} ({name}): {type(e).__name__}", flush=True)
            await asyncio.sleep(2 * attempt)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                    "-ac", "1", "-ar", "24000", "-b:a", "32k", final], check=True)
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", final], capture_output=True, text=True)
    dur = float(p.stdout.strip())
    print(f"{name}: duration={dur:.2f}s size={os.path.getsize(final)}B", flush=True)
    os.remove(raw)
    return name, dur

async def main():
    os.makedirs(OUT, exist_ok=True)
    results = {}
    for name, text in PHRASES:
        n, d = await gen(name, text)
        results[n] = d
    bad = [n for n, d in results.items() if d < 1.0]
    print("done; clips under 1s:", bad if bad else "none", flush=True)

asyncio.run(main())
