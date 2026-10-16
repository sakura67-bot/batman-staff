Import os
import json
import logging
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
    "promo": "<a:giveaway:1559999666068791359>",         # Custom Promotion Emoji
    "demo": "<:angry:1274605955035693057>",           # Custom Demotion Emoji
    "shield": "<:z_mod_DiscordShield:1560000554909040694>",       # Custom Supervisor / Staff Emoji
    "gear": "<:Settings:1560007503239188594>",           # Custom Settings Emoji
    "ladder": "<:16400redswordrankicon:1560003340492865639>",       # Custom Rank Ladder Emoji
    "announcement": "<a:1369announce:1560000473623437376>",# Custom Channel Announcement Emoji
    "audit": "<a:a7_book:1559999925822165105>",         # Custom Audit Log Emoji
    "success": "<a:MW_tick:1559999735329456220>",     # Custom Checkmark / Success Emoji
    "warning": "<a:03_awarndns:1560000005492703303>",     # Custom Warning Emoji
    "error": "<a:error:1560000088732999810>",         # Custom Denied / Error Emoji
    "user": "<:ntl_user:1556434847856328726>",           # Custom User Profile Emoji
    "bullet": "<:MW_arrow_right:1560001178937598175>"        # Custom Select Option Bullet Emoji
}

# Persistent settings
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

        role_id = guild_setting(
            "supervisor_roles", ctx.guild.id
        )

        if not role_id:
            return False

        return any(
            role.id == int(role_id)
            for role in ctx.author.roles
        )

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
        await channel.send(
            message,
            allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.HTTPException:
        logging.exception("Failed to send audit log")


# =========================
# PROMOTION / DEMOTION EMBEDS
# =========================

async def send_promodemo_embed(
    guild, member, role, action, moderator
):
    channel_id = guild_setting(
        "promodemo_channels", guild.id
    )

    if not channel_id:
        logging.info(
            "No promotion/demotion channel configured for %s",
            guild.id
        )
        return

    channel = guild.get_channel(int(channel_id))

    if channel is None:
        logging.warning("Promotion/demotion channel not found")
        return

    promoted = action == "promoted"

    embed = discord.Embed(
        title=(
            f"{EMOJIS['promo']} Staff Promotion"
            if promoted else f"{EMOJIS['demo']} Staff Demotion"
        ),
        description=(
            f"{member.mention} has been **promoted**!"
            if promoted else
            f"{member.mention} has been **demoted**."
        ),
        color=(
            discord.Color.green()
            if promoted else discord.Color.red()
        ),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name=f"{EMOJIS['user']} Staff Member",
        value=f"{member.mention}\n`{member.id}`",
        inline=False
    )

    embed.add_field(
        name=f"{EMOJIS['ladder']} Role",
        value=role.mention,
        inline=True
    )

    embed.add_field(
        name=f"{EMOJIS['shield']} Action By",
        value=moderator.mention,
        inline=True
    )

    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{guild.name} • Staff Management System")

    try:
        await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.HTTPException:
        logging.exception(
            "Failed to send promotion/demotion announcement"
        )


# =========================
# RANK LADDER DROPDOWN SETUP
# =========================

class RankSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        # Exclude @everyone and managed roles, limit to 25 safely
        roles = [r for r in guild.roles if not r.is_default() and not r.managed and r < guild.me.top_role][:25]
        
        options = []
        for role in roles:
            options.append(
                discord.SelectOption(
                    label=role.name[:100], 
                    value=str(role.id)
                )
            )

        if not options:
            options = [discord.SelectOption(label="No valid assignable roles", value="none")]

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
            description=f"**Ordered Ladder Hierarchy:**\n{roles_mention}\n\n*Now you can use `>promo @member` or `>demo @member` automatically!*",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)


class RankSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.add_item(RankSelect(guild))


# =========================
# STAFF ROLE CHANGES & AUTO-PROMO/DEMO
# =========================

