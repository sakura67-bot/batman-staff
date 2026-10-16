import os
import io
import json
from datetime import datetime, timezone

import discord
from discord.ext import commands
from PIL import Image, ImageDraw


TOKEN = os.getenv("DISCORD_TOKEN")
EMOJI_GUILD_ID = os.getenv("EMOJI_GUILD_ID")

PREFIX = ">"
SETTINGS_FILE = "staff_settings.json"
HISTORY_FILE = "staff_history.json"


# name: (letter, background RGB, foreground RGB, animated)
# Letters are used so Pillow's built-in font can draw them reliably.
EMOJI_SPECS = {
    "r2g_staff":    ("S", (108, 73, 180), (255, 230, 128), False),
    "r2g_settings": ("G", (67, 78, 112), (235, 240, 255), False),
    "r2g_promote":  ("P", (36, 145, 102), (230, 255, 238), False),
    "r2g_demote":   ("D", (185, 66, 81), (255, 230, 232), False),
    "r2g_audit":    ("A", (42, 116, 170), (230, 248, 255), False),
    "r2g_star":     ("*", (186, 135, 35), (255, 245, 178), False),
    "r2g_check":    ("Y", (42, 142, 93), (225, 255, 232), False),
    "r2g_warning":  ("!", (191, 111, 38), (255, 239, 211), False),
    "r2g_shield":   ("X", (55, 89, 155), (225, 237, 255), False),
    "r2g_ticket":   ("T", (147, 74, 128), (255, 227, 246), False),
    "r2g_batflap":  ("B", (76, 54, 125), (239, 223, 255), True),
    "r2g_pawwave":  ("W", (40, 132, 142), (220, 255, 252), True),
    "r2g_crown":    ("C", (178, 124, 25), (255, 239, 153), True),
    "r2g_heart":    ("H", (174, 53, 86), (255, 221, 231), True),
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


def make_emoji_image(letter, background, foreground, animated=False):
    frames = []
    frame_count = 8 if animated else 1

    for frame_number in range(frame_count):
        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        radius = 52 + (frame_number % 4 if animated else 0)
        center = 64

        draw.ellipse(
            (center - radius, center - radius, center + radius, center + radius),
            fill=background + (255,),
            outline=foreground + (255,),
            width=6,
        )

        bounds = draw.textbbox((0, 0), letter)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]

        draw.text(
            (center - width / 2, center - height / 2 - bounds[1]),
            letter,
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


def make_embed(title, description="", color=discord.Color.blurple()):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Resell2Gain | Rank Management")
    return embed


def emoji(name, fallback="✨"):
    return emoji_tags.get(name, fallback)


async def reply_embed(ctx, title, description, color=discord.Color.blurple()):
    await ctx.reply(
        embed=make_embed(title, description, color),
        mention_author=False,
    )


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
        await reply_embed(
            ctx,
            f"{emoji('r2g_shield', '🛡️')} Permission needed",
            "You need Administrator permission or the configured supervisor role.",
            discord.Color.red(),
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


async def send_configured_message(guild, setting_name, title, description, color):
    channel_id = get_settings(guild.id).get(setting_name)
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel:
        try:
            await channel.send(embed=make_embed(title, description, color))
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
                print("EMOJI_GUILD_ID must be the server's numeric ID.")
                return

        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            print(f"Missing Create Expressions permission in {guild.name}.")
            continue

        existing = {item.name: item for item in guild.emojis}

        for name, (letter, bg, fg, animated) in EMOJI_SPECS.items():
            if name in existing:
                emoji_tags[name] = str(existing[name])
                continue

            try:
                image_data = make_emoji_image(letter, bg, fg, animated)
                created = await guild.create_custom_emoji(
                    name=name,
                    image=image_data,
                    reason="Resell2Gain Rank Management emoji setup",
                )
                emoji_tags[name] = str(created)
                print(f"Created {name} in {guild.name}.")
            except discord.HTTPException as error:
                print(f"Could not create {name}: {error}")

        if EMOJI_GUILD_ID:
            break


@bot.command(name="help", aliases=["commands"])
async def help_command(ctx):
    embed = make_embed(
        f"{emoji('r2g_staff', '👥')} Resell2Gain | Rank Management",
        f"Bot prefix: `{PREFIX}`",
    )

    embed.add_field(
        name=f"{emoji('r2g_promote', '⬆️')} Staff commands",
        value=(
            "`>promo @member [@role]` — promote to next or selected rank\n"
            "`>demo @member [@role]` — demote one rank or to a lower rank\n"
            "`>staffrank @member` — check a member's configured rank\n"
            "`>stafflist` — list members in configured staff ranks\n"
            "`>staffhistory [@member]` — view recent rank actions"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"{emoji('r2g_settings', '⚙️')} Administrator setup",
        value=(
            "`>setranks` — view the rank ladder\n"
            "`>setranks add @role` — add a rank at the bottom\n"
            "`>setranks remove @role` — remove a rank\n"
            "`>setranks clear` — clear the rank ladder\n"
            "`>setstaffsupervisor @role` — set the supervisor role\n"
            "`>setpromodemo #channel` — choose announcement channel\n"
            "`>setaudit #channel` — choose audit channel\n"
            "`>staffsettings` — view current settings\n"
            "`>emojis` — view the bot emoji"
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
        lines = [
            f"`{number}.` <@&{role_id}>"
            for number, role_id in enumerate(ranks, start=1)
            if ctx.guild.get_role(role_id)
        ]
        await reply_embed(
            ctx,
            f"{emoji('r2g_settings', '⚙️')} Rank ladder",
            (
                "**Lowest to highest:**\n"
                + ("\n".join(lines) if lines else "No ranks configured.")
                + "\n\nUse `>setranks add @role`, `>setranks remove @role`, "
                  "or `>setranks clear`."
            ),
        )
        return

    action = action.lower()

    if action == "clear":
        config["ranks"] = []
        save_json(SETTINGS_FILE, settings)
        await reply_embed(ctx, f"{emoji('r2g_check', '✅')} Rank ladder cleared", "All configured ranks were removed.")
        return

    if role is None:
        await reply_embed(
            ctx,
            f"{emoji('r2g_warning', '⚠️')} Role missing",
            "Mention a role. Example: `>setranks add @Trainee`",
            discord.Color.orange(),
        )
        return

    if action == "add":
        if role.id in ranks:
            await reply_embed(ctx, "Already added", f"{role.mention} is already in the rank ladder.", discord.Color.orange())
            return

        ranks.append(role.id)
        save_json(SETTINGS_FILE, settings)
        await reply_embed(
            ctx,
            f"{emoji('r2g_check', '✅')} Rank added",
            f"{role.mention} was added at the bottom of the ladder.",
            discord.Color.green(),
        )

    elif action == "remove":
        if role.id not in ranks:
            await reply_embed(ctx, "Rank not found", f"{role.mention} is not in the rank ladder.", discord.Color.orange())
            return

        ranks.remove(role.id)
        save_json(SETTINGS_FILE, settings)
        await reply_embed(
            ctx,
            f"{emoji('r2g_check', '✅')} Rank removed",
            f"{role.mention} was removed from the ladder.",
            discord.Color.green(),
        )

    else:
        await reply_embed(
            ctx,
            f"{emoji('r2g_warning', '⚠️')} Invalid option",
            "Use `add`, `remove`, or `clear`. Example: `>setranks add @Trainee`",
            discord.Color.orange(),
        )


@bot.command(name="promo", aliases=["promote"])
@commands.guild_only()
async def promote(ctx, member: discord.Member, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    ranks = [
        role_id for role_id in get_settings(ctx.guild.id)["ranks"]
        if ctx.guild.get_role(role_id)
    ]

    if not ranks:
        await reply_embed(ctx, "No rank ladder", "Set one up with `>setranks add @role`.", discord.Color.orange())
        return

    member_role_ids = {item.id for item in member.roles}
    current = [i for i, role_id in enumerate(ranks) if role_id in member_role_ids]

    if role:
        if role.id not in ranks:
            await reply_embed(ctx, "Rank not found", "That role is not in the configured ladder.", discord.Color.orange())
            return
        target_index = ranks.index(role.id)
    else:
        target_index = max(current, default=-1) + 1
        if target_index >= len(ranks):
            await reply_embed(ctx, "Highest rank", f"{member.mention} is already at the highest configured rank.", discord.Color.orange())
            return

    target = ctx.guild.get_role(ranks[target_index])

    try:
        old_roles = [
            ctx.guild.get_role(role_id)
            for role_id in ranks
            if role_id in member_role_ids and ctx.guild.get_role(role_id)
        ]
        if old_roles:
            await member.remove_roles(*old_roles, reason=f"Promotion by {ctx.author}")
        await member.add_roles(target, reason=f"Promotion by {ctx.author}")
    except discord.Forbidden:
        await reply_embed(
            ctx,
            f"{emoji('r2g_warning', '⚠️')} Missing role access",
            "Give the bot Manage Roles and move its role above the staff rank roles.",
            discord.Color.red(),
        )
        return
    except discord.HTTPException as error:
        await reply_embed(ctx, "Discord role error", str(error), discord.Color.red())
        return

    record_action(ctx.guild, ctx.author, member, "promoted", target)
    await reply_embed(
        ctx,
        f"{emoji('r2g_promote', '⬆️')} Promotion complete",
        f"{member.mention} was promoted to {target.mention}.",
        discord.Color.green(),
    )
    await send_configured_message(
        ctx.guild,
        "announcement_channel",
        f"{emoji('r2g_promote', '⬆️')} Promotion",
        f"{member.mention} was promoted to {target.mention}.",
        discord.Color.green(),
    )
    await send_configured_message(
        ctx.guild,
        "audit_channel",
        f"{emoji('r2g_audit', '📋')} Promotion audit",
        f"Moderator: {ctx.author.mention}\nMember: {member.mention}\nNew rank: {target.mention}",
        discord.Color.blurple(),
    )


@bot.command(name="demo", aliases=["demote"])
@commands.guild_only()
async def demote(ctx, member: discord.Member, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    ranks = [
        role_id for role_id in get_settings(ctx.guild.id)["ranks"]
        if ctx.guild.get_role(role_id)
    ]
    member_role_ids = {item.id for item in member.roles}
    current = [i for i, role_id in enumerate(ranks) if role_id in member_role_ids]

    if not current:
        await reply_embed(ctx, "No staff rank", f"{member.mention} has no configured rank.", discord.Color.orange())
        return

    current_index = max(current)

    if role:
        if role.id not in ranks:
            await reply_embed(ctx, "Rank not found", "That role is not in the configured ladder.", discord.Color.orange())
            return
        target_index = ranks.index(role.id)
        if target_index >= current_index:
            await reply_embed(ctx, "Invalid demotion", "Choose a rank below the member's current rank.", discord.Color.orange())
            return
    else:
        target_index = current_index - 1

    target = ctx.guild.get_role(ranks[target_index]) if target_index >= 0 else None

    try:
        old_roles = [
            ctx.guild.get_role(role_id)
            for role_id in ranks
            if role_id in member_role_ids and ctx.guild.get_role(role_id)
        ]
        if old_roles:
            await member.remove_roles(*old_roles, reason=f"Demotion by {ctx.author}")
        if target:
            await member.add_roles(target, reason=f"Demotion by {ctx.author}")
    except discord.Forbidden:
        await reply_embed(
            ctx,
            f"{emoji('r2g_warning', '⚠️')} Missing role access",
            "Give the bot Manage Roles and move its role above the staff rank roles.",
            discord.Color.red(),
        )
        return
    except discord.HTTPException as error:
        await reply_embed(ctx, "Discord role error", str(error), discord.Color.red())
        return

    record_action(ctx.guild, ctx.author, member, "demoted", target)

    result = (
        f"{member.mention} was demoted to {target.mention}."
        if target
        else f"The lowest staff rank was removed from {member.mention}."
    )

    await reply_embed(
        ctx,
        f"{emoji('r2g_demote', '⬇️')} Demotion complete",
        result,
        discord.Color.orange(),
    )
    await send_configured_message(
        ctx.guild,
        "announcement_channel",
        f"{emoji('r2g_demote', '⬇️')} Demotion",
        result,
        discord.Color.orange(),
    )
    await send_configured_message(
        ctx.guild,
        "audit_channel",
        f"{emoji('r2g_audit', '📋')} Demotion audit",
        f"Moderator: {ctx.author.mention}\nResult: {result}",
        discord.Color.blurple(),
    )


@bot.command(name="setstaffsupervisor")
@commands.guild_only()
async def set_staff_supervisor(ctx, role: discord.Role):
    if not ctx.author.guild_permissions.administrator:
        await reply_embed(ctx, "Administrator required", "Only an administrator can set the supervisor role.", discord.Color.red())
        return

    get_settings(ctx.guild.id)["supervisor_role"] = role.id
    save_json(SETTINGS_FILE, settings)
    await reply_embed(ctx, f"{emoji('r2g_check', '✅')} Supervisor role set", f"Supervisor role: {role.mention}", discord.Color.green())


@bot.command(name="setpromodemo")
@commands.guild_only()
async def set_promo_demo(ctx, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        await reply_embed(ctx, "Administrator required", "Only an administrator can set this channel.", discord.Color.red())
        return

    get_settings(ctx.guild.id)["announcement_channel"] = channel.id
    save_json(SETTINGS_FILE, settings)
    await reply_embed(ctx, f"{emoji('r2g_check', '✅')} Announcement channel set", f"Announcements will go to {channel.mention}.", discord.Color.green())


@bot.command(name="setaudit")
@commands.guild_only()
async def set_audit(ctx, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        await reply_embed(ctx, "Administrator required", "Only an administrator can set this channel.", discord.Color.red())
        return

    get_settings(ctx.guild.id)["audit_channel"] = channel.id
    save_json(SETTINGS_FILE, settings)
    await reply_embed(ctx, f"{emoji('r2g_check', '✅')} Audit channel set", f"Audit messages will go to {channel.mention}.", discord.Color.green())


@bot.command(name="staffsettings")
@commands.guild_only()
async def staff_settings(ctx):
    if not await require_staff(ctx):
        return

    config = get_settings(ctx.guild.id)
    rank_mentions = [
        ctx.guild.get_role(role_id).mention
        for role_id in config["ranks"]
        if ctx.guild.get_role(role_id)
    ]
    supervisor = ctx.guild.get_role(config["supervisor_role"]) if config["supervisor_role"] else None
    announcement = ctx.guild.get_channel(config["announcement_channel"]) if config["announcement_channel"] else None
    audit = ctx.guild.get_channel(config["audit_channel"]) if config["audit_channel"] else None

    embed = make_embed(
        f"{emoji('r2g_settings', '⚙️')} Staff bot settings",
        "Current configuration for this server.",
    )
    embed.add_field(
        name=f"{emoji('r2g_star', '⭐')} Rank ladder",
        value="\n".join(rank_mentions) or "Not configured",
        inline=False,
    )
    embed.add_field(name="🛡️ Supervisor", value=supervisor.mention if supervisor else "Not configured")
    embed.add_field(name="📣 Announcements", value=announcement.mention if announcement else "Not configured")
    embed.add_field(name="📋 Audit log", value=audit.mention if audit else "Not configured")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="staffrank")
@commands.guild_only()
async def staff_rank(ctx, member: discord.Member):
    config = get_settings(ctx.guild.id)
    member_role_ids = {item.id for item in member.roles}
    member_ranks = [
        ctx.guild.get_role(role_id)
        for role_id in config["ranks"]
        if role_id in member_role_ids and ctx.guild.get_role(role_id)
    ]

    if member_ranks:
        text = f"{member.mention}'s configured rank is {member_ranks[-1].mention}."
    else:
        text = f"{member.mention} has no configured staff rank."

    await reply_embed(ctx, f"{emoji('r2g_staff', '👥')} Staff rank", text)


@bot.command(name="stafflist")
@commands.guild_only()
async def staff_list(ctx):
    config = get_settings(ctx.guild.id)
    rank_ids = [rid for rid in config["ranks"] if ctx.guild.get_role(rid)]

    if not rank_ids:
        await reply_embed(ctx, "No ranks configured", "Add ranks with `>setranks add @role`.", discord.Color.orange())
        return

    lines = []
    for role_id in rank_ids:
        role = ctx.guild.get_role(role_id)
        members = [member.mention for member in role.members]
        lines.append(
            f"**{emoji('r2g_star', '⭐')} {role.name} ({len(members)})**\n"
            + (", ".join(members[:20]) or "No members")
        )

    embed = make_embed(f"{emoji('r2g_staff', '👥')} Staff list", "\n\n".join(lines))
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
        await reply_embed(ctx, "No history found", "No saved staff actions matched.", discord.Color.orange())
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
            f"{emoji('r2g_audit', '📋')} `{item['action']}` "
            f"{target_text}{role_text}\n"
            f"└ By {actor_text}"
        )

    embed = make_embed(f"{emoji('r2g_audit', '📋')} Recent staff history", "\n\n".join(lines))
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="emojis")
async def show_emojis(ctx):
    lines = [
        f"{emoji_tags.get(name, '▫️')} **{name}**"
        for name in EMOJI_SPECS
    ]
    embed = make_embed(
        f"{emoji('r2g_heart', '💜')} Bot emoji collection",
        "\n".join(lines),
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await reply_embed(
            ctx,
            f"{emoji('r2g_warning', '⚠️')} Missing information",
            f"Try `{PREFIX}help` for command examples.",
            discord.Color.orange(),
        )
        return

    if isinstance(error, commands.BadArgument):
        await reply_embed(
            ctx,
            f"{emoji('r2g_warning', '⚠️')} Couldn't find that",
            "Mention a valid member, role, or channel in the command.",
            discord.Color.orange(),
        )
        return

    if isinstance(error, commands.NoPrivateMessage):
        await reply_embed(ctx, "Server command", "This command only works in a server.")
        return

    print(f"Command error: {error!r}")
    await reply_embed(
        ctx,
        f"{emoji('r2g_warning', '⚠️')} Command error",
        "The command failed. Check the host console for the error details.",
        discord.Color.red(),
    )


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add it in your hosting dashboard's Environment tab."
    )

bot.run(TOKEN)
