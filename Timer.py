import os
import discord
from discord.ext import tasks
from discord.ext import commands
import datetime
import asyncio

intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)

# タイマー用のリスト
timers = []

class Timer:
    def __init__(self, user_id, time_delta, message):
        self.user_id = user_id
        self.time_delta = time_delta
        self.message = message
        self.created_at = datetime.datetime.now()

    def expiration_time(self):
        return self.created_at + self.time_delta

# === タイマーコマンド ===
@client.command(name="timer")
async def set_timer(ctx, time: str, *, message: str):
    """タイマーをセットするコマンド"""
    # タイマーの時間を分かりやすくパースする
    try:
        if time[-1] == 'm':  # 分
            minutes = int(time[:-1])
            delta = datetime.timedelta(minutes=minutes)
        elif time[-1] == 'h':  # 時間
            hours = int(time[:-1])
            delta = datetime.timedelta(hours=hours)
        elif time[-1] == 's':  # 秒
            seconds = int(time[:-1])
            delta = datetime.timedelta(seconds=seconds)
        else:
            raise ValueError("無効な時間形式です")

        # タイマーをリストに追加
        timer = Timer(ctx.author.id, delta, message)
        timers.append(timer)

        await ctx.send(f"⏳ {time}後に「{message}」のリマインダーをセットしました！")
        
        # タイマーが終了したら通知を送信
        await asyncio.sleep(delta.total_seconds())  # 指定された時間が経過するのを待機

        if timer in timers:  # まだリストにあるかを確認（キャンセルされていなければ）
            user = await client.fetch_user(timer.user_id)
            await user.send(f"⏰ タイマー終了！: {message}")

            timers.remove(timer)  # 終了したタイマーをリストから削除
    except ValueError as e:
        await ctx.send(f"⚠️ エラー: {e}")

# === タイマーキャンセルコマンド ===
@client.command(name="canceltimer")
async def cancel_timer(ctx):
    """タイマーをキャンセルするコマンド"""
    global timers
    timers = [timer for timer in timers if timer.user_id != ctx.author.id]  # 自分のタイマーだけ削除

    await ctx.send("⛔ あなたのタイマーはキャンセルされました！")

# === Bot起動時イベント ===
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")

# === Bot起動 ===
TOKEN = os.getenv("DISCORD_TOKEN")
client.run(TOKEN)