async def change_staff_role(ctx, member, role, action):
    if member.id == ctx.author.id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot change your own staff role.", color=discord.Color.orange()))
        return

    if member.id == ctx.guild.owner_id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot modify the server owner's roles.", color=discord.Color.orange()))
        return

    if role.is_default() or role.managed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} That role cannot be assigned.", color=discord.Color.orange()))
        return

    bot_member = ctx.guild.me

    if bot_member is None or role >= bot_member.top_role:
        await ctx.reply(
            embed=discord.Embed(description=f"{EMOJIS['warning']} My highest role must be above the selected role.", color=discord.Color.orange())
        )
        return

    if ctx.author.id != OWNER_ID:
        if role >= ctx.author.top_role:
            await ctx.reply(
                embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot manage a role equal to or above yours.", color=discord.Color.orange())
            )
            return

        if member.top_role >= ctx.author.top_role:
            await ctx.reply(
                embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot manage a member equal to or above you.", color=discord.Color.orange())
            )
            return

    if action == "promote" and role in member.roles:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} That member already has this role.", color=discord.Color.orange()))
        return

    if action == "demote" and role not in member.roles:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} That member does not have this role.", color=discord.Color.orange()))
        return

    try:
        if action == "promote":
            await member.add_roles(
                role,
                reason=f"Staff promotion by {ctx.author}"
            )
            result = "promoted"

        else:
            await member.remove_roles(
                role,
                reason=f"Staff demotion by {ctx.author}"
            )
            result = "demoted"

        await send_promodemo_embed(
            ctx.guild,
            member,
            role,
            result,
            ctx.author
        )

        await audit(
            ctx.guild,
            f"Staff {result}: {member} ({member.id}) | "
            f"Role: {role.name} ({role.id}) | "
            f"By: {ctx.author} ({ctx.author.id})"
        )

        emoji = EMOJIS['promo'] if result == "promoted" else EMOJIS['demo']
        embed = discord.Embed(
            description=f"{emoji} {member.mention} was **{result}**: {role.mention}",
            color=discord.Color.green() if result == "promoted" else discord.Color.red()
        )
        await ctx.reply(embed=embed)

    except discord.Forbidden:
        await ctx.reply(
            embed=discord.Embed(description=f"{EMOJIS['error']} I lack permission to change that role. Check my Manage Roles permission and role hierarchy.", color=discord.Color.red())
        )

    except discord.HTTPException:
        logging.exception("Role change failed")
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Discord rejected the role change.", color=discord.Color.red()))


# =========================
# PROMO / DEMO COMMANDS
# =========================

@bot.command()
@commands.guild_only()
@is_supervisor()
async def promo(ctx, staff: discord.Member, role: discord.Role = None):
    """Promotes a staff member. If no role is given, uses the rank ladder."""
    if role is None:
        ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id))
        if not ladder:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No rank ladder has been set up yet! Use `>setranks` or specify a role manually (`>promo @member @role`).", color=discord.Color.orange()))
            return
        
        user_role_ids = [r.id for r in staff.roles]
        matched_index = -1
        
        for idx, r_id in enumerate(ladder):
            if r_id in user_role_ids:
                matched_index = idx
        
        next_index = matched_index + 1
        if next_index >= len(ladder):
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {staff.mention} is already at the highest rank on the ladder!", color=discord.Color.orange()))
            return
            
        role = ctx.guild.get_role(ladder[next_index])
        if role is None:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} The configured next rank role no longer exists in this server.", color=discord.Color.red()))
            return

    await change_staff_role(ctx, staff, role, "promote")


