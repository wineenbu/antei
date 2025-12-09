import os
import json
import datetime
import asyncio
import uuid
import discord
from discord.ext import tasks
from flask import Flask

# === Flaskサーバー（Render動作用）===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# === Discord Bot ===
TOKEN = os.environ.get("DISCORD_TOKEN")
if TOKEN is None:
    raise ValueError("DISCORD_TOKEN が設定されていません。Render環境変数を確認してください。")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

DATA_FILE = "reminders.json"

# === JSON読み書き ===
def load_reminders():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reminders(reminders):
    with open(DATA_FILE, "w") as f:
        json.dump(reminders, f, indent=2)

# === 日時形式解析 ===
def parse_datetime_input(time_str: str) -> datetime.datetime:
    formats = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%m/%d %H:%M",
        "%H:%M",
    ]

    now = datetime.datetime.now()
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(time_str, fmt)
            if fmt == "%m/%d %H:%M":
                dt = dt.replace(year=now.year)
            elif fmt == "%H:%M":
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt
        except ValueError:
            continue

    raise ValueError("対応していない日時形式です。例: 2025-11-08T09:30")

# === JSTフォーマット ===
def format_jst_datetime(dt: datetime.datetime) -> str:
    jst = dt + datetime.timedelta(hours=9)
    return jst.strftime("%Y年%m月%d日 %H時%M分")

# === リマインダー処理 ===
@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.datetime.now(datetime.UTC).timestamp()
    reminders = load_reminders()
    remaining = []

    for r in reminders:
        if r.get("deleted", False):  # 削除済みは無視
            continue

        if r["time"] <= now:
            try:
                remind_dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
                formatted_time = format_jst_datetime(remind_dt)

                if r.get("type") == "channel":
                    channel = client.get_channel(r["channel_id"])
                    if channel:
                        embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                        embed.add_field(name="💬 内容", value=r["message"], inline=False)
                        embed.set_footer(text=f"設定者: <@{r['user_id']}>")
                        await channel.send(embed=embed)
                    else:
                        print(f"⚠️ Channel not found: {r}")

                else:  # DM宛て
                    user = await client.fetch_user(r["user_id"])
                    embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                    embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                    embed.add_field(name="💬 内容", value=r["message"], inline=False)
                    await user.send(embed=embed)

                # weeklyの場合は再設定
                if r.get("repeat") == "weekly":
                    next_time = datetime.datetime.fromtimestamp(r["time"], datetime.UTC) + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)

            except Exception as e:
                print(f"❌ Failed to send reminder: {e}")
        else:
            remaining.append(r)

    save_reminders(remaining)

# === 起動時 ===
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    print("Slash commands synced.")
    check_reminders.start()

# === /remindat (DM) ===
@tree.command(name="remindat", description="指定時刻にDMでリマインドを設定します")
async def remindat(interaction: discord.Interaction, time_str: str, message: str):
    remind_time = parse_datetime_input(time_str)
    remind_time_utc = remind_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    reminders.append({
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "type": "dm"
    })
    save_reminders(reminders)

    await interaction.response.send_message("⏰ リマインダーを設定しました！ /remindlist で確認できます。", ephemeral=True)

# === /remindhere ===
@tree.command(name="remindhere", description="このチャンネルにリマインドします")
async def remindhere(interaction: discord.Interaction, time_str: str, message: str):
    remind_time = parse_datetime_input(time_str)
    remind_time_utc = remind_time - datetime.timedelta(hours=9)

    reminders = load_reminders()
    reminders.append({
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "channel_id": interaction.channel.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "type": "channel"
    })
    save_reminders(reminders)

    await interaction.response.send_message("📌 このチャンネルにリマインドを設定しました。", ephemeral=True)

