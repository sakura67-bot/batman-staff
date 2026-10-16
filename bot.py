"""Batcat staff management bot. Prefix: >"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import discord
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("batcat-staff-bot")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
EMOJI_DIR = Path("emoji_assets")
PREFIX = ">"
EMOJI_NAMES = ("batcat_staff", "batcat_settings", "batcat_rankup", "batcat_rankdown", "batcat_audit")


def ensure_emoji_files(directory: Path) -> None:
    """Create the original PNG emoji files on first start."""
    directory.mkdir(parents=True, exist_ok=True)
    specs = {
        "batcat_staff": ("B", (104, 67, 183), (246, 211, 79)),
        "batcat_settings": ("⚙", (66, 79, 111), (224, 231, 255)),
        "batcat_rankup": ("↑", (37, 145, 106), (224, 255, 237)),
        "batcat_rankdown": ("↓", (186, 74, 88), (255, 230, 230)),
        "batcat_audit": ("✓", (41, 113, 168), (227, 245, 255)),
    }
    font_path = "C:/Windows/Fonts/seguisym.ttf" if Path("C:/Windows/Fonts/seguisym.ttf").exists() else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        font = ImageFont.truetype(font_path, 46)
    except OSError:
        font = ImageFont.load_default()
    for name, (glyph, bg, fg) in specs.items():
        path = directory / f"{name}.png"
        if path.exists():
            continue
        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 124, 124), fill=bg, outline=(255, 255, 255, 245), width=5)
        bounds = draw.textbbox((0, 0), glyph, font=font, stroke_width=1)
        x = (128 - (bounds[2] - bounds[0])) / 2 - bounds[0]
        y = (128 - (bounds[3] - bounds[1])) / 2 - bounds[1] - 3
        draw.text((x, y), glyph, font=font, fill=fg, stroke_width=1, stroke_fill=fg)
        image.save(path, format="PNG", optimize=True)


class Store:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / "guild_settings.json"
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def guild(self, guild_id: int) -> dict:
        return self.data.setdefault(str(guild_id), {"ranks": [], "supervisor_role": None,
            "promo_channel": None, "audit_channel": None, "emojis": {}})

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)


store = Store()
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


def custom(guild: discord.Guild, key: str, fallback: str) -> str:
    emoji_id = store.guild(guild.id).get("emojis", {}).get(key)
    return f"<:{key}:{emoji_id}>" if emoji_id else fallback


async def audit(guild: discord.Guild, text: str) -> None:
    channel_id = store.guild(guild.id).get("audit_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        await channel.send(text)


async def is_supervisor(ctx: commands.Context) -> bool:
    if not ctx.guild or not isinstance(ctx.author, discord.Member):
        return False
    if ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild:
        return True
    role_id = store.guild(ctx.guild.id).get("supervisor_role")
    return bool(role_id and any(role.id == role_id for role in ctx.author.roles))


async def require_supervisor(ctx: commands.Context) -> bool:
    if await is_supervisor(ctx):
        return True
    await ctx.reply("Only the configured staff supervisor (or a server administrator) can use this command.", mention_author=False)
    return False


@bot.event
async def on_ready() -> None:
    ensure_emoji_files(EMOJI_DIR)
    for guild in bot.guilds:
        cfg = store.guild(guild.id)
        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            log.warning("Missing Manage Expressions permission in %s; custom emojis were not installed", guild.name)
            continue
        for name in EMOJI_NAMES:
            if name in cfg["emojis"] and discord.utils.get(guild.emojis, id=cfg["emojis"][name]):
                continue
            existing = discord.utils.get(guild.emojis, name=name)
            if existing:
                cfg["emojis"][name] = existing.id
                continue
            try:
                created = await guild.create_custom_emoji(name=name, image=(EMOJI_DIR / f"{name}.png").read_bytes(), reason="Install Batcat staff bot command emojis")
                cfg["emojis"][name] = created.id
            except discord.HTTPException:
                log.exception("Could not install %s in %s", name, guild.name)
        store.save()
    log.info("Ready as %s", bot.user)


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    await on_ready()


@bot.command(name="help")
async def help_command(ctx: commands.Context) -> None:
    g = ctx.guild
    staff = custom(g, "batcat_staff", "👥") if g else "👥"
    setup = custom(g, "batcat_settings", "⚙️") if g else "⚙️"
    embed = discord.Embed(title=f"{setup} Batcat Staff Management Bot — Help Center", color=0x8B5CF6)
    embed.description = "Configure a rank ladder, then manage staff with the commands below. Prefix: `>`"
    embed.add_field(name=f"{staff} Staff Supervisor Commands", value="`>promo @member [@role]` — Promote to the next rank, or to the supplied role\n`>demo @member [@role]` — Demote one rank, or to the supplied role", inline=False)
    embed.add_field(name=f"{setup} Owner Setup Commands", value="`>setranks` — Choose and order the staff rank ladder\n`>setstaffsupervisor @role` — Set who may manage staff\n`>setpromodemo #channel` — Set promotion announcement channel\n`>setaudit #channel` — Set the audit log channel\n`>staffsettings` — View this server's configuration", inline=False)
    embed.set_footer(text="Setup commands require Administrator or Manage Server. Staff actions require the supervisor role.")
    await ctx.reply(embed=embed, mention_author=False)


class RankPicker(discord.ui.View):
    def __init__(self, author_id: int, roles: list[discord.Role]):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.roles = roles
        self.ranked: list[discord.Role] = []
        self.select = discord.ui.RoleSelect(placeholder="Choose the next rank, starting with the lowest", min_values=1, max_values=1, role=discord.Role)
        self.select.callback = self.selected
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who opened this menu can use it.", ephemeral=True)
            return False
        return True

    async def selected(self, interaction: discord.Interaction) -> None:
        role = self.select.values[0]
        if role not in self.roles:
            await interaction.response.send_message("That role can't be assigned by the bot. Choose a normal role below the bot's highest role.", ephemeral=True)
            return
        if role not in self.ranked:
            self.ranked.append(role)
        order = " → ".join(r.mention for r in self.ranked)
        await interaction.response.edit_message(content=f"Rank order so far (lowest → highest): {order}\nChoose the next role, or press **Save ladder**.", view=self)

    @discord.ui.button(label="Save ladder", style=discord.ButtonStyle.success)
    async def save_ladder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.ranked:
            await interaction.response.send_message("Select at least one rank first.", ephemeral=True)
            return
        cfg = store.guild(interaction.guild_id)
        cfg["ranks"] = [role.id for role in self.ranked]
        store.save()
        names = [interaction.guild.get_role(rid).mention for rid in cfg["ranks"]]
        await interaction.response.edit_message(content="Rank ladder saved (lowest → highest): " + " → ".join(names), view=None)
        self.stop()

    @discord.ui.button(label="Undo last", style=discord.ButtonStyle.secondary)
    async def undo_last(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.ranked:
            self.ranked.pop()
        order = " → ".join(r.mention for r in self.ranked) or "(none yet)"
        await interaction.response.edit_message(content=f"Rank order so far (lowest → highest): {order}\nChoose the next role, or press **Save ladder**.", view=self)


@bot.command(name="setranks")
async def set_ranks(ctx: commands.Context) -> None:
    if not await require_supervisor(ctx):
        return
    roles = [r for r in ctx.guild.roles if not r.is_default() and not r.managed and r < ctx.guild.me.top_role]
    if not roles:
        await ctx.reply("I can't see any assignable roles below my highest role. Move my bot role above the staff ranks.", mention_author=False)
        return
    await ctx.reply("Choose ranks **one at a time from lowest to highest**, then press **Save ladder**. Use **Undo last** to fix a selection.", view=RankPicker(ctx.author.id, roles), mention_author=False)


@bot.command(name="setstaffsupervisor")
async def set_supervisor(ctx: commands.Context, role: discord.Role) -> None:
    if not await require_supervisor(ctx):
        return
    store.guild(ctx.guild.id)["supervisor_role"] = role.id
    store.save()
    await ctx.reply(f"Staff supervisor role set to {role.mention}.", mention_author=False)


@bot.command(name="setpromodemo")
async def set_promo_channel(ctx: commands.Context, channel: discord.TextChannel) -> None:
    if not await require_supervisor(ctx):
        return
    store.guild(ctx.guild.id)["promo_channel"] = channel.id
    store.save()
    await ctx.reply(f"Promotion/demotion announcements will go to {channel.mention}.", mention_author=False)


@bot.command(name="setaudit")
async def set_audit(ctx: commands.Context, channel: discord.TextChannel) -> None:
    if not await require_supervisor(ctx):
        return
    store.guild(ctx.guild.id)["audit_channel"] = channel.id
    store.save()
    await ctx.reply(f"Audit logs will go to {channel.mention}.", mention_author=False)


@bot.command(name="staffsettings")
async def staff_settings(ctx: commands.Context) -> None:
    cfg = store.guild(ctx.guild.id)
    ranks = [ctx.guild.get_role(rid) for rid in cfg["ranks"]]
    ranks = [role.mention for role in ranks if role]
    supervisor = ctx.guild.get_role(cfg["supervisor_role"]) if cfg.get("supervisor_role") else None
    promo = ctx.guild.get_channel(cfg["promo_channel"]) if cfg.get("promo_channel") else None
    audit_ch = ctx.guild.get_channel(cfg["audit_channel"]) if cfg.get("audit_channel") else None
    embed = discord.Embed(title=f"{custom(ctx.guild, 'batcat_settings', '⚙️')} Batcat Staff Bot Settings", color=0x8B5CF6)
    embed.add_field(name="Rank ladder", value=" → ".join(ranks) or "Not configured", inline=False)
    embed.add_field(name="Supervisor role", value=supervisor.mention if supervisor else "Not configured", inline=True)
    embed.add_field(name="Promo/demotion channel", value=promo.mention if promo else "Not configured", inline=True)
    embed.add_field(name="Audit channel", value=audit_ch.mention if audit_ch else "Not configured", inline=True)
    embed.add_field(name="Custom emojis", value=f"{len(cfg.get('emojis', {}))}/{len(EMOJI_NAMES)} installed", inline=True)
    await ctx.reply(embed=embed, mention_author=False)


async def staff_action(ctx: commands.Context, member: discord.Member, role: discord.Role | None, promote: bool) -> None:
    if not await require_supervisor(ctx):
        return
    if member == ctx.author and not ctx.author.guild_permissions.administrator:
        await ctx.reply("You can't change your own staff rank.", mention_author=False)
        return
    cfg = store.guild(ctx.guild.id)
    rank_ids = cfg.get("ranks", [])
    ranks = [ctx.guild.get_role(rid) for rid in rank_ids]
    ranks = [r for r in ranks if r]
    if not ranks:
        await ctx.reply("No rank ladder is configured. An administrator can run `>setranks`.", mention_author=False)
        return
    held = [r for r in ranks if r in member.roles]
    current = held[-1] if held else None
    if role is None:
        index = ranks.index(current) if current in ranks else (-1 if promote else len(ranks))
        target_index = index + (1 if promote else -1)
        if target_index < 0 or target_index >= len(ranks):
            await ctx.reply(f"{member.mention} is already at the {'highest' if promote else 'lowest'} configured rank.", mention_author=False)
            return
        role = ranks[target_index]
    elif role not in ranks:
        await ctx.reply("That role is not in the configured rank ladder.", mention_author=False)
        return
    if role >= ctx.guild.me.top_role:
        await ctx.reply("I can't assign that role. Move my bot role above it in Server Settings → Roles.", mention_author=False)
        return
    if role not in member.roles:
        try:
            await member.add_roles(role, reason=f"Staff rank {'promotion' if promote else 'demotion'} by {ctx.author}")
        except discord.Forbidden:
            await ctx.reply("I don't have permission to manage that member's roles. Check my role position and Manage Roles permission.", mention_author=False)
            return
    old_ranks = [held_role for held_role in held if held_role != role]
    if old_ranks:
        try:
            await member.remove_roles(*old_ranks, reason=f"Staff rank updated by {ctx.author}")
        except discord.Forbidden:
            await ctx.reply("I added the new rank, but could not remove the previous rank roles. Check my role position.", mention_author=False)
            return
    verb = "promoted" if promote else "demoted"
    direction = custom(ctx.guild, "batcat_rankup" if promote else "batcat_rankdown", "🔼" if promote else "🔽")
    message = f"{direction} {member.mention} was {verb} to {role.mention} by {ctx.author.mention}."
    channel_id = cfg.get("promo_channel")
    announcement = ctx.guild.get_channel(channel_id) if channel_id else None
    if isinstance(announcement, discord.TextChannel):
        await announcement.send(message)
    await audit(ctx.guild, f"{direction} {member} ({member.id}) {verb} to {role.name} by {ctx.author} ({ctx.author.id}).")
    await ctx.reply(f"{direction} {member.mention} {verb} to {role.mention}.", mention_author=False)


@bot.command(name="promo")
async def promote(ctx: commands.Context, member: discord.Member, role: discord.Role | None = None) -> None:
    await staff_action(ctx, member, role, True)


@bot.command(name="demo")
async def demote(ctx: commands.Context, member: discord.Member, role: discord.Role | None = None) -> None:
    await staff_action(ctx, member, role, False)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Missing argument. Use `>help` for command examples.", mention_author=False)
    elif isinstance(error, commands.BadArgument):
        await ctx.reply("I couldn't find that member, role, or channel. Mention it directly, then try again.", mention_author=False)
    else:
        log.error("Command failed: %r", error, exc_info=(type(error), error, error.__traceback__))
        await ctx.reply("That command ran into an error. Check the bot logs and try again.", mention_author=False)


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Set the DISCORD_TOKEN environment variable before starting the bot.")
bot.run(token)
