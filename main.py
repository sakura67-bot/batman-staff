import os
import json
import logging
import random
import aiohttp
import discord
from discord.ext import commands

# =========================
# CONFIGURATION
# =========================

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

CONFIG_FILE = "config.json"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix=">",
    intents=intents,
    help_command=None
)

# =========================
# CUSTOM EMOJI CONFIGURATION
# =========================

EMOJIS = {
    "promo": "<a:giveaway:1559999666068791359>",
    "demo": "<:angry:1274605955035693057>",
    "shield": "<:z_mod_DiscordShield:1560000554909040694>",
    "gear": "<:Settings:1560007503239188594>",
    "ladder": "<:16400redswordrankicon:1560003340492865639>",
    "announcement": "<a:1369announce:1560000473623437376>",
    "audit": "<a:a7_book:1559999925822165105>",
    "success": "<a:MW_tick:1559999735329456220>",
    "warning": "<a:03_awarndns:1560000005492703303>",
    "error": "<a:error:1560000088732999810>",
    "user": "<:ntl_user:1556434847856328726>",
    "bullet": "<:MW_arrow_right:1560001178937598175>"
}

config = {
    "supervisor_roles": {},
    "promodemo_channels": {},
    "audit_channels": {},
    "rank_ladders": {}
}


def load_config():
    global config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)
        for key in config:
            if isinstance(saved.get(key), dict):
                config[key] = saved[key]
    except FileNotFoundError:
        save_config()
    except (json.JSONDecodeError, OSError):
        logging.exception("Could not load config.json")


def save_config():
    temporary_file = CONFIG_FILE + ".tmp"
    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=4)
        os.replace(temporary_file, CONFIG_FILE)
    except OSError:
        logging.exception("Could not save configuration")


def guild_setting(setting, guild_id):
    return config[setting].get(str(guild_id))


# =========================
# PERMISSION CHECKS
# =========================

def is_owner():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)


def is_supervisor():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID:
            return True
        role_id = guild_setting("supervisor_roles", ctx.guild.id)
        if not role_id:
            return False
        return any(role.id == int(role_id) for role in ctx.author.roles)
    return commands.check(predicate)


# =========================
# AUDIT LOGGING
# =========================

