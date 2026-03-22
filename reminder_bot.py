import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime
import pytz
import json
import os

# ── 설정 ──────────────────────────────────────────────
TOKEN = os.environ.get("TOKEN", "")
DATA_FILE = "bot_data.json"
KST = pytz.timezone("Asia/Seoul")

# ── 데이터 로드/저장 ──────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"alert_channel": None, "command_channel": None, "reminders": []}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()

# ── 봇 설정 ───────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── 유틸 ──────────────────────────────────────────────
def is_command_channel(interaction: discord.Interaction) -> bool:
    cmd_ch = data.get("command_channel")
    if cmd_ch is None:
        return True
    return interaction.channel_id == cmd_ch

def parse_time(time_str: str):
    time_str = time_str.strip()
    if " " in time_str:
        parts = time_str.split(" ", 1)
        date_part, time_part = parts[0], parts[1]
        try:
            datetime.strptime(time_part, "%H:%M")
            year = datetime.now(KST).year
            datetime.strptime(f"{year}/{date_part}", "%Y/%m/%d")
            return time_part, date_part, True
        except ValueError:
            return None, None, False
    else:
        try:
            datetime.strptime(time_str, "%H:%M")
            return time_str, None, True
        except ValueError:
            return None, None, False

def resolve_mention(guild: discord.Guild, mention_str: str) -> str:
    m = mention_str.strip()
    if m in ("everyone", "here"):
        return f"@{m}"
    role = discord.utils.get(guild.roles, name=m)
    if role:
        return role.mention
    member = discord.utils.get(guild.members, display_name=m) or \
             discord.utils.get(guild.members, name=m)
    if member:
        return member.mention
    try:
        mid = int(m)
        r = guild.get_role(mid)
        if r:
            return r.mention
        mem = guild.get_member(mid)
        if mem:
            return mem.mention
        return f"<@{mid}>"
    except ValueError:
        pass
    return m

# ── 봇 준비 ───────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    check_reminders.start()
    print(f"✅ 봇 온라인: {bot.user}")

