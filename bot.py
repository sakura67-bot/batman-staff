import os
import io
import json
from datetime import datetime, timezone

import discord
from discord.ext import commands
from PIL import Image, ImageDraw


# Add DISCORD_TOKEN in your host's Environment tab.
TOKEN = os.getenv("DISCORD_TOKEN")

PREFIX = ">"
SETTINGS_FILE = "staff_settings.json"
HISTORY_FILE = "staff_history.json"

# Optional: set EMOJI_GUILD_ID in Environment to choose the server
# where the bot creates its custom emojis. Otherwise, it uses its first server.
EMOJI_GUILD_ID = os.getenv("EMOJI_GUILD_ID")


# name: (symbol, circle color, symbol color, animated)
EMOJI_SPECS = {
    "r2g_staff": ("S", (108, 73, 180), (255, 230, 128), False),
    "r2g_settings": ("⚙", (67, 78, 112), (235, 240, 255), False),
    "r2g_promote": ("↑", (36, 145, 102), (230,255, 238), False),
    "r2g_demote": ("↓", (185, 66, 81), (255, 230, 232), False),
    "r2g_audit": ("✓", (42, 116, 170), (230, 248, 255), False),
    "r2g_star": ("★", (186, 135, 35), (255, 245, 178), False),
    "r2g_check": ("✔", (42, 142, 93), (225, 255, 232), False),
    "r2g_warning": ("!", (191, 111, 38), (255, 239, 211), False),
    "r2g_shield": ("◆", (55, 89, 155), (225, 237, 255), False),
    "r2g_ticket": ("T", (147, 74, 128), (255, 227, 246), False),
    "r2g_batflap": ("B", (76, 54, 125), (239, 223, 255), True),
    "r2g_pawwave": ("P", (40, 132, 142), (220, 255, 252), True),
    "r2g_crown": ("♛", (178, 124, 25), (255, 239, 153), True),
    "r2g_heart": ("♥", (174, 53, 86), (255, 221, 231), True),
}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


settings = load_json(SETTINGS_FILE, {})
history = load_json(HISTORY_FILE, [])
emoji_tags = {}


def get_settings(guild_id):
    key = str(guild_id)
    if key not in settings:
        settings[key] = {
            "ranks": [],
            "supervisor_role": None,
            "announcement_channel": None,
            "audit_channel": None,
        }
    return settings[key]


def make_emoji_image(symbol, background, foreground, animated=False):
    frames = []
    frame_count = 8 if animated else 1

    for frame_number in range(frame_count):
        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        radius = 54
        if animated:
            radius += (frame_number % 4) * 2

        center = 64
        draw.ellipse(
            (center - radius, center - radius, center + radius, center + radius),
            fill=background + (255,),
            outline=foreground + (255,),
            width=5,
        )

        bounds = draw.textbbox((0, 0), symbol)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        draw.text(
            (
                center - text_width / 2,
                center - text_height / 2 - bounds[1],
            ),
            symbol,
            fill=foreground + (255,),
        )
        frames.append(image)

    output = io.BytesIO()
    if animated:
        frames[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=120,
            loop=0,
            disposal=2,
        )
    else:
        frames[0].save(output, format="PNG")

    return output.getvalue()


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True,
)


def is_staff_supervisor(ctx):
    if ctx.guild is None:
        return False

    if ctx.author.guild_permissions.administrator:
        return True

    supervisor_id = get_settings(ctx.guild.id).get("supervisor_role")
    return bool(
        supervisor_id
        and any(role.id == supervisor_id for role in ctx.author.roles)
    )


async def require_staff(ctx):
    if not is_staff_supervisor(ctx):
        await ctx.reply(
            f"{emoji_tags.get('r2g_shield', '🛡️')} "
            "You need Administrator permission or the configured supervisor role.",
            mention_author=False,
        )
        return False
    return True


def record_action(guild, actor, target, action, role=None):
    history.append({
        "time": datetime.now(timezone.utc).isoformat(),
        "guild_id": guild.id,
        "actor_id": actor.id,
        "target_id": target.id,
        "action": action,
        "role_id": role.id if role else None,
    })
    save_json(HISTORY_FILE, history)