@bot.command()
@commands.guild_only()
@is_supervisor()
async def demo(ctx, staff: discord.Member, role: discord.Role = None):
    """Demotes a staff member. If no role is given, auto-demotes down the rank ladder."""
    if role is None:
        ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id))
        if not ladder:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No rank ladder has been set up yet! Use `>setranks` or specify a role manually (`>demo @member @role`).", color=discord.Color.orange()))
            return
        
        user_role_ids = [r.id for r in staff.roles]
        matched_index = -1
        
        for idx, r_id in enumerate(ladder):
            if r_id in user_role_ids:
                matched_index = idx
        
        prev_index = matched_index - 1
        if prev_index < 0:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {staff.mention} has no lower rank on the ladder to demote to!", color=discord.Color.orange()))
            return
            
        role = ctx.guild.get_role(ladder[prev_index])
        if role is None:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} The configured lower rank role no longer exists in this server.", color=discord.Color.red()))
            return

    await change_staff_role(ctx, staff, role, "demote")


# =========================
# NEW COMMANDS: >super, >staff, >role, >steal
# =========================

@bot.command()
@commands.guild_only()
@is_owner()
async def super(ctx, member: discord.Member):
    """Automatically assigns the server's configured Staff Supervisor role to a member."""
    role_id = guild_setting("supervisor_roles", ctx.guild.id)
    if not role_id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No supervisor role has been configured yet! Use `>setstaffsupervisor @role` first.", color=discord.Color.orange()))
        return

    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} The configured supervisor role no longer exists.", color=discord.Color.red()))
        return

    if role in member.roles:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {member.mention} already has the supervisor role.", color=discord.Color.orange()))
        return

    try:
        await member.add_roles(role, reason=f"Assigned supervisor role by {ctx.author}")
        
        embed = discord.Embed(
            title=f"{EMOJIS['shield']} Supervisor Assigned",
            description=f"{member.mention} has been given the supervisor role: {role.mention}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)
        
        await audit(ctx.guild, f"Supervisor role given to {member} ({member.id}) by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permission to assign that role.", color=discord.Color.red()))


@bot.command()
@commands.guild_only()
@is_owner()
async def staff(ctx, member: discord.Member):
    """Automatically finds and assigns 'Junior Moderator' and 'Staff Team' roles to a user."""
    jr_mod_role = discord.utils.find(lambda r: "junior moderator" in r.name.lower(), ctx.guild.roles)
    staff_team_role = discord.utils.find(lambda r: "staff team" in r.name.lower(), ctx.guild.roles)

    missing_roles = []
    roles_to_add = []

    if jr_mod_role:
        roles_to_add.append(jr_mod_role)
    else:
        missing_roles.append("Junior Moderator")

    if staff_team_role:
        roles_to_add.append(staff_team_role)
    else:
        missing_roles.append("Staff Team")

    if not roles_to_add:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Neither 'Junior Moderator' nor 'Staff Team' roles were found in this server.", color=discord.Color.red()))
        return

    try:
        await member.add_roles(*roles_to_add, reason=f"Automated staff roles assigned by {ctx.author}")
        
        assigned_mentions = ", ".join([r.mention for r in roles_to_add])
        embed = discord.Embed(
            title=f"{EMOJIS['success']} Staff Team Roles Assigned",
            description=f"Successfully assigned {assigned_mentions} to {member.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        if missing_roles:
            embed.add_field(name=f"{EMOJIS['warning']} Missing Roles", value=f"Could not find: {', '.join(missing_roles)}", inline=False)
            
        embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)
        
        await audit(ctx.guild, f"Staff roles ({assigned_mentions}) given to {member} ({member.id}) by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permissions to assign one or more of those roles.", color=discord.Color.red()))


@bot.command()
@commands.guild_only()
@is_supervisor()
async def role(ctx, member: discord.Member, role: discord.Role):
    """Quickly adds or removes a role from a member."""
    if role.is_default() or role.managed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot manage that role.", color=discord.Color.orange()))
        return

    try:
        if role in member.roles:
            await member.remove_roles(role, reason=f"Role removed by {ctx.author}")
            embed = discord.Embed(
                description=f"{EMOJIS['demo']} Removed {role.mention} from {member.mention}.",
                color=discord.Color.red()
            )
        else:
            await member.add_roles(role, reason=f"Role added by {ctx.author}")
            embed = discord.Embed(
                description=f"{EMOJIS['promo']} Added {role.mention} to {member.mention}.",
                color=discord.Color.green()
            )
        await ctx.reply(embed=embed)
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permissions to manage this role.", color=discord.Color.red()))


