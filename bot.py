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

# =====================
# Supabase
# =====================
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

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
    res = supabase.table("reminders") \
        .select("*") \
        .eq("deleted", False) \
        .execute()

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
                    supabase.table("reminders") \
                        .update({"time": new_time}) \
                        .eq("uid", r["uid"]) \
                        .execute()
                else:
                    supabase.table("reminders") \
                        .update({"deleted": True}) \
                        .eq("uid", r["uid"]) \
                        .execute()

            except Exception as e:
                print("送信失敗:", e)

# =====================
# 削除ボタン＆ページ送りボタン用 View
# =====================
class ListView(discord.ui.View):
    def __init__(self, table: str, items: list, owner_id: int, start_index: int = 0):
        super().__init__(timeout=180)
        self.table = table
        self.items = items
        self.owner_id = owner_id
        self.start_index = start_index
        self.page_size = 10
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        # ◀
        if self.start_index > 0:
            self.add_item(discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, custom_id="prev"))
        # ❌ 削除
        self.add_item(discord.ui.Button(label="❌ 削除", style=discord.ButtonStyle.danger, custom_id="delete"))
        # ▶
        if self.start_index + self.page_size < len(self.items):
            self.add_item(discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, custom_id="next"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ あなた以外は操作できません", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="dummy", style=discord.ButtonStyle.secondary, disabled=True, custom_id="dummy")
    async def dummy(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    async def interaction_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data["custom_id"]
        if custom_id == "prev":
            self.start_index = max(self.start_index - self.page_size, 0)
            self.update_embed(interaction)
        elif custom_id == "next":
            self.start_index = min(self.start_index + self.page_size, len(self.items))
            self.update_embed(interaction)
        elif custom_id == "delete":
            # 削除は現在ページの全件を削除
            for item in self.items[self.start_index:self.start_index+self.page_size]:
                supabase.table(self.table).update({"deleted": True}).eq("uid", item["uid"]).execute()
            await interaction.response.edit_message(content="🗑 削除しました", embed=None, view=None)

    def create_embed(self):
        embed = discord.Embed(
            title=f"{'リマインド' if self.table=='reminders' else 'メモ'} 一覧",
            color=discord.Color.orange() if self.table=="reminders" else discord.Color.green()
        )
        page_items = self.items[self.start_index:self.start_index+self.page_size]
        for item in page_items:
            dt = datetime.datetime.fromtimestamp(item["time"], datetime.timezone.utc)
            where = "📩 DM" if item["send_to"] == "dm" else "📢 チャンネル"
            repeat = ""
            if self.table=="reminders" and item.get("repeat")=="weekly":
                repeat = f"\n🔁 毎週（{WEEKDAY_JP.get(item['weekday'],'')}）"
            embed.add_field(
                name=f"{where}｜{format_jst(dt)} {repeat}",
                value=item["message"][:100],
                inline=False
            )
        embed.set_footer(text=f"{self.start_index+1}-{min(self.start_index+self.page_size,len(self.items))} / {len(self.items)} 件")
        return embed

# =====================
# on_ready
# =====================
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await tree.sync()
    check_reminders.start()

# =====================
# /remind
# =====================
# （既存コードそのまま）...
# 省略（以前の /remind コマンドを使う）

# =====================
# /remind_list（ページング対応）
# =====================
@tree.command(name="remind_list", description="リマインド一覧を表示します（ページング対応）")
@app_commands.choices(scope=REMIND_LIST_SCOPE)
async def remind_list(interaction: discord.Interaction, scope: app_commands.Choice[str]):
    user_id = interaction.user.id
    channel_id = interaction.channel.id

    query = supabase.table("reminders").select("*").eq("deleted", False).order("time")
    if scope.value=="me": query=query.eq("user_id", user_id)
    else: query=query.eq("channel_id", channel_id)

    items = query.execute().data
    if not items:
        await interaction.response.send_message("📭 リマインドはありません", ephemeral=True)
        return

    view = ListView(table="reminders", items=items, owner_id=user_id)
    embed = view.create_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# =====================
# /memo_list（ページング対応）
# =====================
@tree.command(name="memo_list", description="メモ一覧を表示します（ページング対応）")
@app_commands.choices(scope=LIST_SCOPE)
async def memo_list(interaction: discord.Interaction, scope: app_commands.Choice[str]):
    user_id = interaction.user.id
    channel_id = interaction.channel.id

    query = supabase.table("memos").select("*").eq("deleted", False).order("time")
    if scope.value=="me": query=query.eq("user_id", user_id)
    else: query=query.eq("channel_id", channel_id)

    items = query.execute().data
    if not items:
        await interaction.response.send_message("📭 メモはありません", ephemeral=True)
        return

    view = ListView(table="memos", items=items, owner_id=user_id)
    embed = view.create_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# =====================
# 起動
# =====================
if __name__ == "__main__":
    import threading

    def run_bot():
        client.run(TOKEN)

    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
