import os
import datetime
import uuid
import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask
from supabase import create_client

# =====================
# Flask（Render用）
# =====================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# =====================
# Discord Bot
# =====================
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN が設定されていません")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =====================
# Supabase
# =====================
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =====================
# JST 表示
# =====================
def format_jst(dt: datetime.datetime):
    jst = datetime.timezone(datetime.timedelta(hours=9))
    return dt.astimezone(jst).strftime("%Y年%m月%d日 %H:%M")

# =====================
# 曜日表示用
# =====================
WEEKDAY_JP = {
    "mon": "月曜日",
    "tue": "火曜日",
    "wed": "水曜日",
    "thu": "木曜日",
    "fri": "金曜日",
    "sat": "土曜日",
    "sun": "日曜日",
}

# =====================
# 日時パース（JST入力）
# =====================
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

# =====================
# リマインダー監視
# =====================
@tasks.loop(seconds=30)
async def check_reminders():
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    res = supabase.table("reminders").select("*").eq("deleted", False).execute()
    for r in res.data or []:
        if r["time"] <= now_ts:
            try:
                dt = datetime.datetime.fromtimestamp(r["time"], datetime.timezone.utc)
                content = f"⏰ {format_jst(dt)}\n💬 {r['message']}"
                if r.get("role_id"):
                    content = f"<@&{r['role_id']}> " + content
                if r["send_to"] == "dm":
                    user = await client.fetch_user(r["user_id"])
                    await user.send(content)
                else:
                    ch = client.get_channel(r["channel_id"])
                    if ch:
                        await ch.send(content)

                if r.get("repeat") == "weekly":
                    new_time = (dt + datetime.timedelta(days=7)).timestamp()
                    supabase.table("reminders").update({"time": new_time}).eq("uid", r["uid"]).execute()
                else:
                    supabase.table("reminders").update({"deleted": True}).eq("uid", r["uid"]).execute()
            except Exception as e:
                print("送信失敗:", e)

# =====================
# on_ready
# =====================
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    check_reminders.start()

# =====================
# 曜日 Choice
# =====================
WEEKDAYS = [
    app_commands.Choice(name="月曜日", value="mon"),
    app_commands.Choice(name="火曜日", value="tue"),
    app_commands.Choice(name="水曜日", value="wed"),
    app_commands.Choice(name="木曜日", value="thu"),
    app_commands.Choice(name="金曜日", value="fri"),
    app_commands.Choice(name="土曜日", value="sat"),
    app_commands.Choice(name="日曜日", value="sun"),
]

# =====================
# /remind
# =====================
REMIND_LIST_SCOPE = [
    app_commands.Choice(name="自分のリマインド", value="me"),
    app_commands.Choice(name="このチャンネルのリマインド", value="channel"),
]

