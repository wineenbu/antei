# bot.py
# 統合版 /remind (日時指定 / 毎週) + 送信先選択ボタン + ephemeral 選択
# 既存 remind_list / 削除ボタンロジックを保持、Flask keepalive あり

import os
import json
import datetime
import uuid
import threading
import traceback

import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask

# -------------------------
# Flask keep-alive (Render)
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# -------------------------
# Bot / config
# -------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if TOKEN is None:
    raise RuntimeError("DISCORD_TOKEN is not set in environment")

intents = discord.Intents.default()
# If you need message_content for other features, enable it here.
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

DATA_FILE = "reminders.json"

# -------------------------
# Storage helpers
# -------------------------
def load_reminders():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        # if file corrupted, return empty but log
        print("⚠️ load_reminders failed; returning []")
        traceback.print_exc()
        return []

def save_reminders(reminders):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(reminders, f, ensure_ascii=False, indent=2)
    except Exception:
        print("⚠️ save_reminders failed")
        traceback.print_exc()

# -------------------------
# Time parsing / formatting
# -------------------------
def parse_datetime_input(time_str: str) -> datetime.datetime:
    """
    Accepts:
    - YYYY-MM-DDTHH:MM
    - YYYY-MM-DD HH:MM
    - YYYY/MM/DD HH:MM
    - MM/DD HH:MM (this year)
    - HH:MM (today or tomorrow if time already passed)
    Returns naive datetime (local) — we treat user input as JST (as before).
    """
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
                # if it's already past today, shift to next day
                if dt <= now:
                    dt = dt + datetime.timedelta(days=1)
            return dt
        except ValueError:
            continue
    raise ValueError("対応していない日時形式です。例: 2025-11-08T09:30 または 14:30")

