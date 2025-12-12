# bot.py
# /remind 統合版（送信先選択 + ephemeral 選択）＋既存 remind_list / 削除ボタン維持
import os
import json
import datetime
import uuid
import discord
from discord import app_commands
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
    raise ValueError("DISCORD_TOKEN が設定されていません。Renderの環境変数を確認してください。")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

DATA_FILE = "reminders.json"

# === JSON読み書き ===
def load_reminders():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_reminders(reminders):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, ensure_ascii=False, indent=2)


# === 日時パース ===
def parse_datetime_input(time_str: str) -> datetime.datetime:
    """
    受け入れる形式:
      - YYYY-MM-DDTHH:MM
      - YYYY-MM-DD HH:MM
      - YYYY/MM/DD HH:MM
      - MM/DD HH:MM (今年)
      - HH:MM (今日 or 明日 -> 今日の時刻が過ぎていれば翌日)
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
                # もし今日の時刻が過ぎているなら翌日
                if dt < now:
                    dt = dt + datetime.timedelta(days=1)
            return dt
        except ValueError:
            continue

    raise ValueError("対応していない日時形式です。例: 2025-11-08T09:30 または 14:30")


# === JST 表示形式 ===
def format_jst_datetime(dt: datetime.datetime) -> str:
    # dt は naive でも timezone-aware でも扱えるように
    if dt.tzinfo is None:
        # treat as local naive (we've stored UTC timestamps normally) — caller should pass aware
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    jst = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    return jst.strftime("%Y年%m月%d日 %H時%M分")


# === リマインダー監視タスク ===
@tasks.loop(seconds=30)
async def check_reminders():
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    reminders = load_reminders()
    remaining = []

    for r in reminders:
        if r.get("deleted", False):
            continue

        # times may be stored either as timestamp (float/int) or as string -> try to convert
        try:
            r_time = float(r["time"])
        except Exception:
            # invalid entry: skip keeping it
            continue

        if r_time <= now_ts:
            try:
                remind_dt = datetime.datetime.fromtimestamp(r_time, datetime.timezone.utc)
                formatted_time = format_jst_datetime(remind_dt)

                # 組み立て embed
                embed = discord.Embed(title="🔔 リマインダー", color=discord.Color.green())
                embed.add_field(name="🕒 時刻", value=formatted_time, inline=False)
                embed.add_field(name="💬 内容", value=r.get("message", "（内容なし）"), inline=False)
                embed.set_footer(text=f"設定者: <@{r.get('user_id')}>")

                # 送信先
                if r.get("type") == "channel" and r.get("channel_id"):
                    channel = client.get_channel(r.get("channel_id"))
                    if channel:
                        await channel.send(embed=embed)
                    else:
                        print(f"⚠️ Channel not found for reminder uid={r.get('uid')}")
                else:
                    # DM
                    try:
                        user = await client.fetch_user(r.get("user_id"))
                        await user.send(embed=embed)
                    except Exception as e:
                        print(f"❌ Failed to DM for uid={r.get('uid')} -> {e}")

                # 繰り返し処理（weekly）
                if r.get("repeat") == "weekly":
                    # 次の週へ
                    next_time = remind_dt + datetime.timedelta(days=7)
                    r["time"] = next_time.timestamp()
                    remaining.append(r)
                else:
                    # 一回限りは残さない
                    pass

            except Exception as e:
                print(f"❌ Failed to send reminder uid={r.get('uid')}: {e}")
                # 送信に失敗した場合は念のため残す（次回再トライ）
                remaining.append(r)
        else:
            # まだ未来のものは保持
            remaining.append(r)

    save_reminders(remaining)


# === 削除ボタン用 View（既存ロジックを保持） ===
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

        # ボタンを無効化してメッセージを更新
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


# === on_ready ===
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    try:
        await tree.sync()
        print("🌐 Slash commands synced.")
    except Exception as e:
        print("⚠️ tree.sync failed:", e)

    if not check_reminders.is_running():
        check_reminders.start()


# ==========================
# === 統合 /remind コマンド ===
# フロー:
# 1) ユーザーが /remind mode:at/weekly time: message: (weekday optional) を入力
# 2) Bot は「送信先選択 (DM / チャンネル)」ボタンを表示（ephemeral）
# 3) ユーザーが送信先を選択したら「誰に見えるか (自分だけ/全員)」を選択するボタンを表示
# 4) 選択に応じて JSON に保存し、完了メッセージを表示（ephemeral の選択は確認メッセージに適用）
# 注意: 実際のリマインドは DM か チャンネルへ通常メッセージで送信（後述の注意あり）
# ==========================
@tree.command(
    name="remind",
    description="日時指定または毎週のリマインドを設定します（送信先と表示範囲をボタンで選べます）"
)
@app_commands.describe(
    mode="通知モード: at=日時指定, weekly=毎週",
    time="日時 or 時刻（at の場合: 例 2025-11-08T09:30 or 09:30。weekly の場合: HH:MM）",
    weekday="weekly の場合の曜日 (mon/tue/... または 月/火/...)",
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
    # parse time depending on mode
    try:
        if mode.value == "at":
            dt = parse_datetime_input(time)  # naive local time treated as JST
            remind_time_utc = (dt - datetime.timedelta(hours=9)).timestamp()
            repeat = None
            weekday_store = None
        else:  # weekly
            # weekday may be provided as 'mon' or '月'
            if weekday is None:
                await interaction.response.send_message("❌ 毎週モードでは weekday を指定してください（例: fri または 金）", ephemeral=True)
                return

            # normalize weekday
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
                await interaction.response.send_message("❌ weekly の time は HH:MM の形式で指定してください。例: 14:30", ephemeral=True)
                return

            # compute next occurrence in JST, then convert to UTC timestamp
            now = datetime.datetime.now()
            # build candidate in JST (year/month/day using today)
            target = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)
            weekday_map_num = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
            target_weekday = weekday_map_num[w]
            days_ahead = (target_weekday - target.weekday()) % 7
            if days_ahead == 0 and target <= now:
                days_ahead = 7
            target = target + datetime.timedelta(days=days_ahead)
            remind_time_utc = (target - datetime.timedelta(hours=9)).timestamp()
            repeat = "weekly"
            weekday_store = w

    except ValueError as e:
        await interaction.response.send_message(f"❌ 時刻パースエラー: {e}", ephemeral=True)
        return

    # --- 送信先を選ぶビュー（DM / Channel） ---
    class ChooseDestinationView(discord.ui.View):
        def __init__(self, remind_time_ts, repeat_flag, weekday_val, message_text):
            super().__init__(timeout=60)
            self.remind_time_ts = remind_time_ts
            self.repeat_flag = repeat_flag
            self.weekday_val = weekday_val
            self.message_text = message_text

        @discord.ui.button(label="📩 DM に送る", style=discord.ButtonStyle.primary)
        async def choose_dm(self, button_interaction: discord.Interaction, button):
            # after choosing destination, ask visibility
            await button_interaction.response.defer()
            await button_interaction.followup.send("🔒 次に『誰に見えるか』を選択してください。", ephemeral=True)
            await button_interaction.followup.send(view=ChooseVisibilityView(self.remind_time_ts, "dm", self.repeat_flag, self.weekday_val, self.message_text), ephemeral=True)

        @discord.ui.button(label="📢 このチャンネルに送る", style=discord.ButtonStyle.success)
        async def choose_channel(self, button_interaction: discord.Interaction, button):
            await button_interaction.response.defer()
            await button_interaction.followup.send("🔒 次に『誰に見えるか』を選択してください。", ephemeral=True)
            await button_interaction.followup.send(view=ChooseVisibilityView(self.remind_time_ts, "channel", self.repeat_flag, self.weekday_val, self.message_text), ephemeral=True)

    # --- 誰に見えるかを選ぶ View（ephemeral 選択） ---
    class ChooseVisibilityView(discord.ui.View):
        def __init__(self, remind_time_ts, dest_type, repeat_flag, weekday_val, message_text):
            super().__init__(timeout=60)
            self.remind_time_ts = remind_time_ts
            self.dest_type = dest_type
            self.repeat_flag = repeat_flag
            self.weekday_val = weekday_val
            self.message_text = message_text

        @discord.ui.button(label="🔒 自分だけに見える (ephemeral)", style=discord.ButtonStyle.secondary)
        async def vis_private(self, vis_interaction: discord.Interaction, button):
            await vis_interaction.response.defer()
            # 保存処理
            reminders = load_reminders()
            uid = str(uuid.uuid4())
            entry = {
                "uid": uid,
                "user_id": vis_interaction.user.id,
                "time": self.remind_time_ts,
                "message": self.message_text,
                "type": self.dest_type,
                "ephemeral_choice": True,   # stored for reference (note: actual scheduled message can't be ephemeral)
            }
            if self.repeat_flag:
                entry["repeat"] = "weekly"
                entry["weekday"] = self.weekday_val
            if self.dest_type == "channel":
                entry["channel_id"] = vis_interaction.channel.id

            reminders.append(entry)
            save_reminders(reminders)

            # 確認メッセージ（ephemeral）
            confirm_embed = discord.Embed(title="✅ リマインダー設定完了", color=discord.Color.green())
            # show JST display of time
            ts = datetime.datetime.fromtimestamp(self.remind_time_ts, datetime.timezone.utc)
            confirm_embed.add_field(name="🕒 時刻", value=format_jst_datetime(ts), inline=False)
            confirm_embed.add_field(name="💬 内容", value=self.message_text, inline=False)
            confirm_embed.add_field(name="📍 送信先", value=("DM" if self.dest_type=="dm" else "このチャンネル"), inline=False)
            if self.repeat_flag:
                confirm_embed.add_field(name="🔁 繰り返し", value=f"毎週 ({self.weekday_val})", inline=False)
            confirm_embed.set_footer(text="※注意: 実際のリマインド送信はDMかチャンネルへ通常メッセージで行います（将来の送信を ephemeral にすることはできません）")

            await vis_interaction.followup.send(embed=confirm_embed, ephemeral=True)

        @discord.ui.button(label="🌐 全員に見える (公開)", style=discord.ButtonStyle.danger)
        async def vis_public(self, vis_interaction: discord.Interaction, button):
            await vis_interaction.response.defer()
            reminders = load_reminders()
            uid = str(uuid.uuid4())
            entry = {
                "uid": uid,
                "user_id": vis_interaction.user.id,
                "time": self.remind_time_ts,
                "message": self.message_text,
                "type": self.dest_type,
                "ephemeral_choice": False,
            }
            if self.repeat_flag:
                entry["repeat"] = "weekly"
                entry["weekday"] = self.weekday_val
            if self.dest_type == "channel":
                entry["channel_id"] = vis_interaction.channel.id

            reminders.append(entry)
            save_reminders(reminders)

            confirm_embed = discord.Embed(title="✅ リマインダー設定完了", color=discord.Color.green())
            ts = datetime.datetime.fromtimestamp(self.remind_time_ts, datetime.timezone.utc)
            confirm_embed.add_field(name="🕒 時刻", value=format_jst_datetime(ts), inline=False)
            confirm_embed.add_field(name="💬 内容", value=self.message_text, inline=False)
            confirm_embed.add_field(name="📍 送信先", value=("DM" if self.dest_type=="dm" else "このチャンネル"), inline=False)
            if self.repeat_flag:
                confirm_embed.add_field(name="🔁 繰り返し", value=f"毎週 ({self.weekday_val})", inline=False)
            confirm_embed.set_footer(text="※注意: 実際のリマインド送信はDMかチャンネルへ通常メッセージで行います（将来の送信を ephemeral にすることはできません）")

            # If user chose public, we still send this confirmation ephemeral (so only they see it),
            # but they requested 'public' for the reminder itself: reminder will be sent in channel when triggers.
            await vis_interaction.followup.send(embed=confirm_embed, ephemeral=True)

    # send initial destination selection UI (always ephemeral so control buttons are private)
    view = ChooseDestinationView(remind_time_utc, repeat == "weekly", weekday_store if mode.value == "weekly" else None, message)
    await interaction.response.send_message("📍 送信先を選んでください（60秒）", view=view, ephemeral=True)


# === /remind_list コマンド（既存のまま） ===
@tree.command(name="remind_list", description="設定されているリマインダーを一覧表示します")
async def remind_list(interaction: discord.Interaction):
    reminders = load_reminders()
    user_id = interaction.user.id

    user_reminders = [
        r for r in reminders
        if r.get("user_id") == user_id and not r.get("deleted", False)
    ]

    if not user_reminders:
        await interaction.response.send_message(
            "📭 現在、設定されているリマインダーはありません。",
            ephemeral=True
        )
        return

    weekday_jp = {
        "mon": "月曜日", "tue": "火曜日", "wed": "水曜日",
        "thu": "木曜日", "fri": "金曜日", "sat": "土曜日", "sun": "日曜日"
    }

    await interaction.response.send_message(
        f"📋 あなたのリマインダーは **{len(user_reminders)} 件** あります。",
        ephemeral=True
    )

    for r in user_reminders:
        # r['time'] may be timestamp or earlier weekly stored time_str (if weekly stored differently)
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

        # 毎週リマインドなら曜日表示
        if r.get("repeat") == "weekly":
            w = r.get("weekday", "?")
            embed.add_field(name="📅 曜日", value=weekday_jp.get(w, "不明"), inline=False)

        view = ReminderDeleteView(r["uid"], user_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# === 起動（Render 互換）===
if __name__ == "__main__":
    import threading

    # --- Discord Bot をバックグラウンドで起動 ---
    def run_bot():
        client.run(TOKEN)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # --- Flask（Render が必須とする Web サーバー）をメインスレッドで起動 ---
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
