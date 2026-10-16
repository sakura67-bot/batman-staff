import os
import io
import json
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
from PIL import Image, ImageDraw


# Required environment variable: DISCORD_TOKEN
TOKEN = os.getenv("DISCORD_TOKEN")

PREFIX = ">"
SETTINGS_FILE = "staff_settings.json"
HISTORY_FILE = "staff_history.json"

# Custom emojis are created in the first server the bot can manage expressions in.
# To choose a specific server, add EMOJI_GUILD_ID as an environment variable.
EMOJI_GUILD_ID = os.getenv("EMOJI_GUILD_ID")


EMOJI_SPECS = {
    "r2g_staff": ("S", (108, 73, 180), (255, 230, 128), False),
    "r2g_settings": ("⚙", (67, 78, 112), (235, 240, 255), False),
    "r2g_promote": ("↑", (36, 145, 102), (230, 255, 238), False),
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


def server_settings(guild_id):
    key = str(guild_id)
    if key not in settings:
        settings[key] = {
            "ranks": [],
            "supervisor_role": None,
            "announcement_channel": None,
            "audit_channel": None,
        }
    return settings[key]


def record_action(guild, actor, target, action, role=None):
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "guild_id": guild.id,
        "actor_id": actor.id,
        "target_id": target.id,
        "action": action,
        "role_id": role.id if role else None,
    }
    history.append(entry)
    save_json(HISTORY_FILE, history)


def make_emoji_image(letter, background, foreground, animated=False):
    frames = []
    frame_count = 8 if animated else 1

    for frame_number in range(frame_count):
        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # Animated emojis gently change their circle size.
        radius = 54
        if animated:
            radius += int(5 * (frame_number % 4) / 3)

        center = 64
        draw.ellipse(
            (center - radius, center - radius, center + radius, center + radius),
            fill=background + (255,),
            outline=foreground + (255,),
            width=6,
        )

        # Use a built-in font so no extra font file is required.
        box = draw.textbbox((0, 0), letter)
        text_width = box[2] - box[0]
        text_height = box[3] - box[1]
        draw.text(
            (center - text_width / 2, center - text_height / 2 - box[1]),
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


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True,
)


def has_staff_permission(ctx):
    """Allow server admins, or members with the configured supervisor role."""
    if ctx.guild is None:
        return False

    if ctx.author.guild_permissions.administrator:
        return True

    supervisor_id = server_settings(ctx.guild.id).get("supervisor_role")
    if supervisor_id and any(role.id == supervisor_id for role in ctx.author.roles):
        return True

    return False


async def require_staff(ctx):
    if not has_staff_permission(ctx):
        await ctx.reply(
            f"{emoji_tags.get('r2g_shield', '🛡️')} You need Administrator "
            "permission or the configured staff supervisor role to use this command.",
            mention_author=False,
        )
        return False
    return True


async def send_audit(guild, message):
    channel_id = server_settings(guild.id).get("audit_channel")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel:
        try:
            await channel.send(message)
        except discord.Forbidden:
            pass


async def send_announcement(guild, message):
    channel_id = server_settings(guild.id).get("announcement_channel")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel:
        try:
            await channel.send(message)
        except discord.Forbidden:
            pass


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} | Prefix: {PREFIX}")

    # Create the custom emojis once per server when possible.
    for guild in bot.guilds:
        if EMOJI_GUILD_ID and guild.id != int(EMOJI_GUILD_ID):
            continue

        if not guild.me.guild_permissions.manage_emojis_and_stickers:
            print(f"Missing Manage Expressions permission in {guild.name}; skipping emoji setup.")
            continue

        existing = {emoji.name: emoji for emoji in guild.emojis}

        for name, (letter, background, foreground, animated) in EMOJI_SPECS.items():
            if name in existing:
                emoji_tags[name] = str(existing[name])
                continue

            try:
                image_bytes = make_emoji_image(
                    letter, background, foreground, animated
                )
                filename = f"{name}.gif" if animated else f"{name}.png"
                emoji = await guild.create_custom_emoji(
                    name=name,
                    image=image_bytes,
                    reason="Resell2Gain Rank Management bot emoji setup",
                )
                emoji_tags[name] = str(emoji)
                print(f"Created emoji {name} in {guild.name}")
            except discord.HTTPException as error:
                print(f"Could not create emoji {name}: {error}")

        # If a specific server was selected, stop after checking that server.
        if EMOJI_GUILD_ID:
            break