def format_jst_datetime(dt: datetime.datetime) -> str:
    """
    dt should be timezone-aware (UTC) or naive considered as UTC.
    Output JST formatted string.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    jst = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    return jst.strftime("%Y年%m月%d日 %H時%M分")

# -------------------------
# Reminder checker loop
# -------------------------
@tasks.loop(seconds=30)
async def check_reminders():
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    reminders = load_reminders()
    remaining = []

    for r in reminders:
        if r.get("deleted", False):
            continue
        try:
            r_time = float(r["time"])
        except Exception:
            # bad data, skip it (drop)
            print(f"⚠️ reminder has invalid time and will be skipped: {r}")
            continue

        if r_time <= now_ts:
            try:
                remind_dt = datetime.datetime.fromtimestamp(r_time, datetime.timezone.utc)
                formatted = format_jst_datetime(remind_dt)

                embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                embed.add_field(name="🕒 時刻", value=formatted, inline=False)
                embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)
                embed.set_footer(text=f"設定者: <@{r.get('user_id')}>")

                if r.get("type") == "channel" and r.get("channel_id"):
                    ch = client.get_channel(r.get("channel_id"))
                    if ch:
                        await ch.send(embed=embed)
                    else:
                        print(f"⚠️ Channel not found for reminder uid={r.get('uid')}")
                else:
                    try:
                        user = await client.fetch_user(r.get("user_id"))
                        await user.send(embed=embed)
                    except Exception as e:
                        print(f"❌ Failed to DM reminder uid={r.get('uid')} -> {e}")

                # if weekly, reschedule to next week
                if r.get("repeat") == "weekly":
                    next_time = remind_dt + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)
                else:
                    # one-shot: do not append (so it's removed)
                    pass

            except Exception as e:
                print(f"❌ Exception while sending reminder uid={r.get('uid')}: {e}")
                traceback.print_exc()
                # keep it so we can retry later
                remaining.append(r)
        else:
            remaining.append(r)

    save_reminders(remaining)

# -------------------------
# Delete button view (keeps existing behavior)
# -------------------------
class ReminderDeleteView(discord.ui.View):
    def __init__(self, uid: str, owner_id: int):
        super().__init__(timeout=None)
        self.uid = uid
        self.owner_id = owner_id

    @discord.ui.button(label="❌ 削除", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⚠️ 操作する権限がありません。", ephemeral=True)
            return

        reminders = load_reminders()
        found = False
        for r in reminders:
            if r.get("uid") == self.uid and r.get("user_id") == interaction.user.id:
                r["deleted"] = True
                found = True
                break

        if not found:
            await interaction.response.send_message("⚠️ リマインダーが見つからないか、既に削除されています。", ephemeral=True)
            return

        save_reminders(reminders)

        button.disabled = True
        try:
            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
            if embed:
                embed.set_footer(text="🗑 削除済み")
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.edit_message(content="🗑 削除済み", view=self)
        except Exception:
            await interaction.response.send_message("🗑 削除しました。", ephemeral=True)

# -------------------------
# on_ready
# -------------------------
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user} ({client.user.id})")
    try:
        await tree.sync()
        print("🌐 Slash commands synced.")
    except Exception as e:
        print("⚠️ tree.sync failed:", e)

    if not check_reminders.is_running():
        check_reminders.start()

# -------------------------
# Unified /remind command
# -------------------------
@tree.command(
    name="remind",
    description="日時指定 (1回) または 毎週（曜日）リマインドを設定します。送信先と表示範囲はボタンで選択します。"
)
@app_commands.describe(
    mode="モード: at=日時指定(1回), weekly=毎週",
    time="at の場合は日時(例: 2025-11-08T09:30, 09:30 等)。weekly の場合は HH:MM",
    weekday="weekly の場合は曜日 (mon/tue/... または 月/火/... など)",
    message="リマインド内容"
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="日時指定 (1回)", value="at"),
        app_commands.Choice(name="毎週リマインド", value="weekly"),
    ]
)
async def remind(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    time: str,
    message: str,
    weekday: str | None = None
):
    # parse and compute timestamp for first occurrence (JST converted to UTC timestamp)
    try:
        if mode.value == "at":
            dt_local = parse_datetime_input(time)  # treat as local/JST naive
            # convert to UTC timestamp by subtracting 9 hours
            remind_ts = (dt_local - datetime.timedelta(hours=9)).timestamp()
            repeat_flag = False
            weekday_store = None
        else:
            # weekly mode: require weekday parameter
            if not weekday:
                await interaction.response.send_message("❌ weekly モードでは weekday を指定してください（例: fri または 金）", ephemeral=True)
                return

            # normalize weekday (allow Japanese characters)
            jp_map = {"月":"mon","火":"tue","水":"wed","木":"thu","金":"fri","土":"sat","日":"sun"}
            w = weekday.lower()
            if w in jp_map:
                w = jp_map[w]
            if w not in {"mon","tue","wed","thu","fri","sat","sun"}:
                await interaction.response.send_message("❌ 曜日は mon/tue/... または 月/火/... で指定してください。", ephemeral=True)
                return

            # parse HH:MM
            try:
                hhmm = datetime.datetime.strptime(time, "%H:%M")
            except Exception:
                await interaction.response.send_message("❌ weekly の time は HH:MM 形式で指定してください（例: 14:30）", ephemeral=True)
                return

            now = datetime.datetime.now()
            # create a candidate target in JST using today's date + given time
            candidate = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)
            weekday_num = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}[w]
            days_ahead = (weekday_num - candidate.weekday()) % 7
            if days_ahead == 0 and candidate <= now:
                days_ahead = 7
            target = candidate + datetime.timedelta(days=days_ahead)
            # convert JST datetime to UTC timestamp
            remind_ts = (target - datetime.timedelta(hours=9)).timestamp()
            repeat_flag = True
            weekday_store = w

    except ValueError as e:
        await interaction.response.send_message(f"❌ 時刻パースエラー: {e}", ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"❌ 内部エラー: {e}", ephemeral=True)
        traceback.print_exc()
        return

    # Define Views for selection flow
    class ChooseVisibilityView(discord.ui.View):
        def __init__(self, dest_type: str, ts: float, repeat_f: bool, weekday_val: str | None, message_text: str):
            super().__init__(timeout=60)
            self.dest_type = dest_type
            self.ts = ts
            self.repeat_f = repeat_f
            self.weekday_val = weekday_val
            self.message_text = message_text

        async def do_save_and_confirm(self, interaction2: discord.Interaction, ephemeral_choice: bool):
            reminders = load_reminders()
            uid = str(uuid.uuid4())
            entry = {
                "uid": uid,
                "user_id": interaction2.user.id,
                "time": self.ts,
                "message": self.message_text,
                "type": self.dest_type,
                "ephemeral_choice": bool(ephemeral_choice),
            }
            if self.repeat_f:
                entry["repeat"] = "weekly"
                entry["weekday"] = self.weekday_val
            if self.dest_type == "channel":
                entry["channel_id"] = interaction2.channel.id

            reminders.append(entry)
            save_reminders(reminders)

            # Confirmation embed (ephemeral)
            confirm = discord.Embed(title="✅ リマインダー設定完了", color=discord.Color.green())
            try:
                dt_obj = datetime.datetime.fromtimestamp(self.ts, datetime.timezone.utc)
                confirm.add_field(name="🕒 時刻", value=format_jst_datetime(dt_obj), inline=False)
            except Exception:
                confirm.add_field(name="🕒 時刻", value=str(self.ts), inline=False)
            confirm.add_field(name="💬 内容", value=self.message_text, inline=False)
            confirm.add_field(name="📍 送信先", value=("DM" if self.dest_type=="dm" else "このチャンネル"), inline=False)
            if self.repeat_f:
                confirm.add_field(name="🔁 繰り返し", value=f"毎週 ({self.weekday_val})", inline=False)
            confirm.set_footer(text="※実際の送信は DM または チャンネルへ通常メッセージとして送られます（将来 ephemeral にすることは不可）")

            await interaction2.response.send_message(embed=confirm, ephemeral=True)

        @discord.ui.button(label="🔒 自分だけに見える (ephemeral)", style=discord.ButtonStyle.secondary)
        async def private_button(self, interaction2: discord.Interaction, button):
            await self.do_save_and_confirm(interaction2, True)

        @discord.ui.button(label="🌐 全員に見える (公開)", style=discord.ButtonStyle.danger)
        async def public_button(self, interaction2: discord.Interaction, button):
            await self.do_save_and_confirm(interaction2, False)

    class ChooseDestinationView(discord.ui.View):
        def __init__(self, ts: float, repeat_f: bool, weekday_val: str | None, message_text: str):
            super().__init__(timeout=60)
            self.ts = ts
            self.repeat_f = repeat_f
            self.weekday_val = weekday_val
            self.message_text = message_text

        @discord.ui.button(label="📩 DM に送る", style=discord.ButtonStyle.primary)
        async def dm_button(self, interaction2: discord.Interaction, button):
            # after choosing destination, ask visibility (ephemeral)
            await interaction2.response.send_message("🔒 次に『誰に見えるか』を選択してください。", ephemeral=True)
            view = ChooseVisibilityView("dm", self.ts, self.repeat_f, self.weekday_val, self.message_text)
            await interaction2.followup.send("公開範囲を選択してください：", view=view, ephemeral=True)

        @discord.ui.button(label="📢 このチャンネルに送る", style=discord.ButtonStyle.success)
        async def channel_button(self, interaction2: discord.Interaction, button):
            await interaction2.response.send_message("🔒 次に『誰に見えるか』を選択してください。", ephemeral=True)
            view = ChooseVisibilityView("channel", self.ts, self.repeat_f, self.weekday_val, self.message_text)
            await interaction2.followup.send("公開範囲を選択してください：", view=view, ephemeral=True)

    view = ChooseDestinationView(remind_ts, repeat_flag, weekday_store if repeat_flag else None, message)
    # initial ephemeral prompt with destination buttons
    await interaction.response.send_message("📍 送信先を選んでください（60秒）", view=view, ephemeral=True)

# -------------------------
# remind_list (保持)
# -------------------------
@tree.command(name="remind_list", description="設定されているリマインダーを一覧表示します")
async def remind_list(interaction: discord.Interaction):
    reminders = load_reminders()
    user_id = interaction.user.id

    user_reminders = [
        r for r in reminders
        if r.get("user_id") == user_id and not r.get("deleted", False)
    ]

    if not user_reminders:
        await interaction.response.send_message("📭 現在、設定されているリマインダーはありません。", ephemeral=True)
        return

    weekday_jp = {
        "mon": "月曜日", "tue": "火曜日", "wed": "水曜日",
        "thu": "木曜日", "fri": "金曜日", "sat": "土曜日", "sun": "日曜日"
    }

    await interaction.response.send_message(f"📋 あなたのリマインダーは **{len(user_reminders)} 件** あります。", ephemeral=True)

    for r in user_reminders:
        # time -> try to format timestamp; if not a timestamp, show raw
        try:
            dt = datetime.datetime.fromtimestamp(r["time"], datetime.timezone.utc)
            formatted_time = format_jst_datetime(dt)
        except Exception:
            formatted_time = str(r.get("time"))

        repeat = r.get("repeat", "なし")
        embed = discord.Embed(title="⏰ リマインダー", color=discord.Color.blurple())
        embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
        embed.add_field(name="🔁 繰り返し", value=repeat, inline=False)
        embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)

        if r.get("repeat") == "weekly":
            w = r.get("weekday", "?")
            embed.add_field(name="📅 曜日", value=weekday_jp.get(w, "不明"), inline=False)

        view = ReminderDeleteView(r["uid"], user_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# -------------------------
# Start bot + Flask (Render-compatible)
# -------------------------
if __name__ == "__main__":
    # run bot in background thread; flask runs in main thread (Render expects a web server)
    def run_bot():
        client.run(TOKEN)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