# === /remindeveryweek ===
@tree.command(name="remindeveryweek", description="毎週リマインドします（日本語の曜日指定OK）")
async def remindeveryweek(
    interaction: discord.Interaction,
    weekday: str,
    time_str: str,
    message: str,
    here: bool = False  # ← Trueならチャンネルに投下
):
    # 日本語 → 英語曜日マップ
    jp_weekdays = {
        "月": "mon", "月曜": "mon", "月曜日": "mon",
        "火": "tue", "火曜": "tue", "火曜日": "tue",
        "水": "wed", "水曜": "wed", "水曜日": "wed",
        "木": "thu", "木曜": "thu", "木曜日": "thu",
        "金": "fri", "金曜": "fri", "金曜日": "fri",
        "土": "sat", "土曜": "sat", "土曜日": "sat",
        "日": "sun", "日曜": "sun", "日曜日": "sun",
    }

    # 英語3文字もOK
    en_weekdays = {"mon","tue","wed","thu","fri","sat","sun"}

    # 正規化
    w = weekday.lower()
    if w in jp_weekdays:
        w = jp_weekdays[w]
    elif w not in en_weekdays:
        await interaction.response.send_message(
            "⚠️ 曜日は「月 / 月曜 / 月曜日 / mon」などで指定してください。",
            ephemeral=True
        )
        return

    weekday_num = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}[w]

    # 時刻パース（これはローカル時刻 = JST として扱う）
    base_time = parse_datetime_input(time_str)

    # 今日の同じ時刻の基準を作る（JST）
    now = datetime.datetime.now()
    target = now.replace(
        hour=base_time.hour,
        minute=base_time.minute,
        second=0,
        microsecond=0
    )

    # 次の該当曜日まで進める
    while target.weekday() != weekday_num or target <= now:
        target += datetime.timedelta(days=1)

    # 保存用に UTC に変換
    remind_time_utc = target - datetime.timedelta(hours=9)

    # リマインダー保存
    reminders = load_reminders()
    data = {
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": remind_time_utc.timestamp(),
        "message": message,
        "repeat": "weekly",
        "type": "channel" if here else "dm"
    }
    if here:
        data["channel_id"] = interaction.channel.id

    reminders.append(data)
    save_reminders(reminders)

    # JST 表示（設定確認用）
    formatted = format_jst_datetime(target)

    # 🔔 embed 形式で返信
    embed = discord.Embed(
        title="⏳ 毎週リマインダーを設定しました！",
        color=discord.Color.green()
    )
    embed.add_field(name="📅 曜日", value=weekday, inline=False)
    embed.add_field(name="🕒 時刻（JST）", value=formatted, inline=False)
    embed.add_field(name="💬 内容", value=message, inline=False)
    embed.add_field(
        name="📍 場所",
        value=("このチャンネルに投稿" if here else "DMで通知"),
        inline=False
    )
    embed.set_footer(text=f"設定者: {interaction.user.name}")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# === /remindlist ===
@tree.command(name="remindlist", description="リマインド一覧を表示")
async def remindlist(interaction: discord.Interaction):
    reminders = load_reminders()
    mine = [r for r in reminders if r["user_id"] == interaction.user.id and not r.get("deleted", False)]

    if not mine:
        await interaction.response.send_message("🔍 リマインダーはありません。", ephemeral=True)
        return

    text = ""
    for r in mine:
        dt = datetime.datetime.fromtimestamp(r["time"], datetime.UTC)
        text += f"UID: `{r['uid']}` | {format_jst_datetime(dt)} | {r['message']} | {r.get('repeat','once')}\n"

    await interaction.response.send_message(text, ephemeral=True)

# === /reminddelete ===
@tree.command(name="reminddelete", description="リマインドを削除する")
async def reminddelete(interaction: discord.Interaction, uid: str):
    reminders = load_reminders()
    found = False
    for r in reminders:
        if r.get("uid") == uid and r["user_id"] == interaction.user.id:
            r["deleted"] = True
            found = True

    save_reminders(reminders)

    if found:
        await interaction.response.send_message(f"🗑 削除しました: `{uid}`", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ UIDが見つかりません。/remindlist を確認してください", ephemeral=True)

# === 起動 ===
if __name__ == "__main__":
    from threading import Thread
    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    Thread(target=run_flask).start()

    client.run(TOKEN)