@bot.command(name="help", aliases=["commands"])
async def help_command(ctx):
    e = emoji_tags.get("r2g_staff", "👥")
    embed = discord.Embed(
        title=f"{e} Resell2Gain | Rank Management",
        description=f"Bot prefix: `{PREFIX}`",
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Staff supervisor commands",
        value=(
            "`>promo @member [@role]` — promote to the next rank, "
            "or assign the specified rank\n"
            "`>demo @member [@role]` — demote one rank, "
            "or remove the specified rank\n"
            "`>staffrank @member` — show a member's configured rank\n"
            "`>stafflist` — list members with configured staff ranks\n"
            "`>staffhistory [@member]` — show recent rank actions"
        ),
        inline=False,
    )
    embed.add_field(
        name="Administrator setup",
        value=(
            "`>setranks` — show rank setup instructions\n"
            "`>setranks add @role` — add a rank to the bottom of the ladder\n"
            "`>setranks remove @role` — remove a rank from the ladder\n"
            "`>setranks clear` — clear the rank ladder\n"
            "`>setstaffsupervisor @role` — set the staff supervisor role\n"
            "`>setpromodemo #channel` — set the announcement channel\n"
            "`>setaudit #channel` — set the audit log channel\n"
            "`>staffsettings` — view current settings\n"
            "`>emojis` — list the bot emoji"
        ),
        inline=False,
    )
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="setranks")
@commands.guild_only()
async def setranks(ctx, action: str = None, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    config = server_settings(ctx.guild.id)
    ranks = config["ranks"]

    if action is None:
        if ranks:
            role_list = "\n".join(
                f"`{index + 1}.` <@&{role_id}>"
                for index, role_id in enumerate(ranks)
                if ctx.guild.get_role(role_id)
            )
        else:
            role_list = "No ranks configured yet."

        await ctx.reply(
            f"**Current rank ladder (lowest to highest):**\n{role_list}\n\n"
            "Use `>setranks add @role` to add a rank at the bottom, "
            "`>setranks remove @role` to remove one, or "
            "`>setranks clear` to clear the ladder.",
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
            "Mention a role too. Example: `>setranks add @Trainee`",
            mention_author=False,
        )
        return

    if action == "add":
        if role.id in ranks:
            await ctx.reply("That role is already in the rank ladder.", mention_author=False)
            return
        ranks.append(role.id)
        save_json(SETTINGS_FILE, settings)
        await ctx.reply(
            f"Added {role.mention} at the bottom of the rank ladder.",
            mention_author=False,
        )
    elif action == "remove":
        if role.id not in ranks:
            await ctx.reply("That role is not in the rank ladder.", mention_author=False)
            return
        ranks.remove(role.id)
        save_json(SETTINGS_FILE, settings)
        await ctx.reply(f"Removed {role.mention} from the rank ladder.", mention_author=False)
    else:
        await ctx.reply(
            "Choose `add`, `remove`, or `clear`. Example: `>setranks add @Trainee`",
            mention_author=False,
        )


@bot.command(name="promo", aliases=["promote"])
@commands.guild_only()
async def promote(ctx, member: discord.Member, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    if member.bot:
        await ctx.reply("Bots cannot be promoted by this command.", mention_author=False)
        return

    config = server_settings(ctx.guild.id)
    ranks = [r for r in config["ranks"] if ctx.guild.get_role(r)]

    if not ranks:
        await ctx.reply("No rank ladder is configured. Use `>setranks add @role`.", mention_author=False)
        return

    member_rank_indexes = [i for i, role_id in enumerate(ranks) if role_id in {r.id for r in member.roles}]

    if role:
        if role.id not in ranks:
            await ctx.reply("That role is not in the configured rank ladder.", mention_author=False)
            return
        target_index = ranks.index(role.id)
    else:
        current_index = max(member_rank_indexes, default=-1)
        target_index = current_index + 1
        if target_index >= len(ranks):
            await ctx.reply(f"{member.mention} is already at the highest configured rank.", mention_author=False)
            return

    target_role = ctx.guild.get_role(ranks[target_index])
    if target_role in member.roles:
        await ctx.reply(f"{member.mention} already has {target_role.mention}.", mention_author=False)
        return

    try:
        # Remove existing ladder roles before adding the new rank.
        remove_roles = [
            ctx.guild.get_role(role_id)
            for role_id in ranks
            if role_id in {r.id for r in member.roles}
            and ctx.guild.get_role(role_id) is not None
        ]
        if remove_roles:
            await member.remove_roles(*remove_roles, reason=f"Rank promotion by {ctx.author}")
        await member.add_roles(target_role, reason=f"Rank promotion by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(
            "I couldn't change that member's roles. Give me Manage Roles and move my bot role above the staff ranks.",
            mention_author=False,
        )
        return
    except discord.HTTPException as error:
        await ctx.reply(f"Discord couldn't update the roles: {error}", mention_author=False)
        return

    record_action(ctx.guild, ctx.author, member, "promoted", target_role)
    await ctx.reply(
        f"{emoji_tags.get('r2g_promote', '⬆️')} Promoted {member.mention} to {target_role.mention}.",
        mention_author=False,
    )
    await send_announcement(
        ctx.guild,
        f"{emoji_tags.get('r2g_promote', '⬆️')} {member.mention} was promoted to {target_role.mention}.",
    )
    await send_audit(
        ctx.guild,
        f"Promotion: {ctx.author.mention} promoted {member.mention} to {target_role.mention}.",
    )


@bot.command(name="demo", aliases=["demote"])
@commands.guild_only()
async def demote(ctx, member: discord.Member, role: discord.Role = None):
    if not await require_staff(ctx):
        return

    config = server_settings(ctx.guild.id)
    ranks = [r for r in config["ranks"] if ctx.guild.get_role(r)]
    member_rank_indexes = [i for i, role_id in enumerate(ranks) if role_id in {r.id for r in member.roles}]

    if not member_rank_indexes:
        await ctx.reply(f"{member.mention} does not have a configured staff rank.", mention_author=False)
        return

    current_index = max(member_rank_indexes)

    if role:
        if role.id not in ranks:
            await ctx.reply("That role is not in the configured rank ladder.", mention_author=False)
            return
        target_index = ranks.index(role.id)
        if target_index >= current_index:
            await ctx.reply("The selected rank is not below the member's current rank.", mention_author=False)
            return
    else:
        target_index = current_index - 1

    try:
        current_roles = [
            ctx.guild.get_role(role_id)
            for role_id in ranks
            if role_id in {r.id for r in member.roles}
            and ctx.guild.get_role(role_id) is not None
        ]
        if current_roles:
            await member.remove_roles(*current_roles, reason=f"Rank demotion by {ctx.author}")

        target_role = ctx.guild.get_role(ranks[target_index]) if target_index >= 0 else None
        if target_role:
            await member.add_roles(target_role, reason=f"Rank demotion by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(
            "I couldn't change that member's roles. Give me Manage Roles and move my bot role above the staff ranks.",
            mention_author=False,
        )
        return
    except discord.HTTPException as error:
        await ctx.reply(f"Discord couldn't update the roles: {error}", mention_author=False)
        return

    record_action(ctx.guild, ctx.author, member, "demoted", target_role)
    result = f"Demoted {member.mention} to {target_role.mention}." if target_role else f"Removed the lowest staff rank from {member.mention}."
    await ctx.reply(f"{emoji_tags.get('r2g_demote', '⬇️')} {result}", mention_author=False)
    await send_announcement(ctx.guild, f"{emoji_tags.get('r2g_demote', '⬇️')} {result}")
    await send_audit(ctx.guild, f"Demotion: {ctx.author.mention} — {result}")


@bot.command(name="setstaffsupervisor")
@commands.guild_only()
async def set_staff_supervisor(ctx, role: discord.Role):
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("Only a server administrator can set the supervisor role.", mention_author=False)
        return

    server_settings(ctx.guild.id)["supervisor_role"] = role.id
    save_json(SETTINGS_FILE, settings)
    await ctx.reply(f"Staff supervisor role set to {role.mention}.", mention_author=False)


@bot.command(name="setpromodemo")
@commands.guild_only()
async def set_promo_demo_channel(ctx, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("Only a server administrator can set this channel.", mention_author=False)
        return

    server_settings(ctx.guild.id)["announcement_channel"] = channel.id
    save_json(SETTINGS_FILE, settings)
    await ctx.reply(f"Promotion/demotion announcements will go to {channel.mention}.", mention_author=False)


@bot.command(name="setaudit")
@commands.guild_only()
async def set_audit_channel(ctx, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("Only a server administrator can set this channel.", mention_author=False)
        return

    server_settings(ctx.guild.id)["audit_channel"] = channel.id
    save_json(SETTINGS_FILE, settings)
    await ctx.reply(f"Audit messages will go to {channel.mention}.", mention_author=False)


@bot.command(name="staffsettings")
@commands.guild_only()
async def staff_settings(ctx):
    if not await require_staff(ctx):
        return

    config = server_settings(ctx.guild.id)
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
    embed.add_field(name="Rank ladder (lowest to highest)", value="\n".join(rank_names) or "Not configured", inline=False)
    embed.add_field(name="Supervisor role", value=supervisor.mention if supervisor else "Not configured", inline=True)
    embed.add_field(name="Announcement channel", value=announcement.mention if announcement else "Not configured", inline=True)
    embed.add_field(name="Audit channel", value=audit.mention if audit else "Not configured", inline=True)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="staffrank")
@commands.guild_only()
async def staff_rank(ctx, member: discord.Member):
    config = server_settings(ctx.guild.id)
    member_role_ids = {role.id for role in member.roles}
    assigned = [
        ctx.guild.get_role(role_id)
        for role_id in config["ranks"]
        if role_id in member_role_ids and ctx.guild.get_role(role_id)
    ]

    if assigned:
        await ctx.reply(f"{member.mention}'s configured rank: {assigned[-1].mention}", mention_author=False)
    else:
        await ctx.reply(f"{member.mention} has no configured staff rank.", mention_author=False)


@bot.command(name="stafflist")
@commands.guild_only()
async def staff_list(ctx):
    config = server_settings(ctx.guild.id)
    rank_ids = [role_id for role_id in config["ranks"] if ctx.guild.get_role(role_id)]
    if not rank_ids:
        await ctx.reply("No rank ladder is configured yet.", mention_author=False)
        return

    lines = []
    for role_id in rank_ids:
        role = ctx.guild.get_role(role_id)
        members = [member.mention for member in role.members]
        lines.append(f"**{role.name}** ({len(members)}): " + (", ".join(members[:20]) or "Nobody"))

    embed = discord.Embed(title="Staff List", description="\n".join(lines), color=discord.Color.purple())
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="staffhistory")
@commands.guild_only()
async def staff_history(ctx, member: discord.Member = None):
    if not await require_staff(ctx):
        return

    entries = [item for item in reversed(history) if item["guild_id"] == ctx.guild.id]
    if member:
        entries = [item for item in entries if item["target_id"] == member.id]

    entries = entries[:10]
    if not entries:
        await ctx.reply("No saved staff actions found.", mention_author=False)
        return

    lines = []
    for item in entries:
        target = ctx.guild.get_member(item["target_id"])
        actor = ctx.guild.get_member(item["actor_id"])
        target_text = target.mention if target else f"User {item['target_id']}"
        actor_text = actor.mention if actor else f"User {item['actor_id']}"
        role = ctx.guild.get_role(item["role_id"]) if item.get("role_id") else None
        role_text = f" → {role.mention}" if role else ""
        lines.append(f"`{item['action']}` {target_text}{role_text} — by {actor_text}")

    await ctx.reply(
        embed=discord.Embed(
            title="Recent Staff History",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        ),
        mention_author=False,
    )


@bot.command(name="emojis")
async def list_emojis(ctx):
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
            f"Missing information. Use `{PREFIX}help` to see examples.",
            mention_author=False,
        )
        return
    if isinstance(error, commands.BadArgument):
        await ctx.reply(
            "I couldn't find that member, role, or channel. Mention it in the command.",
            mention_author=False,
        )
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("This command only works inside a server.", mention_author=False)
        return

    print(f"Command error: {repr(error)}")
    await ctx.reply(
        "That command ran into an error. Check the bot logs and try again.",
        mention_author=False,
    )


if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable. Add your bot token in the host's Environment tab.")

bot.run(TOKEN)