@tree.command(name="remind", description="リマインダーを設定します")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="日時指定", value="at"),
        app_commands.Choice(name="毎週", value="weekly"),
    ],
    weekday=WEEKDAYS
)
async def remind(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    time: str,
    message: str,
    channel: discord.TextChannel | None = None,
    dm: bool | None = False,
    role: discord.Role | None = None,
    weekday: app_commands.Choice[str] | None = None,
):
    if mode.value == "weekly" and not weekday:
        await interaction.response.send_message("❌ 毎週モードの場合は曜日を選択してください", ephemeral=True)
        return
    try:
        if mode.value == "at":
            dt = parse_datetime_input(time)
        else:
            hhmm = datetime.datetime.strptime(time, "%H:%M")
            now = datetime.datetime.now()
            target = now.replace(hour=hhmm.hour, minute=hhmm.minute, second=0, microsecond=0)
            weekday_map = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
            wd = weekday_map[weekday.value]
            days_ahead = (wd - target.weekday()) % 7
            if days_ahead == 0 and target <= now:
                days_ahead = 7
            dt = target + datetime.timedelta(days=days_ahead)
        remind_ts = (dt - datetime.timedelta(hours=9)).timestamp()
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    send_to = "dm" if dm else "channel"
    target_channel = channel or interaction.channel
    entry = {
        "uid": str(uuid.uuid4()),
        "user_id": interaction.user.id,
        "channel_id": None if dm else target_channel.id,
        "role_id": role.id if role else None,
        "send_to": send_to,
        "message": message,
        "time": remind_ts,
        "repeat": "weekly" if mode.value == "weekly" else None,
        "weekday": weekday.value if weekday else None,
        "deleted": False
    }
    supabase.table("reminders").insert(entry).execute()
    dt_display = datetime.datetime.fromtimestamp(remind_ts, datetime.timezone.utc)
    content = f"🔔 リマインダー設定完了\n⏰ {format_jst(dt_display)}"
    if mode.value == "weekly":
        content += f"\n🔁 毎週（{WEEKDAY_JP[weekday.value]}）"
    content += f"\n💬 {message}"
    if role:
        content = f"<@&{role.id}> " + content
    try:
        if send_to == "dm":
            await interaction.user.send(content)
        else:
            await target_channel.send(content)
    except Exception as e:
        print("設定完了メッセージ送信失敗:", e)
    await interaction.response.send_message("✅ リマインダーを設定しました！", ephemeral=True)

# =====================
# /memo
# =====================
LIST_SCOPE = [
    app_commands.Choice(name="自分のメモ", value="me"),
    app_commands.Choice(name="このチャンネルのメモ", value="channel"),
]