async def send_configured_message(guild, setting_name, message):
    channel_id = get_settings(guild.id).get(setting_name)
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel:
        try:
            await channel.send(message)
        except discord.HTTPException:
            pass


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} | Prefix: {PREFIX}")

    for guild in bot.guilds:
        if EMOJI_GUILD_ID:
            try:
                if guild.id != int(EMOJI_GUILD_ID):
                    continue
            except ValueError:
                print("EMOJI_GUILD_ID must contain only the server ID.")
                return

        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            print(f"Missing Manage Expressions permission in {guild.name}.")
            continue

        existing = {emoji.name: emoji for emoji in guild.emojis}

        for name, (symbol, background, foreground, animated) in EMOJI_SPECS.items():
            if name in existing:
                emoji_tags[name] = str(existing[name])
                continue

            try:
                image_data = make_emoji_image(
                    symbol, background, foreground, animated
                )
                emoji = await guild.create_custom_emoji(
                    name=name,
                    image=image_data,
                    reason="Resell2Gain Rank Management emoji setup",
                )
                emoji_tags[name] = str(emoji)
                print(f"Created emoji {name} in {guild.name}.")
            except discord.HTTPException as error:
                print(f"Could not create emoji {name}: {error}")

        if EMOJI_GUILD_ID:
            break