# ══════════════════════════════════════════════════════
# 멘션 대상 드롭다운
# ══════════════════════════════════════════════════════
class MentionTargetSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, reminder_data: dict):
        self.reminder_data = reminder_data
        options = [
            discord.SelectOption(label="@everyone", value="everyone", emoji="📢"),
            discord.SelectOption(label="@here", value="here", emoji="🔔"),
        ]
        for role in guild.roles:
            if role.name != "@everyone" and len(options) < 23:
                options.append(discord.SelectOption(label=f"역할: {role.name}", value=f"role:{role.id}", emoji="🏷️"))
        count = 0
        for member in guild.members:
            if not member.bot and len(options) < 25 and count < 5:
                options.append(discord.SelectOption(label=f"유저: {member.display_name}", value=f"user:{member.id}", emoji="👤"))
                count += 1
        super().__init__(placeholder="멘션 대상을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val in ("everyone", "here"):
            self.reminder_data["mention"] = val
        elif val.startswith("role:"):
            role = interaction.guild.get_role(int(val.split(":")[1]))
            self.reminder_data["mention"] = role.name if role else val
        elif val.startswith("user:"):
            member = interaction.guild.get_member(int(val.split(":")[1]))
            self.reminder_data["mention"] = member.display_name if member else val

        data["reminders"].append(self.reminder_data)
        save_data()
        idx = len(data["reminders"]) - 1

        time_display = f"{self.reminder_data['date']} {self.reminder_data['time']}" if self.reminder_data.get("date") else self.reminder_data["time"]
        repeat_str = f"매 {self.reminder_data['interval']}분마다" if self.reminder_data["repeat"] else "1회"

        await interaction.response.edit_message(
            content=(
                f"✅ **알림 추가 완료!**\n"
                f"🔢 알림 번호: `{idx}`\n"
                f"📌 제목: **{self.reminder_data['title']}**\n"
                f"⏰ 시간: `{time_display}`\n"
                f"🔁 반복: {repeat_str}\n"
                f"📣 방식: 고정 멘션\n"
                f"👥 대상: `{self.reminder_data['mention']}`"
            ),
            view=None
        )

# ══════════════════════════════════════════════════════
# 멘션 모드 선택 버튼
# ══════════════════════════════════════════════════════
class MentionModeView(discord.ui.View):
    def __init__(self, guild: discord.Guild, reminder_data: dict):
        super().__init__(timeout=60)
        self.guild = guild
        self.reminder_data = reminder_data

    @discord.ui.button(label="A - 고정 멘션", style=discord.ButtonStyle.primary, emoji="📢")
    async def mode_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.reminder_data["mode"] = "A"
        view = discord.ui.View(timeout=60)
        view.add_item(MentionTargetSelect(self.guild, self.reminder_data))
        await interaction.response.edit_message(content="👥 **멘션 대상을 선택하세요**", view=view)

    @discord.ui.button(label="B - 수신 선택 (이모지)", style=discord.ButtonStyle.secondary, emoji="✅")
    async def mode_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.reminder_data["mode"] = "B"
        self.reminder_data["mention"] = ""
        data["reminders"].append(self.reminder_data)
        save_data()
        idx = len(data["reminders"]) - 1

        time_display = f"{self.reminder_data['date']} {self.reminder_data['time']}" if self.reminder_data.get("date") else self.reminder_data["time"]
        repeat_str = f"매 {self.reminder_data['interval']}분마다" if self.reminder_data["repeat"] else "1회"

        await interaction.response.edit_message(
            content=(
                f"✅ **알림 추가 완료!**\n"
                f"🔢 알림 번호: `{idx}`\n"
                f"📌 제목: **{self.reminder_data['title']}**\n"
                f"⏰ 시간: `{time_display}`\n"
                f"🔁 반복: {repeat_str}\n"
                f"📣 방식: 이모지 수신 선택 (✅❌)"
            ),
            view=None
        )

# ══════════════════════════════════════════════════════
# 알림추가 Modal
# ══════════════════════════════════════════════════════
class AddReminderModal(discord.ui.Modal, title="🔔 알림 추가"):
    제목 = discord.ui.TextInput(label="제목", placeholder="예: 저녁 먹을 시간!", max_length=50)
    시간 = discord.ui.TextInput(label="시간", placeholder="매일: 18:00  /  특정 날짜: 03/25 18:00", max_length=20)
    반복주기 = discord.ui.TextInput(
        label="반복 주기 (분) — 반복 없으면 0",
        placeholder="예: 30 (30분마다)  /  0 (1회)",
        max_length=5,
        default="0"
    )

    async def on_submit(self, interaction: discord.Interaction):
        parsed_time, parsed_date, valid = parse_time(str(self.시간))
        if not valid:
            await interaction.response.send_message(
                "❌ 시간 형식이 잘못됐어요.\n• 매일: `18:00`\n• 특정 날짜: `03/25 18:00`",
                ephemeral=True
            )
            return

        try:
            interval = int(str(self.반복주기).strip())
        except ValueError:
            interval = 0

        is_repeat = (interval > 0) and (parsed_date is None)

        reminder_data = {
            "id": int(datetime.now(KST).timestamp()),
            "title": str(self.제목),
            "time": parsed_time,
            "date": parsed_date,
            "repeat": is_repeat,
            "interval": interval,
            "mode": "A",
            "mention": "everyone",
            "opted_out": [],
            "last_sent": ""
        }

        view = MentionModeView(interaction.guild, reminder_data)
        await interaction.response.send_message(
            "📣 **멘션 방식을 선택하세요**\n"
            "• **A - 고정 멘션**: 항상 지정한 대상에게 알림\n"
            "• **B - 수신 선택**: 알림에 ✅❌ 달림, ❌ 누르면 그 회차 제외",
            view=view,
            ephemeral=True
        )

@tree.command(name="알림추가", description="팝업창에서 알림을 설정합니다.")
async def add_reminder(interaction: discord.Interaction):
    if not is_command_channel(interaction):
        await interaction.response.send_message("❌ 이 채널에서는 명령어를 사용할 수 없어요.", ephemeral=True)
        return
    await interaction.response.send_modal(AddReminderModal())

# ══════════════════════════════════════════════════════
# 알림설정 Modal (수정)
# ══════════════════════════════════════════════════════
class EditReminderModal(discord.ui.Modal, title="✏️ 알림 수정"):
    def __init__(self, index: int, r: dict):
        super().__init__()
        self.index = index
        time_display = f"{r['date']} {r['time']}" if r.get("date") else r["time"]
        self.제목.default = r["title"]
        self.시간.default = time_display
        self.반복주기.default = str(r.get("interval", 0))

    제목 = discord.ui.TextInput(label="제목", max_length=50)
    시간 = discord.ui.TextInput(label="시간 (18:00 또는 03/25 18:00)", max_length=20)
    반복주기 = discord.ui.TextInput(label="반복 주기 (분, 0=반복없음)", max_length=5)

    async def on_submit(self, interaction: discord.Interaction):
        parsed_time, parsed_date, valid = parse_time(str(self.시간))
        if not valid:
            await interaction.response.send_message("❌ 시간 형식이 잘못됐어요.", ephemeral=True)
            return
        try:
            interval = int(str(self.반복주기).strip())
        except ValueError:
            interval = 0

        r = data["reminders"][self.index]
        r["title"] = str(self.제목)
        r["time"] = parsed_time
        r["date"] = parsed_date
        r["repeat"] = (interval > 0) and (parsed_date is None)
        r["interval"] = interval
        save_data()

        time_display = f"{parsed_date} {parsed_time}" if parsed_date else parsed_time
        await interaction.response.send_message(
            f"✅ `{self.index}번` 알림 수정 완료!\n"
            f"📌 제목: **{r['title']}**\n"
            f"⏰ 시간: `{time_display}`",
            ephemeral=True
        )

@tree.command(name="알림설정", description="기존 알림을 수정합니다.")
@app_commands.describe(번호="수정할 알림 번호 (/알림목록 으로 확인)")
async def edit_reminder(interaction: discord.Interaction, 번호: int):
    if not is_command_channel(interaction):
        await interaction.response.send_message("❌ 이 채널에서는 명령어를 사용할 수 없어요.", ephemeral=True)
        return
    reminders = data["reminders"]
    if 번호 < 0 or 번호 >= len(reminders):
        await interaction.response.send_message("❌ 잘못된 번호예요. `/알림목록` 으로 확인해 주세요.", ephemeral=True)
        return
    await interaction.response.send_modal(EditReminderModal(번호, reminders[번호]))

# ══════════════════════════════════════════════════════
# /알림삭제
# ══════════════════════════════════════════════════════
@tree.command(name="알림삭제", description="알림을 삭제합니다.")
@app_commands.describe(번호="삭제할 알림 번호 (/알림목록 으로 확인)")
async def delete_reminder(interaction: discord.Interaction, 번호: int):
    if not is_command_channel(interaction):
        await interaction.response.send_message("❌ 이 채널에서는 명령어를 사용할 수 없어요.", ephemeral=True)
        return
    reminders = data["reminders"]
    if 번호 < 0 or 번호 >= len(reminders):
        await interaction.response.send_message("❌ 잘못된 번호예요.", ephemeral=True)
        return
    removed = reminders.pop(번호)
    save_data()
    await interaction.response.send_message(f"🗑️ **{removed['title']}** 알림이 삭제됐어요!")

# ══════════════════════════════════════════════════════
# /알림목록
# ══════════════════════════════════════════════════════
@tree.command(name="알림목록", description="등록된 알림 목록을 봅니다.")
async def list_reminders(interaction: discord.Interaction):
    reminders = data["reminders"]
    if not reminders:
        await interaction.response.send_message("등록된 알림이 없어요.", ephemeral=True)
        return
    msg = "📋 **알림 목록**\n```\n"
    for i, r in enumerate(reminders):
        time_display = f"{r['date']} {r['time']}" if r.get("date") else r["time"]
        repeat_str = f"매 {r['interval']}분" if r.get("repeat") and r.get("interval", 0) > 0 else "1회"
        mode_str = f"고정(@{r['mention']})" if r["mode"] == "A" else "선택수신"
        msg += f"[{i}] {r['title']} | {time_display} | {repeat_str} | {mode_str}\n"
    msg += "```"
    await interaction.response.send_message(msg)

# ══════════════════════════════════════════════════════
# /알림방설정, /알림방변경, /알림명령방지정
# ══════════════════════════════════════════════════════
@tree.command(name="알림방설정", description="알림을 보낼 채널을 설정합니다.")
@app_commands.describe(채널="알림을 보낼 채널")
async def set_alert_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    if not is_command_channel(interaction):
        await interaction.response.send_message("❌ 이 채널에서는 명령어를 사용할 수 없어요.", ephemeral=True)
        return
    data["alert_channel"] = 채널.id
    save_data()
    await interaction.response.send_message(f"✅ 알림 채널이 {채널.mention} 으로 설정됐어요!")

@tree.command(name="알림방변경", description="알림을 보낼 채널을 변경합니다.")
@app_commands.describe(채널="변경할 채널")
async def change_alert_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    if not is_command_channel(interaction):
        await interaction.response.send_message("❌ 이 채널에서는 명령어를 사용할 수 없어요.", ephemeral=True)
        return
    data["alert_channel"] = 채널.id
    save_data()
    await interaction.response.send_message(f"✅ 알림 채널이 {채널.mention} 으로 변경됐어요!")

@tree.command(name="알림명령방지정", description="명령어를 입력할 수 있는 전용 채널을 지정합니다.")
@app_commands.describe(채널="명령어 전용 채널")
async def set_command_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    data["command_channel"] = 채널.id
    save_data()
    await interaction.response.send_message(f"✅ 명령어 전용 채널이 {채널.mention} 으로 지정됐어요!")

# ══════════════════════════════════════════════════════
# 알림 전송
# ══════════════════════════════════════════════════════
async def send_reminder(reminder):
    channel_id = data.get("alert_channel")
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    mention_str = ""
    if reminder["mode"] == "A":
        mention_str = resolve_mention(channel.guild, reminder["mention"])

    content = f"{mention_str}\n🔔 **{reminder['title']}**"

    if reminder["mode"] == "B":
        reminder["opted_out"] = []
        msg = await channel.send(content + "\n✅ 받기   ❌ 안 받기")
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        reminder["last_msg_id"] = msg.id
    else:
        await channel.send(content)

    save_data()

# ══════════════════════════════════════════════════════
# 이모지 반응 감지 (모드 B)
# ══════════════════════════════════════════════════════
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    for r in data["reminders"]:
        if r.get("mode") == "B" and r.get("last_msg_id") == payload.message_id:
            if str(payload.emoji) == "❌":
                if payload.user_id not in r["opted_out"]:
                    r["opted_out"].append(payload.user_id)
                    save_data()
            elif str(payload.emoji) == "✅":
                if payload.user_id in r["opted_out"]:
                    r["opted_out"].remove(payload.user_id)
                    save_data()
            break

# ══════════════════════════════════════════════════════
# 시간 체크 루프
# ══════════════════════════════════════════════════════
@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.now(KST)
    now_hm = now.strftime("%H:%M")
    now_full = now.strftime("%Y-%m-%d %H:%M")
    now_md = now.strftime("%m/%d")

    for r in data["reminders"]:
        try:
            # 날짜 지정 1회성
            if r.get("date"):
                if r["date"] == now_md and r["time"] == now_hm and r.get("last_sent") != now_full:
                    r["last_sent"] = now_full
                    await send_reminder(r)

            # 반복 알림 (분 단위)
            elif r.get("repeat") and r.get("interval", 0) > 0:
                last = r.get("last_sent", "")
                if not last:
                    if r["time"] == now_hm:
                        r["last_sent"] = now_full
                        await send_reminder(r)
                else:
                    last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
                    diff = (now - last_dt).total_seconds() / 60
                    if diff >= r["interval"]:
                        r["last_sent"] = now_full
                        await send_reminder(r)

            # 매일 1회
            else:
                if r["time"] == now_hm and r.get("last_sent", "")[:10] != now.strftime("%Y-%m-%d"):
                    r["last_sent"] = now_full
                    await send_reminder(r)

        except Exception as e:
            print(f"알림 처리 오류: {e}")

    save_data()

# ══════════════════════════════════════════════════════
bot.run(TOKEN)
