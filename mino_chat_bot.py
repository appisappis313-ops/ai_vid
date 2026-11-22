from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from google import genai
import asyncio
import glob
import os
import sys
import time

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(f"أهلاً وسهلاً بك يا {user_name}!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل أي سؤال وسأقوم بالرد عليه.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    try:
        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=user_text,
        )
        ai = response.text
    except Exception as e:
        ai = "حدث خطأ للأسف 😔"

    await update.message.reply_text(ai)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "videos")
os.makedirs(VIDEO_DIR, exist_ok=True)
video_lock = asyncio.Lock()

VIDEO_PROMPT = """You are an expert Python code generator.
Write a complete, runnable Python script that creates a cartoon animation using tkinter.

It must define:
STORY = "the story text to be read aloud, in Arabic"
DURATION = 15 # or a duration in seconds based on story

Rules:
- Write a function def draw_frame(t, canvas): that draws the COMPLETE scene as it looks at time t (0 <= t <= DURATION).
- draw_frame must depend only on t. Compute every position, color and size from t. No saved state, no time.sleep, no loops over time.
- Use an 800x500 tkinter Canvas (origin 0,0 at top-left). Use only canvas.create_rectangle, create_oval, create_polygon, create_line and create_text.
- Draw a full background first, then characters and objects, and move them smoothly with t. Follow the story exactly.
- CRITICAL: At the very end of the code, you MUST create the tkinter window, set up the Canvas, create a function to update time t from 0 to DURATION using canvas.after(33, ...), and call root.mainloop() so the script runs directly.
- Output ONLY raw Python code. No explanation, no markdown. Arabic only inside string quotes.
Story: """

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    story = " ".join(context.args).strip()
    if not story:
        await update.message.reply_text("اكتب القصة بعد الأمر، مثال:\n/video قطة صغيرة تلعب بالكرة في الحديقة")
        return

    if video_lock.locked():
        await update.message.reply_text("⏳ يوجد فيديو قيد التوليد، طلبك في الانتظار...")

    async with video_lock:
        await update.message.reply_text("🎬 جار توليد الفيديو، انتظر قليلاً...")
        tag = f"{update.effective_chat.id}_{int(time.time())}"
        code_path = os.path.join(VIDEO_DIR, f"{tag}.py")
        out_path = os.path.join(VIDEO_DIR, f"{tag}.mp4")
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-flash-lite-latest",
                contents=VIDEO_PROMPT + story,
            )
            code = response.text.replace("```python", "").replace("```", "").strip()
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            proc = await asyncio.create_subprocess_exec(
                sys.executable, os.path.abspath(__file__), "--render", code_path, out_path)
            try:
                await asyncio.wait_for(proc.wait(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                raise
            if proc.returncode != 0 or not os.path.exists(out_path):
                raise RuntimeError("video render failed")

            with open(out_path, "rb") as f:
                await update.message.reply_video(
                    video=f, read_timeout=120, write_timeout=120, connect_timeout=30
                )
        except Exception as e:
            print("video error:", e)
            await update.message.reply_text("حدث خطأ أثناء توليد الفيديو 😔")
        finally:
            for p in glob.glob(os.path.join(VIDEO_DIR, tag + "*")):
                try:
                    os.remove(p)
                except Exception:
                    pass


def render_video(code_path, out_path):
    import re
    import runpy
    import subprocess
    import tkinter as tk
    from PIL import ImageGrab
    import imageio_ffmpeg
    from gtts import gTTS
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    FPS = 24

    def audio_duration(path):
        r = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0

    silent = out_path + ".silent.mp4"
    voice = out_path + ".voice.mp3"

    rec = tk.Tk()
    rec.title("جار التسجيل... لا تغطِ هذه النافذة")
    rec.attributes("-topmost", True)
    rec.geometry("800x500+50+50")
    canvas = tk.Canvas(rec, width=800, height=500, highlightthickness=0)
    canvas.pack()
    rec.update()

    stray = []
    old_mainloop = tk.Misc.mainloop
    tk.Misc.mainloop = lambda self, n=0: stray.append(self)
    try:
        ns = runpy.run_path(code_path)
    finally:
        tk.Misc.mainloop = old_mainloop
    for w in stray:
        try:
            w.withdraw()
        except Exception:
            pass
    story, duration, draw_frame = ns["STORY"], float(ns["DURATION"]), ns["draw_frame"]

    gTTS(text=story, lang="ar").save(voice)
    total = max(duration, audio_duration(voice))
    frames = int(total * FPS)

    proc = subprocess.Popen(
        [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "800x500",
         "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for i in range(frames):
            t = min(i / FPS, duration)
            canvas.delete("all")
            draw_frame(t, canvas)
            rec.update()
            x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
            img = ImageGrab.grab(bbox=(x, y, x + canvas.winfo_width(), y + canvas.winfo_height()))
            proc.stdin.write(img.convert("RGB").resize((800, 500)).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        rec.destroy()

    subprocess.run([FFMPEG, "-y", "-i", silent, "-i", voice, "-c:v", "copy",
                    "-c:a", "aac", "-shortest", out_path],
                   stderr=subprocess.DEVNULL, check=True)


def main():
    app = Application.builder().token(TOKEN).read_timeout(30).connect_timeout(30).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("video", video_command, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--render":
        render_video(sys.argv[2], sys.argv[3])
    else:
        main()