@bot.command(name="help", aliases=["commands"])
async def help_command(ctx):
    embed = discord.Embed(
        title=f"{emoji_tags.get('r2g_staff', '👥')} Resell2Gain | Rank Management",
        description=f"Prefix: `{PREFIX}`",
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Staff commands",
        value=(
            "`>promo @member [@role]` — promote to the next rank or a chosen rank\n"
            "`>demo @member [@role]` — demote one rank or to a chosen lower rank\n"
            "`>staffrank @member` — check a member's configured rank\n"
            "`>stafflist` — list staff in the configured ranks\n"
            "`>staffhistory [@member]` — show recent rank actions"
        ),
        inline=False,
    )
    embed.add_field(
        name="Administrator setup",
        value=(
            "`>setranks` — view the rank ladder\n"
            "`>setranks add @role` — add a rank at the bottom\n"
            "`>setranks remove @role` — remove a rank\n"
            "`>setranks clear` — clear the ladder\n"
            "`>setstaffsupervisor @role` — set who can manage staff\n"
            "`>setpromodemo #channel` — choose announcement channel\n"
            "`>setaudit #channel` — choose audit log channel\n"
            "`>staffsettings` — view current settings\n"
            "`>emojis` — show the emoji list"
        ),
        inline=False,
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="setranks")
@commands.guild_only()
async def setranks(ctx, action: str = None, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    config = get_settings(ctx.guild.id)
    ranks = config["ranks"]

    if action is None:
        rank_lines = [
            f"`{number}.` <@&{role_id}>"
            for number, role_id in enumerate(ranks, start=1)
            if ctx.guild.get_role(role_id)
        ]
        await ctx.reply(
            "**Rank ladder, lowest to highest:**\n"
            + ("\n".join(rank_lines) if rank_lines else "No ranks configured.")
            + "\n\nUse `>setranks add @role`, `>setranks remove @role`, "
              "or `>setranks clear`.",
            mention_author=False,
        )
        return

    action = action.lower()

    if action == "clear":
        config["ranks"] = []
        save_json(SETTINGS_FILE, settings)
        await ctx.reply("Rank ladder cleared.", mention_author=False)
        return

    if role is None:
        await ctx.reply(
            "Mention a role. Example: `>setranks add @Trainee`",
            mention_author=False,
        )
        return

    if action == "add":
        if role.id in ranks:
            await ctx.reply("That role is already in the ladder.", mention_author=False)
            return
        ranks.append(role.id)
        save_json(SETTINGS_FILE, settings)
        await ctx.reply(f"Added {role.mention} at the bottom of the ladder.", mention_author=False)

    elif action == "remove":
        if role.id not in ranks:
            await ctx.reply("That role is not in the ladder.", mention_author=False)
            return
        ranks.remove(role.id)
        save_json(SETTINGS_FILE, settings)
        await ctx.reply(f"Removed {role.mention} from the ladder.", mention_author=False)

    else:
        await ctx.reply(
            "Use `add`, `remove`, or `clear`. Example: `>setranks add @Trainee`",
            mention_author=False,
        )


@bot.command(name="promo", aliases=["promote"])
@commands.guild_only()
async def promote(ctx, member: discord.Member, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    config = get_settings(ctx.guild.id)
    ranks = [
        role_id for role_id in config["ranks"]
        if ctx.guild.get_role(role_id)
    ]

    if not ranks:
        await ctx.reply("No rank ladder set up. Use `>setranks add @role`.", mention_author=False)
        return

    member_role_ids = {member_role.id for member_role in member.roles}
    current_indexes = [
        index for index, role_id in enumerate(ranks)
        if role_id in member_role_ids
    ]

    if role:
        if role.id not in ranks:
            await ctx.reply("That role isn't in the configured rank ladder.", mention_author=False)
            return
        target_index = ranks.index(role.id)
    else:
        target_index = max(current_indexes, default=-1) + 1
        if target_index >= len(ranks):
            await ctx.reply(f"{member.mention} is already at the highest rank.", mention_author=False)
            return

    target_role = ctx.guild.get_role(ranks[target_index])

    try:
        old_roles = [
            ctx.guild.get_role(role_id)
            for role_id in ranks
            if role_id in member_role_ids and ctx.guild.get_role(role_id)
        ]
        if old_roles:
            await member.remove_roles(*old_roles, reason=f"Promotion by {ctx.author}")
        await member.add_roles(target_role, reason=f"Promotion by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(
            "I can't manage those roles. Give me Manage Roles and move my bot role above the staff ranks.",
            mention_author=False,
        )
        return
    except discord.HTTPException as error:
        await ctx.reply(f"Discord could not update the roles: {error}", mention_author=False)
        return

    record_action(ctx.guild, ctx.author, member, "promoted", target_role)
    message = (
        f"{emoji_tags.get('r2g_promote', '⬆️')} "
        f"Promoted {member.mention} to {target_role.mention}."
    )
    await ctx.reply(message, mention_author=False)
    await send_configured_message(ctx.guild, "announcement_channel", message)
    await send_configured_message(
        ctx.guild,
        "audit_channel",
        f"Promotion by {ctx.author.mention}: {member.mention} → {target_role.mention}",
    )


@bot.command(name="demo", aliases=["demote"])
@commands.guild_only()
async def demote(ctx, member: discord.Member, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    config = get_settings(ctx.guild.id)
    ranks = [
        role_id for role_id in config["ranks"]
        if ctx.guild.get_role(role_id)
    ]
    member_role_ids = {member_role.id for member_role in member.roles}
    current_indexes = [
        index for index, role_id in enumerate(ranks)
        if role_id in member_role_ids
    ]

    if not current_indexes:
        await ctx.reply(f"{member.mention} has no configured staff rank.", mention_author=False)
        return

    current_index = max(current_indexes)

    if role:
        if role.id not in ranks:
            await ctx.reply("That role isn't in the configured rank ladder.", mention_author=False)
            return
        target_index = ranks.index(role.id)
        if target_index >= current_index:
            await ctx.reply("Choose a rank below the member's current rank.", mention_author=False)
            return
    else:
        target_index = current_index - 1

    target_role = ctx.guild.get_role(ranks[target_index]) if target_index >= 0 else None

    try:
        old_roles = [
            ctx.guild.get_role(role_id)
            for role_id in ranks
            if role_id in member_role_ids and ctx.guild.get_role(role_id)
        ]
        if old_roles:
            await member.remove_roles(*old_roles, reason=f"Demotion by {ctx.author}")
        if target_role:
            await member.add_roles(target_role, reason=f"Demotion by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(
            "I can't manage those roles. Give me Manage Roles and move my bot role above the staff ranks.",
            mention_author=False,
        )
        return
    except discord.HTTPException as error:
        await ctx.reply(f"Discord could not update the roles: {error}", mention_author=False)
        return

    record_action(ctx.guild, ctx.author, member, "demoted", target_role)

    if target_role:
        message = f"{emoji_tags.get('r2g_demote', '⬇️')} Demoted {member.mention} to {target_role.mention}."
    else:
        message = f"{emoji_tags.get('r2g_demote', '⬇️')} Removed the lowest staff rank from {member.mention}."

    await ctx.reply(message, mention_author=False)
    await send_configured_message(ctx.guild, "announcement_channel", message)
    await send_configured_message(
        ctx.guild,
        "audit_channel",
        f"Demotion by {ctx.author.mention}: {message}",
    )


@bot.command(name="setstaffsupervisor")
@commands.guild_only()
async def set_staff_supervisor(ctx, role: discord.Role):
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("Only an administrator can set the supervisor role.", mention_author=False)
        return

    get_settings(ctx.guild.id)["supervisor_role"] = role.id
    save_json(SETTINGS_FILE, settings)
    await ctx.reply(f"Supervisor role set to {role.mention}.", mention_author=False)


@bot.command(name="setpromodemo")
@commands.guild_only()
async def set_promo_demo(ctx, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("Only an administrator can set this channel.", mention_author=False)
        return

    get_settings(ctx.guild.id)["announcement_channel"] = channel.id
    save_json(SETTINGS_FILE, settings)
    await ctx.reply(f"Promotion and demotion announcements go to {channel.mention}.", mention_author=False)


@bot.command(name="setaudit")
@commands.guild_only()
async def set_audit(ctx, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("Only an administrator can set this channel.", mention_author=False)
        return

    get_settings(ctx.guild.id)["audit_channel"] = channel.id
    save_json(SETTINGS_FILE, settings)
    await ctx.reply(f"Audit messages go to {channel.mention}.", mention_author=False)


@bot.command(name="staffsettings")
@commands.guild_only()
async def staff_settings(ctx):
    if not await require_staff(ctx):
        return

    config = get_settings(ctx.guild.id)
    rank_names = [
        ctx.guild.get_role(role_id).mention
        for role_id in config["ranks"]
        if ctx.guild.get_role(role_id)
    ]
    supervisor = ctx.guild.get_role(config["supervisor_role"]) if config["supervisor_role"] else None
    announcement = ctx.guild.get_channel(config["announcement_channel"]) if config["announcement_channel"] else None
    audit = ctx.guild.get_channel(config["audit_channel"]) if config["audit_channel"] else None

    embed = discord.Embed(
        title=f"{emoji_tags.get('r2g_settings', '⚙️')} Staff Bot Settings",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Rank ladder (lowest to highest)",
        value="\n".join(rank_names) or "Not configured",
        inline=False,
    )
    embed.add_field(
        name="Supervisor role",
        value=supervisor.mention if supervisor else "Not configured",
    )
    embed.add_field(
        name="Announcement channel",
        value=announcement.mention if announcement else "Not configured",
    )
    embed.add_field(
        name="Audit channel",
        value=audit.mention if audit else "Not configured",
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="staffrank")
@commands.guild_only()
async def staff_rank(ctx, member: discord.Member):
    config = get_settings(ctx.guild.id)
    member_role_ids = {role.id for role in member.roles}
    ranks = [
        ctx.guild.get_role(role_id)
        for role_id in config["ranks"]
        if role_id in member_role_ids and ctx.guild.get_role(role_id)
    ]

    if ranks:
        await ctx.reply(f"{member.mention}'s rank: {ranks[-1].mention}", mention_author=False)
    else:
        await ctx.reply(f"{member.mention} has no configured staff rank.", mention_author=False)


@bot.command(name="stafflist")
@commands.guild_only()
async def staff_list(ctx):
    config = get_settings(ctx.guild.id)
    rank_ids = [
        role_id for role_id in config["ranks"]
        if ctx.guild.get_role(role_id)
    ]

    if not rank_ids:
        await ctx.reply("No rank ladder is configured.", mention_author=False)
        return

    lines = []
    for role_id in rank_ids:
        role = ctx.guild.get_role(role_id)
        members = [member.mention for member in role.members]
        lines.append(
            f"**{role.name}** ({len(members)}): "
            + (", ".join(members[:20]) or "Nobody")
        )

    embed = discord.Embed(
        title="Staff List",
        description="\n".join(lines),
        color=discord.Color.purple(),
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="staffhistory")
@commands.guild_only()
async def staff_history(ctx, member: discord.Member = None):
    if not await require_staff(ctx):
        return

    entries = [
        item for item in reversed(history)
        if item.get("guild_id") == ctx.guild.id
    ]
    if member:
        entries = [item for item in entries if item.get("target_id") == member.id]

    if not entries:
        await ctx.reply("No saved staff actions found.", mention_author=False)
        return

    lines = []
    for item in entries[:10]:
        target = ctx.guild.get_member(item["target_id"])
        actor = ctx.guild.get_member(item["actor_id"])
        role = ctx.guild.get_role(item["role_id"]) if item.get("role_id") else None

        target_text = target.mention if target else f"User {item['target_id']}"
        actor_text = actor.mention if actor else f"User {item['actor_id']}"
        role_text = f" → {role.mention}" if role else ""

        lines.append(
            f"`{item['action']}` {target_text}{role_text} — by {actor_text}"
        )

    embed = discord.Embed(
        title="Recent Staff History",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="emojis")
async def show_emojis(ctx):
    lines = [
        f"{emoji_tags.get(name, '▫️')} `{name}`"
        for name in EMOJI_SPECS
    ]
    await ctx.reply("\n".join(lines), mention_author=False)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(
            f"Missing information. Try `{PREFIX}help`.",
            mention_author=False,
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.reply(
            "I couldn't find that member, role, or channel. Please mention it.",
            mention_author=False,
        )
        return

    if isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("That command only works in a server.", mention_author=False)
        return

    print(f"Command error: {error!r}")
    await ctx.reply(
        "That command ran into an error. Check the bot logs.",
        mention_author=False,
    )


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add it in your host's Environment tab."
    )

bot.run(TOKEN)