@bot.command()
@commands.guild_only()
@is_owner()
async def steal(ctx, emoji: str, name: str = None):
    """Steals an emoji and adds it to the current server (`>steal :emoji: [name]`)."""
    import re
    custom_emoji_match = re.match(r"<(a?):([a-zA-Z0-9_]+):([0-9]+)>", emoji)
    
    if not custom_emoji_match:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Please provide a valid custom emoji from another server.", color=discord.Color.red()))
        return

    animated, emoji_name, emoji_id = custom_emoji_match.groups()
    if not name:
        name = emoji_name

    extension = "gif" if animated == "a" else "png"
    url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?v=1"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Could not fetch emoji image.", color=discord.Color.red()))
                    return
                image_bytes = await resp.read()

        new_emoji = await ctx.guild.create_custom_emoji(name=name, image=image_bytes, reason=f"Stolen by {ctx.author}")
        embed = discord.Embed(
            title=f"{EMOJIS['success']} Emoji Added!",
            description=f"Successfully added {new_emoji} (`:{new_emoji.name}:`) to the server.",
            color=discord.Color.green()
        )
        await ctx.reply(embed=embed)
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack 'Manage Emojis and Stickers' permissions.", color=discord.Color.red()))
    except discord.HTTPException as e:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Failed to upload emoji: {e}", color=discord.Color.red()))


# =========================
# SETUP COMMANDS
# =========================

@bot.command()
@commands.guild_only()
@is_owner()
async def setranks(ctx):
    """Opens an interactive dropdown menu to configure the staff rank ladder sequence."""
    view = RankSelectView(ctx.guild)
    embed = discord.Embed(
        title=f"{EMOJIS['ladder']} Staff Rank Ladder Setup",
        description="Select the staff roles below **in order from lowest to highest rank** using the dropdown menu.\n\n*This allows automatic promotion/demotion using `>promo @member` and `>demo @member`.*",
        color=discord.Color.blurple()
    )
    await ctx.reply(embed=embed, view=view)


@bot.command()
@commands.guild_only()
@is_owner()
async def setstaffsupervisor(ctx, role: discord.Role):
    if role.is_default() or role.managed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Choose a normal server role.", color=discord.Color.orange()))
        return

    config["supervisor_roles"][str(ctx.guild.id)] = role.id
    save_config()

    embed = discord.Embed(
        title=f"{EMOJIS['shield']} Staff Supervisor Configured",
        description=(
            f"Authorized staff supervisors: {role.mention}\n\n"
            "Members with this role can use `>promo` and `>demo`."
        ),
        color=discord.Color.blue()
    )

    await ctx.reply(embed=embed)
    await audit(ctx.guild, f"Supervisor role set to {role.name} by {ctx.author}")


@bot.command()
@commands.guild_only()
@is_owner()
async def setpromodemo(ctx, channel: discord.TextChannel):
    config["promodemo_channels"][str(ctx.guild.id)] = channel.id
    save_config()

    embed = discord.Embed(
        title=f"{EMOJIS['announcement']} Promotion & Demotion Channel Set",
        description=(
            f"Channel: {channel.mention}\n\n"
            "Promotion and demotion announcements will appear here."
        ),
        color=discord.Color.green()
    )

    await ctx.reply(embed=embed)
    await audit(ctx.guild, f"Promotion/demotion channel set to #{channel.name} by {ctx.author}")