async def audit(guild, message):
    channel_id = guild_setting("audit_channels", guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return
    try:
        await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logging.exception("Failed to send audit log")


# =========================
# EMBED HELPERS
# =========================

async def send_promodemo_embed(guild, member, role, action, moderator):
    channel_id = guild_setting("promodemo_channels", guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return

    promoted = action == "promoted"
    embed = discord.Embed(
        title=f"{EMOJIS['promo']} Staff Promotion" if promoted else f"{EMOJIS['demo']} Staff Demotion",
        description=f"{member.mention} has been **promoted**!" if promoted else f"{member.mention} has been **demoted**.",
        color=discord.Color.green() if promoted else discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name=f"{EMOJIS['user']} Staff Member", value=f"{member.mention}\n`{member.id}`", inline=False)
    embed.add_field(name=f"{EMOJIS['ladder']} Role", value=role.mention, inline=True)
    embed.add_field(name=f"{EMOJIS['shield']} Action By", value=moderator.mention, inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{guild.name} • Staff Management System")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


# =========================
# RANK LADDER SETUP VIEW
# =========================

class RankSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        roles = [r for r in guild.roles if not r.is_default() and not r.managed][:25]
        
        options = []
        for role in roles:
            options.append(
                discord.SelectOption(
                    label=role.name[:100], 
                    value=str(role.id)
                )
            )

        if not options:
            options = [discord.SelectOption(label="No valid roles available", value="none")]

        super().__init__(
            placeholder="Select staff ranks in order (Lowest to Highest)...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values == ["none"]:
            await interaction.response.send_message(f"{EMOJIS['error']} No valid roles to configure.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        selected_role_ids = [int(val) for val in self.values]
        if "rank_ladders" not in config:
            config["rank_ladders"] = {}
        config["rank_ladders"][guild_id] = selected_role_ids
        save_config()

        roles_mention = ", ".join([f"<@&{r_id}>" for r_id in selected_role_ids])
        embed = discord.Embed(
            title=f"{EMOJIS['success']} Rank Ladder Configured Successfully!",
            description=f"**Ordered Ladder Hierarchy:**\n{roles_mention}",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)


class RankSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.add_item(RankSelect(guild))


async def change_staff_role(ctx, member, role, action):
    if member.id == ctx.author.id or member.id == ctx.guild.owner_id or role.is_default() or role.managed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Action unauthorized or invalid role.", color=discord.Color.orange()))
        return
    try:
        if action == "promote":
            await member.add_roles(role, reason=f"Staff promotion by {ctx.author}")
            result = "promoted"
        else:
            await member.remove_roles(role, reason=f"Staff demotion by {ctx.author}")
            result = "demoted"

        await send_promodemo_embed(ctx.guild, member, role, result, ctx.author)
        await audit(ctx.guild, f"Staff {result}: {member} | Role: {role.name} | By: {ctx.author}")
        emoji = EMOJIS['promo'] if result == "promoted" else EMOJIS['demo']
        await ctx.reply(embed=discord.Embed(description=f"{emoji} {member.mention} was **{result}**: {role.mention}", color=discord.Color.green() if result == "promoted" else discord.Color.red()))
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Missing permissions to modify roles.", color=discord.Color.red()))


# =========================
# MANAGEMENT & STAFF COMMANDS
# =========================

@bot.command()
@commands.guild_only()
@is_supervisor()
async def promo(ctx, staff: discord.Member, role: discord.Role = None):
    """Promotes a staff member."""
    if role is None:
        ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id))
        if not ladder:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No rank ladder configured.", color=discord.Color.orange()))
            return
        user_role_ids = [r.id for r in staff.roles]
        matched_index = next((idx for idx, r_id in enumerate(ladder) if r_id in user_role_ids), -1)
        next_index = matched_index + 1
        if next_index >= len(ladder):
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Already at the highest rank!", color=discord.Color.orange()))
            return
        role = ctx.guild.get_role(ladder[next_index])
    await change_staff_role(ctx, staff, role, "promote")


@bot.command()
@commands.guild_only()
@is_supervisor()
async def demo(ctx, staff: discord.Member, role: discord.Role = None):
    """Demotes a staff member."""
    if role is None:
        ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id))
        if not ladder:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No rank ladder configured.", color=discord.Color.orange()))
            return
        user_role_ids = [r.id for r in staff.roles]
        matched_index = next((idx for idx, r_id in enumerate(ladder) if r_id in user_role_ids), -1)
        prev_index = matched_index - 1
        if prev_index < 0:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Already at the lowest rank!", color=discord.Color.orange()))
            return
        role = ctx.guild.get_role(ladder[prev_index])
    await change_staff_role(ctx, staff, role, "demote")


@bot.command()
@commands.guild_only()
@is_owner()
async def super(ctx, member: discord.Member):
    """Assigns the supervisor role."""
    role_id = guild_setting("supervisor_roles", ctx.guild.id)
    if not role_id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Supervisor role not set up.", color=discord.Color.orange()))
        return
    role = ctx.guild.get_role(int(role_id))
    await member.add_roles(role, reason=f"Supervisor assigned by {ctx.author}")
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Assigned supervisor role to {member.mention}.", color=discord.Color.green()))


@bot.command()
@commands.guild_only()
@is_owner()
async def staff(ctx, member: discord.Member):
    """Assigns Junior Moderator and Staff Team roles automatically."""
    jr = discord.utils.find(lambda r: "junior moderator" in r.name.lower(), ctx.guild.roles)
    st = discord.utils.find(lambda r: "staff team" in r.name.lower(), ctx.guild.roles)
    roles = [r for r in [jr, st] if r]
    if not roles:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Roles not found.", color=discord.Color.red()))
        return
    await member.add_roles(*roles, reason=f"Staff roles assigned by {ctx.author}")
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Assigned staff roles to {member.mention}.", color=discord.Color.green()))


@bot.command()
@commands.guild_only()
@is_supervisor()
async def role(ctx, member: discord.Member, role: discord.Role):
    """Toggles a role on a member."""
    if role in member.roles:
        await member.remove_roles(role)
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['demo']} Removed {role.mention} from {member.mention}.", color=discord.Color.red()))
    else:
        await member.add_roles(role)
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['promo']} Added {role.mention} to {member.mention}.", color=discord.Color.green()))


@bot.command()
@commands.guild_only()
@is_owner()
async def steal(ctx, emoji: str, name: str = None):
    """Steals an emoji into the server."""
    import re
    match = re.match(r"<(a?):([a-zA-Z0-9_]+):([0-9]+)>", emoji)
    if not match:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Invalid custom emoji.", color=discord.Color.red()))
        return
    animated, e_name, e_id = match.groups()
    url = f"https://cdn.discordapp.com/emojis/{e_id}.{'gif' if animated=='a' else 'png'}?v=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            image_bytes = await resp.read()
    new_emoji = await ctx.guild.create_custom_emoji(name=name or e_name, image=image_bytes)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Stole and added {new_emoji}!", color=discord.Color.green()))


# =========================
# MODERATION COMMANDS
# =========================

@bot.command()
@commands.guild_only()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    """Kicks a member."""
    await member.kick(reason=reason)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Kicked {member.mention} | Reason: {reason}", color=discord.Color.orange()))


@bot.command()
@commands.guild_only()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    """Bans a member."""
    await member.ban(reason=reason)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Banned {member.mention} | Reason: {reason}", color=discord.Color.red()))


@bot.command()
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 5):
    """Purges messages."""
    await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(embed=discord.Embed(description=f"{EMOJIS['success']} Cleared {amount} messages.", color=discord.Color.green()))
    await msg.delete(delay=3)