@tree.command(name="memo", description="Embed形式のメモを保存＆送信します")
async def memo(
    interaction: discord.Interaction,
    time: str,
    message: str,
    channel: discord.TextChannel | None = None,
    dm: bool | None = False,
):
    try:
        dt = parse_datetime_input(time)
        memo_ts = (dt - datetime.timedelta(hours=9)).timestamp()
    except Exception as e:
        await interaction.response.send_message(f"❌ 時刻の指定が不正です\n{e}", ephemeral=True)
        return
    send_to = "dm" if dm else "channel"
    target_channel = channel or interaction.channel
    memo_uid = str(uuid.uuid4())
    try:
        supabase.table("memos").insert({
            "uid": memo_uid,
            "user_id": interaction.user.id,
            "channel_id": None if dm else target_channel.id,
            "send_to": send_to,
            "message": message,
            "time": memo_ts,
            "deleted": False
        }).execute()
    except Exception as e:
        await interaction.response.send_message(f"❌ メモの保存に失敗しました\n{e}", ephemeral=True)
        return
    dt_utc = datetime.datetime.fromtimestamp(memo_ts, datetime.timezone.utc)
    embed = discord.Embed(title="📝 メモ", description=message, color=discord.Color.blurple(), timestamp=dt_utc)
    embed.add_field(name="🕒 時刻", value=format_jst(dt_utc), inline=False)
    embed.set_footer(text=f"by {interaction.user.display_name}")
    try:
        if send_to == "dm":
            await interaction.user.send(embed=embed)
        else:
            await target_channel.send(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ メモ送信に失敗しました\n{e}", ephemeral=True)
        return
    await interaction.response.send_message("✅ メモを保存しました", ephemeral=True)

# =======================
# 1ページ5件表示＋削除ビュー
# =======================
ITEMS_PER_PAGE = 5

class Paginator(discord.ui.View):
    def __init__(self, items, user_id, item_type="memo"):
        super().__init__(timeout=None)
        self.items = items
        self.user_id = user_id
        self.item_type = item_type
        self.page = 0
        self.delete_select.options = []  # 初期化

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 権限がありません", ephemeral=True)
            return False
        return True

    def get_page_items(self):
        start = self.page * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        return self.items[start:end]

    def total_pages(self):
        return (len(self.items) - 1) // ITEMS_PER_PAGE + 1

    def current_embed(self):
        page_items = self.get_page_items()
        if not page_items:
            return None
        embed = discord.Embed(
            title=f"{'📝 メモ' if self.item_type=='memo' else '⏰ リマインド'}一覧 ({self.page+1}/{self.total_pages()})",
            color=discord.Color.green() if self.item_type=="memo" else discord.Color.orange()
        )
        for i, item in enumerate(page_items):
            dt_utc = datetime.datetime.fromtimestamp(item["time"], datetime.timezone.utc)
            where = "📩 DM" if item["send_to"]=="dm" else "📢 チャンネル"
            repeat = ""
            if self.item_type=="reminder" and item.get("repeat")=="weekly":
                repeat = f"（毎週 {WEEKDAY_JP.get(item.get('weekday',''), '')}）"
            embed.add_field(name=f"{i+1}. {where}｜{format_jst(dt_utc)} {repeat}", value=item["message"], inline=False)
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await self.update_select_options()
            await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages() - 1:
            self.page += 1
            await self.update_select_options()
            await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.select(placeholder="削除するアイテムを選択", min_values=1, max_values=1, options=[])
    async def delete_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            idx = int(select.values[0])-1
        except:
            await interaction.response.send_message("❌ 選択が無効です", ephemeral=True)
            return
        page_items = self.get_page_items()
        if idx >= len(page_items):
            await interaction.response.send_message("❌ 選択が無効です", ephemeral=True)
            return
        item = page_items[idx]
        table_name = "memos" if self.item_type=="memo" else "reminders"
        supabase.table(table_name).update({"deleted": True}).eq("uid", item["uid"]).execute()
        self.items.remove(item)
        if self.page >= self.total_pages():
            self.page = max(0, self.total_pages()-1)
        if self.items:
            await self.update_select_options()
            await interaction.response.edit_message(embed=self.current_embed(), view=self)
        else:
            await interaction.response.edit_message(content="📭 すべて削除されました", embed=None, view=None)

    async def update_select_options(self):
        self.delete_select.options = []
        for i, item in enumerate(self.get_page_items()):
            self.delete_select.options.append(discord.SelectOption(label=f"{i+1}. {item['message'][:50]}", value=str(i+1)))

# =======================
# memo_list
# =======================
@tree.command(name="memo_list", description="保存されたメモ一覧を表示します")
@app_commands.choices(scope=LIST_SCOPE)
async def memo_list(interaction: discord.Interaction, scope: app_commands.Choice[str]):
    user_id = interaction.user.id
    channel_id = interaction.channel.id
    if scope.value=="me":
        res = supabase.table("memos").select("*").eq("user_id", user_id).eq("deleted", False).order("time").execute()
    else:
        res = supabase.table("memos").select("*").eq("channel_id", channel_id).eq("deleted", False).order("time").execute()
    memos = res.data
    if not memos:
        await interaction.response.send_message("📭 メモはありません", ephemeral=True)
        return
    view = Paginator(memos, user_id, item_type="memo")
    await view.update_select_options()
    await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)

# =======================
# remind_list
# =======================
@tree.command(name="remind_list", description="保存されたリマインド一覧を表示します")
@app_commands.choices(scope=REMIND_LIST_SCOPE)
async def remind_list(interaction: discord.Interaction, scope: app_commands.Choice[str]):
    user_id = interaction.user.id
    channel_id = interaction.channel.id
    if scope.value=="me":
        res = supabase.table("reminders").select("*").eq("user_id", user_id).eq("deleted", False).order("time").execute()
    else:
        res = supabase.table("reminders").select("*").eq("channel_id", channel_id).eq("deleted", False).order("time").execute()
    reminders = res.data
    if not reminders:
        await interaction.response.send_message("📭 リマインドはありません", ephemeral=True)
        return
    view = Paginator(reminders, user_id, item_type="reminder")
    await view.update_select_options()
    await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)

# =====================
# 起動
# =====================
if __name__ == "__main__":
    import threading
    def run_bot():
        client.run(TOKEN)
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