@bot.command()
@commands.guild_only()
@is_owner()
async def setaudit(ctx, channel: discord.TextChannel):
    config["audit_channels"][str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Audit log channel set to {channel.mention}", color=discord.Color.green()))


# =========================
# SHOW CURRENT SETTINGS
# =========================

@bot.command()
@commands.guild_only()
@is_owner()
async def staffsettings(ctx):
    supervisor_id = guild_setting("supervisor_roles", ctx.guild.id)
    promo_id = guild_setting("promodemo_channels", ctx.guild.id)
    audit_id = guild_setting("audit_channels", ctx.guild.id)
    ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id), [])

    supervisor = ctx.guild.get_role(int(supervisor_id)) if supervisor_id else None
    promo_channel = ctx.guild.get_channel(int(promo_id)) if promo_id else None
    audit_channel = ctx.guild.get_channel(int(audit_id)) if audit_id else None
    
    ladder_str = " -> ".join([f"<@&{r_id}>" for r_id in ladder]) if ladder else "Not configured"

    embed = discord.Embed(
        title=f"{EMOJIS['gear']} Staff Bot Settings",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name=f"{EMOJIS['shield']} Supervisor Role",
        value=supervisor.mention if supervisor else "Not configured",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['announcement']} Promo/Demo Channel",
        value=promo_channel.mention if promo_channel else "Not configured",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['audit']} Audit Channel",
        value=audit_channel.mention if audit_channel else "Not configured",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['ladder']} Rank Ladder Sequence",
        value=ladder_str,
        inline=False
    )

    await ctx.reply(embed=embed)


# =========================
# HELP COMMAND
# =========================

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title=f"{EMOJIS['gear']} Staff Management Bot - Help Center",
        description="Here is a complete list of commands:",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name=f"{EMOJIS['shield']} Staff Supervisor Commands",
        value=(
            f"`>promo @member [@role]` — Promote a staff member (Auto-promotes if role omitted)\n"
            f"`>demo @member [@role]` — Demote a staff member (Auto-demotes if role omitted)\n"
            f"`>role @member @role` — Quickly add or remove a role"
        ),
        inline=False
    )

    embed.add_field(
        name=f"{EMOJIS['gear']} Bot Owner Setup Commands",
        value=(
            f"`>super @member` — Automatically give a user the supervisor role\n"
            f"`>staff @member` — Automatically give a user Junior Moderator and Staff Team roles\n"
            f"`>steal :emoji: [name]` — Steal and add a custom emoji to this server\n"
            f"`>setranks` — Open interactive menu to configure the rank ladder\n"
            f"`>setstaffsupervisor @role` — Set the supervisor role\n"
            f"`>setpromodemo #channel` — Set the announcement channel\n"
            f"`>setaudit #channel` — Set the audit log channel\n"
            f"`>staffsettings` — View the server configuration settings"
        ),
        inline=False
    )

    embed.set_footer(
        text=f"Requested by {ctx.author.display_name}",
        icon_url=ctx.author.display_avatar.url
    )

    await ctx.reply(embed=embed, mention_author=False)


# =========================
# EVENTS
# =========================

@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user, bot.user.id)
    logging.info("Connected to %s server(s)", len(bot.guilds))


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CheckFailure):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))

    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Missing argument. Use `>help` to see command syntax.", color=discord.Color.orange()))

    elif isinstance(error, commands.MemberNotFound):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Member not found. Mention a valid member.", color=discord.Color.red()))

    elif isinstance(error, commands.RoleNotFound):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Role not found. Mention a valid role.", color=discord.Color.red()))

    elif isinstance(error, commands.ChannelNotFound):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Channel not found. Mention a valid text channel.", color=discord.Color.red()))

    elif isinstance(error, commands.BadArgument):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Invalid argument. Check your mentions or types.", color=discord.Color.orange()))

    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} This command can only be used in a server.", color=discord.Color.orange()))

    else:
        logging.exception("Unhandled command error", exc_info=error)
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} An unexpected error occurred.", color=discord.Color.red()))


# =========================
# START BOT
# =========================

load_config()

if not TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable.")

if OWNER_ID == 0:
    raise RuntimeError("Missing BOT_OWNER_ID environment variable.")

bot.run(TOKEN)