@bot.command()
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, *, reason="No reason provided"):
    """Timeouts a member."""
    from datetime import timedelta
    await member.timeout(timedelta(minutes=10), reason=reason)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Timed out {member.mention} for 10 minutes.", color=discord.Color.orange()))


# =========================
# UTILITY & INFO COMMANDS
# =========================

@bot.command()
@commands.guild_only()
async def ping(ctx):
    """Shows bot latency."""
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Pong! Latency: `{round(bot.latency * 1000)}ms`", color=discord.Color.blue()))


@bot.command()
@commands.guild_only()
async def serverinfo(ctx):
    """Shows server info."""
    g = ctx.guild
    embed = discord.Embed(title=f"{EMOJIS['gear']} {g.name} Information", color=discord.Color.blurple())
    embed.add_field(name=f"{EMOJIS['user']} Owner", value=g.owner, inline=True)
    embed.add_field(name=f"{EMOJIS['ladder']} Members", value=g.member_count, inline=True)
    embed.add_field(name=f"{EMOJIS['success']} Created On", value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.reply(embed=embed)


@bot.command()
@commands.guild_only()
async def userinfo(ctx, member: discord.Member = None):
    """Shows user info."""
    m = member or ctx.author
    embed = discord.Embed(title=f"{EMOJIS['user']} {m} Info", color=m.color)
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="ID", value=m.id, inline=True)
    embed.add_field(name="Joined", value=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "N/A", inline=True)
    await ctx.reply(embed=embed)


@bot.command()
@commands.guild_only()
async def avatar(ctx, member: discord.Member = None):
    """Shows user avatar."""
    m = member or ctx.author
    embed = discord.Embed(title=f"{EMOJIS['user']} Avatar for {m}", color=discord.Color.blurple())
    embed.set_image(url=m.display_avatar.url)
    await ctx.reply(embed=embed)


# =========================
# FUN & MISC COMMANDS
# =========================

@bot.command()
async def coinflip(ctx):
    """Flips a coin."""
    result = random.choice(["Heads", "Tails"])
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} The coin landed on: **{result}**", color=discord.Color.gold()))


@bot.command()
async def roll(ctx):
    """Rolls a dice (1-6)."""
    result = random.randint(1, 6)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} You rolled a **{result}**!", color=discord.Color.gold()))


# =========================
# SETUP COMMANDS
# =========================

@bot.command()
@commands.guild_only()
@is_owner()
async def setranks(ctx):
    view = RankSelectView(ctx.guild)
    embed = discord.Embed(title=f"{EMOJIS['ladder']} Rank Ladder Setup", description="Select roles in order from lowest to highest.", color=discord.Color.blurple())
    await ctx.reply(embed=embed, view=view)


@bot.command()
@commands.guild_only()
@is_owner()
async def setstaffsupervisor(ctx, role: discord.Role):
    config["supervisor_roles"][str(ctx.guild.id)] = role.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Supervisor role set to {role.mention}.", color=discord.Color.green()))


@bot.command()
@commands.guild_only()
@is_owner()
async def setpromodemo(ctx, channel: discord.TextChannel):
    config["promodemo_channels"][str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Promo channel set to {channel.mention}.", color=discord.Color.green()))


@bot.command()
@commands.guild_only()
@is_owner()
async def setaudit(ctx, channel: discord.TextChannel):
    config["audit_channels"][str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Audit channel set to {channel.mention}.", color=discord.Color.green()))


@bot.command()
@commands.guild_only()
@is_owner()
async def staffsettings(ctx):
    embed = discord.Embed(title=f"{EMOJIS['gear']} Bot Settings Configured", color=discord.Color.blurple())
    await ctx.reply(embed=embed)


# =========================
# HELP COMMAND
# =========================

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(title=f"{EMOJIS['gear']} All-In-One Bot Command Center", description="Here are all available command modules:", color=discord.Color.blurple())
    embed.add_field(name=f"{EMOJIS['shield']} Staff & Management", value="`>promo`, `>demo`, `>super`, `>staff`, `>role`, `>steal`", inline=False)
    embed.add_field(name=f"{EMOJIS['warning']} Moderation", value="`>kick`, `>ban`, `>clear`, `>mute`", inline=False)
    embed.add_field(name=f"{EMOJIS['user']} Utility & Info", value="`>ping`, `>serverinfo`, `>userinfo`, `>avatar`", inline=False)
    embed.add_field(name=f"{EMOJIS['success']} Fun & Games", value="`>coinflip`, `>roll`", inline=False)
    embed.add_field(name=f"{EMOJIS['gear']} Setup", value="`>setranks`, `>setstaffsupervisor`, `>setpromodemo`, `>setaudit`, `>staffsettings`", inline=False)
    await ctx.reply(embed=embed, mention_author=False)


# =========================
# EVENTS
# =========================

@bot.event
async def on_ready():
    logging.info("Logged in as %s", bot.user)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return
    
    logging.exception("Command error:", exc_info=error)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Error: `{str(error)}`", color=discord.Color.red()))


load_config()
bot.run(TOKEN)
