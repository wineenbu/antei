# bot.py
import os
import json
import datetime
import uuid
import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask
import threading

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
                content = f"🔔 リマインダー\n🕒 {format_jst(dt)}\n💬 {r['message']}"
                if r.get("role_id"):
                    content = f"<@&{r['role_id']}> " + content

                if r["destination"] == "channel":
                    ch = client.get_channel(r["channel_id"])
                    if ch:
                        await ch.send(content)
                else:
                    user = await client.fetch_user(r["user_id"])
                    await user.send(content)

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

# === 曜日ボタン ===
class WeekdayButton(discord.ui.Button):
    def __init__(self, label, value, parent):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.value = value
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent.selected = self.value
        for item in self.parent.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"選択した曜日: {self.label}", view=self.parent)
        # 選択後にリマインダー作成
        await set_weekly_reminder(
            interaction=self.parent.interaction,
            mode=self.parent.mode,
            time=self.parent.time,
            destination=self.parent.destination,
            message=self.parent.message,
            channel=self.parent.channel,
            role=self.parent.role,
            weekday=self.value
        )

class WeekdaySelectView(discord.ui.View):
    def __init__(self, interaction, mode, time, destination, message, channel=None, role=None):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.mode = mode
        self.time = time
        self.destination = destination
        self.message = message
        self.channel = channel
        self.role = role
        self.selected = None

        days = [("月", "mon"), ("火", "tue"), ("水", "wed"), ("木", "thu"),
                ("金", "fri"), ("土", "sat"), ("日", "sun")]
        for label, value in days:
            self.add_item(WeekdayButton(label, value, self))

# === リマインダー作成関数 ===
async def set_weekly_reminder(interaction, mode, time, destination, message, channel=None, role=None, weekday=None):
    try:
        hhmm = datetime.datetime.strptime(time, "%H:%M")
        now = datetime.datetime.now()
        target = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)
        weekday_map = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        wd = weekday_map.get(weekday.lower())
        days_ahead = (wd - target.weekday()) % 7
        if days_ahead == 0 and target <= now:
            days_ahead = 7
        dt = target + datetime.timedelta(days=days_ahead)
        remind_ts = (dt - datetime.timedelta(hours=9)).timestamp()
    except Exception as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)
        return

    entry = {
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "time": remind_ts,
        "message": message,
        "destination": destination.value,
        "repeat": "weekly",
        "weekday": weekday
    }
    if destination.value == "channel" and channel:
        entry["channel_id"] = channel.id
    if role:
        entry["role_id"] = role.id

    reminders = load_reminders()
    reminders.append(entry)
    save_reminders(reminders)

    content = f"✅ リマインダー設定完了\n🕒 {format_jst(dt)}\n💬 {message}"
    if role:
        content = f"<@&{role.id}> " + content
    content += f"\n📍 {'DM' if destination.value=='dm' else f'#{channel.name}'}"

    try:
        if destination.value == "channel" and channel:
            await channel.send(content)
        else:
            user = await client.fetch_user(interaction.user.id)
            await user.send(content)
    except Exception as e:
        print("設定完了送信失敗:", e)

    await interaction.followup.send("リマインダーを設定しました！", ephemeral=True)

# === on_ready ===
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    check_reminders.start()

# === /remind ===
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
    mode=[app_commands.Choice(name="日時指定", value="at"), app_commands.Choice(name="毎週", value="weekly")],
    destination=[app_commands.Choice(name="DM", value="dm"), app_commands.Choice(name="チャンネル", value="channel")]
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
        await interaction.response.send_message("❌ チャンネルを指定してください。", ephemeral=True)
        return

    if mode.value == "weekly" and not weekday:
        view = WeekdaySelectView(interaction, mode, time, destination, message, channel, role)
        await interaction.response.send_message("曜日を選択してください:", view=view, ephemeral=True)
        return

    # atモードは即作成
    if mode.value == "at":
        try:
            dt = parse_datetime_input(time)
            remind_ts = (dt - datetime.timedelta(hours=9)).timestamp()
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        entry = {
            "uid": str(uuid.uuid4()),
            "user_id": interaction.user.id,
            "time": remind_ts,
            "message": message,
            "destination": destination.value
        }
        if destination.value == "channel":
            entry["channel_id"] = channel.id
        if role:
            entry["role_id"] = role.id

        reminders = load_reminders()
        reminders.append(entry)
        save_reminders(reminders)

        content = f"✅ リマインダー設定完了\n🕒 {format_jst(dt)}\n💬 {message}"
        if role:
            content = f"<@&{role.id}> " + content
        content += f"\n📍 {'DM' if destination.value=='dm' else f'#{channel.name}'}"

        try:
            if destination.value == "channel":
                await channel.send(content)
            else:
                user = await client.fetch_user(interaction.user.id)
                await user.send(content)
        except Exception as e:
            print("設定完了送信失敗:", e)

        await interaction.response.send_message("リマインダーを設定しました！", ephemeral=True)
        return

    # weeklyモードでweekday指定済みの場合
    if mode.value == "weekly" and weekday:
        await set_weekly_reminder(interaction, mode, time, destination, message, channel, role, weekday)

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
        content = f"⏰ {format_jst(dt)}\n💬 {r['message']}"
        await interaction.followup.send(content, view=ReminderDeleteView(r["uid"], interaction.user.id), ephemeral=True)

# === 起動 ===
def run_bot():
    client.run(TOKEN)

threading.Thread(target=run_bot, daemon=True).start()
app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
