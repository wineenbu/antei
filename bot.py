# bot.py
import os
import json
import datetime
import uuid
import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask

# === Flask（Render用）===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# === Discord Bot ===
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN が設定されていません")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

DATA_FILE = "reminders.json"

# === JSON ===
def load_reminders():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reminders(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# === 日時パース ===
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
                if dt < now:
                    dt += datetime.timedelta(days=1)
            return dt
        except ValueError:
            continue
    raise ValueError("日時形式が不正です")

# === JST 表示 ===
def format_jst(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    jst = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    return jst.strftime("%Y年%m月%d日 %H:%M")

# === リマインダー監視 ===
@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    reminders = load_reminders()
    remaining = []

    for r in reminders:
        if r.get("deleted"):
            continue

        if r["time"] <= now:
            try:
                dt = datetime.datetime.fromtimestamp(r["time"], datetime.timezone.utc)

                # --- テキスト形式送信 ---
                text_lines = []
                if r.get("role_id") and r["destination"] == "channel":
                    ch = client.get_channel(r["channel_id"])
                    role = ch.guild.get_role(r["role_id"]) if ch else None
                    if role:
                        text_lines.append(role.mention)
                text_lines.extend([
                    "🔔 リマインダー",
                    f"内容: {r['message']}",
                    f"時刻: {format_jst(dt)}",
                    f"設定者: <@{r['user_id']}>"
                ])
                text = "\n".join(text_lines)
                allowed = discord.AllowedMentions(roles=True)

                if r["destination"] == "channel":
                    ch = client.get_channel(r["channel_id"])
                    if ch:
                        await ch.send(content=text, allowed_mentions=allowed)
                else:
                    user = await client.fetch_user(r["user_id"])
                    await user.send(content=text)

                # --- 繰り返し処理 ---
                if r.get("repeat") == "weekly":
                    next_dt = dt + datetime.timedelta(days=7)
                    r["time"] = next_dt.timestamp()
                    remaining.append(r)

            except Exception as e:
                print("送信失敗:", e)
                remaining.append(r)
        else:
            remaining.append(r)

    save_reminders(remaining)

# === 削除ボタン ===
class ReminderDeleteView(discord.ui.View):
    def __init__(self, uid, owner_id):
        super().__init__(timeout=None)
        self.uid = uid
        self.owner_id = owner_id

    @discord.ui.button(label="❌ 削除", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("権限がありません", ephemeral=True)
            return

        reminders = load_reminders()
        for r in reminders:
            if r["uid"] == self.uid:
                r["deleted"] = True
                save_reminders(reminders)
                await interaction.response.edit_message(content="🗑 削除しました", view=None)
                return

# === on_ready ===
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    check_reminders.start()

# ======================
# /remind
# ======================
@tree.command(name="remind", description="リマインダーを設定します")
@app_commands.describe(
    mode="at=日時指定 / weekly=毎週",
    time="日時 or HH:MM",
    destination="送信先",
    channel="送信先チャンネル（destination=channel の場合）",
    role="メンションするロール（任意）",
    weekday="weekly の場合のみ",
    message="内容"
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="日時指定", value="at"),
        app_commands.Choice(name="毎週", value="weekly"),
    ],
    destination=[
        app_commands.Choice(name="DM", value="dm"),
        app_commands.Choice(name="チャンネル", value="channel"),
    ]
)
async def remind(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    time: str,
    destination: app_commands.Choice[str],
    message: str,
    channel: discord.TextChannel | None = None,
    role: discord.Role | None = None,
    weekday: str | None = None,
):
    if destination.value == "channel" and channel is None:
        await interaction.response.send_message(
            "❌ destination=channel の場合は channel を指定してください。",
            ephemeral=True
        )
        return

    try:
        if mode.value == "at":
            dt = parse_datetime_input(time)
        else:
            if not weekday:
                raise ValueError("weekly には weekday が必要です")
            hhmm = datetime.datetime.strptime(time, "%H:%M")
            now = datetime.datetime.now()
            target = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0)
            wmap = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
            wd = wmap.get(weekday.lower())
            if wd is None:
                raise ValueError("weekday は mon/tue/... を指定してください")
            days_ahead = (wd - target.weekday()) % 7
            if days_ahead == 0 and target <= now:
                days_ahead = 7
            dt = target + datetime.timedelta(days=days_ahead)

        ts = (dt - datetime.timedelta(hours=9)).timestamp()
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return

    entry = {
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": ts,
        "message": message,
        "destination": destination.value,
    }
    if destination.value == "channel":
        entry["channel_id"] = channel.id
    if role:
        entry["role_id"] = role.id
    if mode.value == "weekly":
        entry["repeat"] = "weekly"
        entry["weekday"] = weekday

    reminders = load_reminders()
    reminders.append(entry)
    save_reminders(reminders)

    # --- メッセージ形式 ---
    lines = []
    if role and destination.value == "channel":
        lines.append(role.mention)
    lines.extend([
        "🔔 リマインダー設定完了",
        f"内容: {message}",
        f"時刻: {format_jst(datetime.datetime.fromtimestamp(ts, datetime.timezone.utc))}",
    ])
    if mode.value == "weekly":
        lines.append(f"繰り返し: 毎週 ({weekday})")
    lines.append(f"設定者: {interaction.user.display_name}")
    text = "\n".join(lines)
    allowed = discord.AllowedMentions(roles=True)

    try:
        if destination.value == "channel":
            await channel.send(content=text, allowed_mentions=allowed)
        else:
            user = await client.fetch_user(interaction.user.id)
            await user.send(content=text)
    except Exception as e:
        print("設定完了送信失敗:", e)

    await interaction.response.send_message(content=text, ephemeral=True)

# === /remind_list ===
@tree.command(name="remind_list", description="リマインダー一覧")
async def remind_list(interaction: discord.Interaction):
    reminders = [r for r in load_reminders() if r["user_id"] == interaction.user.id and not r.get("deleted")]
    if not reminders:
        await interaction.response.send_message("📭 なし", ephemeral=True)
        return

    await interaction.response.send_message(f"📋 {len(reminders)} 件", ephemeral=True)

    for r in reminders:
        dt = datetime.datetime.fromtimestamp(r["time"], datetime.timezone.utc)
        embed = discord.Embed(title="⏰ リマインダー")
        embed.add_field(name="🕒 時刻", value=format_jst(dt))
        embed.add_field(name="💬 内容", value=r["message"])
        await interaction.followup.send(embed=embed, view=ReminderDeleteView(r["uid"], interaction.user.id), ephemeral=True)

# === 起動 ===
if __name__ == "__main__":
    import threading

    def run_bot():
        client.run(TOKEN)

    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
