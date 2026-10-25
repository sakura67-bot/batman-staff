import os
import json
import logging
import random
import sys
import asyncio
import re
import html
import secrets
import time
from urllib.parse import urlencode
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# =========================
# CONFIGURATION
# =========================

logging.basicConfig(level=logging.INFO)
THEME_BLUE = discord.Color.from_rgb(38, 105, 210)
THEME_GOLD = discord.Color.from_rgb(220, 176, 55)

TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")
if not OAUTH_REDIRECT_URI and PUBLIC_BASE_URL:
    OAUTH_REDIRECT_URI = f"{PUBLIC_BASE_URL}/oauth/callback"

CONFIG_FILE = "config.json"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class EmojiContext(commands.Context):
    """Rewrite configured emoji markup to this server's uploaded copies."""

    async def send(self, content=None, *, embed=None, embeds=None, **kwargs):
        content = render_server_emojis(self.guild, content)

        def rewrite(value):
            if isinstance(value, str):
                return render_server_emojis(self.guild, value)
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, dict):
                return {key: rewrite(item) for key, item in value.items()}
            return value

        if embed is not None:
            embed = discord.Embed.from_dict(rewrite(embed.to_dict()))
        if embeds is not None:
            embeds = [discord.Embed.from_dict(rewrite(item.to_dict())) for item in embeds]
        return await super().send(content=content, embed=embed, embeds=embeds, **kwargs)


bot = commands.Bot(
    command_prefix=">",
    intents=intents,
    activity=discord.Game(name="The Seven Deadly Seas • Made by Afnan the GOAT"),
    context_class=EmojiContext,
    help_command=None
)

# =========================
# CUSTOM EMOJI CONFIGURATION
# =========================

EMOJIS = {
    "promo": "<a:giveaway:1563258803770753204>",
    "demo": "<:angry:1563252045899898950>",
    "shield": "<:z_mod_DiscordShield:1563259179328868413>",
    "gear": "<:Settings:1563252049544740995>",
    "ladder": "<:16400redswordrankicon:1563259184038809713>",
    "announcement": "<:announcement:1563259185490034802>",
    "audit": "<a:a7_book:1563259186962243647>",
    "success": "<a:MW_tick:1563259189135016098>",
    "warning": "<a:03_awarndns:1563259190548504608>",
    "error": "<a:error:1563259192134074370>",
    "user": "<:cristiano:1540670348851150958>",
    "bullet": "<:MW_arrow_right:1563259195397246976>",
    "welcome": "<a:wel:1563259197603188776>",
    "ticket": "<:ticket:1563259199474106393>",
    "support": "<:Support:1563259201365745746>",
    "purchase": "<a:decomp:1563252072978055259>",
    "partner": "<:developer:1540055696601784320>"
}
SOURCE_EMOJIS = EMOJIS.copy()

EMOJI_FALLBACKS = {
    "promo": "🎉", "demo": "⚠️", "shield": "🛡️", "gear": "⚙️",
    "ladder": "🏆", "announcement": "📢", "audit": "📋", "success": "✅",
    "warning": "⚠️", "error": "❌", "user": "👤", "bullet": "➡️",
    "welcome": "👋", "ticket": "🎫", "support": "🆘", "purchase": "💰",
    "partner": "🤝"
}

config = {
    "supervisor_roles": {},
    "staff_roles": {},
    "ticket_categories": {},
    "promodemo_channels": {},
    "audit_channels": {},
    "rank_ladders": {},
    "staff_limits": {},
    "warnings": {},
    "trainings": {},
    "economy": {},
    "log_channels": {},
    "log_webhooks": {},
    "economy_channels": {},
    "modmail_channels": {},
    "modmail_threads": {},
    "disabled_commands": {},
    "extra_owners": {},
    "maintenance": {"enabled": False},
    "uploaded_emojis": {},
    "application_emojis": {},
    "verified_members": {},
    "backup_guild": {}
}

LOG_WEBHOOKS = {}
APPLICATION_EMOJIS_SYNCED = False
OAUTH_STATES = {}
OAUTH_WEB_RUNNER = None


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
        logging.exception("Could not load config file")


def save_config():
    temporary_file = CONFIG_FILE + ".tmp"
    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=4)
        os.replace(temporary_file, CONFIG_FILE)
    except OSError:
        logging.exception("Could not save configuration")


def apply_application_emoji_map():
    """Use saved application-owned emoji markup throughout all bot responses."""
    for key, markup in config.get("application_emojis", {}).items():
        if key in SOURCE_EMOJIS and isinstance(markup, str):
            EMOJIS[key] = markup


def guild_setting(setting, guild_id):
    return config[setting].get(str(guild_id))


# =========================
# PERMISSION & LIMIT HELPERS
# =========================

def user_is_owner(ctx):
    return ctx.author.id == OWNER_ID or ctx.author.id == ctx.guild.owner_id


def is_bot_owner(ctx):
    if not OWNER_ID:
        return False
    return ctx.author.id == OWNER_ID or bool(
        config.get("extra_owners", {}).get(str(ctx.author.id))
    )


def is_primary_bot_owner(ctx):
    return bool(OWNER_ID) and ctx.author.id == OWNER_ID


def guild_emoji(guild, key):
    """Prefer the bot application's emoji, then a server copy, then source markup."""
    app_markup = config.get("application_emojis", {}).get(key)
    if app_markup:
        return app_markup
    if guild is not None:
        uploaded_id = config.get("uploaded_emojis", {}).get(str(guild.id), {}).get(key)
        if uploaded_id:
            emoji = discord.utils.get(guild.emojis, id=int(uploaded_id))
            if emoji:
                return str(emoji)
    return EMOJIS.get(key, "")


def render_server_emojis(guild, text):
    """Prefer application-owned emoji markup, then server copies or Unicode."""
    if not isinstance(text, str):
        return text
    for key, source_markup in SOURCE_EMOJIS.items():
        app_markup = config.get("application_emojis", {}).get(key)
        if app_markup:
            text = text.replace(source_markup, app_markup)
    if guild is None:
        return text
    uploaded = config.get("uploaded_emojis", {}).get(str(guild.id), {})
    for key, source_markup in SOURCE_EMOJIS.items():
        if config.get("application_emojis", {}).get(key):
            continue
        uploaded_id = uploaded.get(key)
        emoji = discord.utils.get(guild.emojis, id=int(uploaded_id)) if uploaded_id else None
        if emoji is None:
            match = re.fullmatch(r"<a?:([A-Za-z0-9_]+):\d+>", source_markup)
            if match:
                emoji = discord.utils.get(guild.emojis, name=match.group(1))
        replacement = str(emoji) if emoji else EMOJI_FALLBACKS.get(key, "✨")
        text = text.replace(source_markup, replacement)
    return text


def user_is_supervisor(ctx):
    if user_is_owner(ctx):
        return True
    role_id = guild_setting("supervisor_roles", ctx.guild.id)
    if not role_id:
        return False
    return any(role.id == int(role_id) for role in ctx.author.roles)


def user_is_staff(ctx):
    if user_is_supervisor(ctx) or ctx.author.guild_permissions.kick_members:
        return True
    staff_role_id = guild_setting("staff_roles", ctx.guild.id)
    if staff_role_id and any(r.id == int(staff_role_id) for r in ctx.author.roles):
        return True
    return any("staff" in r.name.lower() or "trial" in r.name.lower() or "mod" in r.name.lower() for r in ctx.author.roles)


def check_and_increment_action_limit(ctx, action_type, limit=3):
    if user_is_supervisor(ctx):
        return True, 0

    import time
    now = time.time()
    user_id = str(ctx.author.id)

    if "staff_limits" not in config:
        config["staff_limits"] = {}

    if user_id not in config["staff_limits"]:
        config["staff_limits"][user_id] = {"kicks": [], "bans": []}

    timestamps = [t for t in config["staff_limits"][user_id].get(action_type, []) if now - t < 86400]

    if len(timestamps) >= limit:
        return False, len(timestamps)

    timestamps.append(now)
    config["staff_limits"][user_id][action_type] = timestamps
    save_config()
    return True, len(timestamps)


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


async def log_server_event(guild, title, description, color=THEME_BLUE):
    """Send a server event as an embed to its configured logging channel."""
    if guild is None:
        return
    channel_id = guild_setting("log_channels", guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return
    embed = discord.Embed(
        title=f"{EMOJIS['audit']} {title}",
        description=(description or "No additional details.")[:4000],
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{guild.name} • Server Event Log")
    try:
        webhook_id = config.setdefault("log_webhooks", {}).get(str(guild.id))
        webhook = LOG_WEBHOOKS.get(guild.id)
        if webhook is None:
            webhooks = await channel.webhooks()
            webhook = discord.utils.get(webhooks, id=int(webhook_id)) if webhook_id else None
            if webhook is None:
                webhook = discord.utils.find(lambda item: item.name == "The Seven Deadly Seas Helper Event Logs", webhooks)
            if webhook is None:
                webhook = await channel.create_webhook(name="The Seven Deadly Seas Helper Event Logs", reason="Server event embed logging")
            config["log_webhooks"][str(guild.id)] = webhook.id
            save_config()
            LOG_WEBHOOKS[guild.id] = webhook
        await webhook.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logging.exception("Failed to send server event log through webhook")


async def upload_emoji_pack(guild):
    """Copy the configured custom emoji pack into a newly joined server."""
    member = guild.me
    if member is None:
        logging.warning("The Seven Deadly Seas Helper could not upload emojis to %s: bot member is unavailable", guild.id)
        return

    permissions = member.guild_permissions
    can_manage_expressions = (
        getattr(permissions, "manage_expressions", False)
        or getattr(permissions, "manage_emojis_and_stickers", False)
    )
    if not can_manage_expressions:
        logging.info("The Seven Deadly Seas Helper joined %s without Manage Expressions; skipped emoji upload", guild.id)
        return

    import re
    sources = []
    for key, markup in SOURCE_EMOJIS.items():
        match = re.fullmatch(r"<(a?):([A-Za-z0-9_]+):(\d+)>", markup)
        if match:
            animated, name, emoji_id = match.groups()
            sources.append((key, name[:32], int(emoji_id), animated == "a"))

    existing_by_name = {emoji.name: emoji for emoji in guild.emojis}
    guild_uploads = config.setdefault("uploaded_emojis", {}).setdefault(str(guild.id), {})
    timeout = aiohttp.ClientTimeout(total=20)
    uploaded_count = 0
    mapping_changed = False

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for key, name, emoji_id, animated in sources:
            if name in existing_by_name:
                existing_emoji_id = existing_by_name[name].id
                if guild_uploads.get(key) != existing_emoji_id:
                    guild_uploads[key] = existing_emoji_id
                    mapping_changed = True
                continue
            extension = "gif" if animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=128"
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        logging.info("Skipping emoji %s: source returned HTTP %s", name, response.status)
                        continue
                    if response.content_length and response.content_length >= 256 * 1024:
                        logging.info("Skipping emoji %s: source is over the upload size limit", name)
                        continue
                    image_bytes = await response.read()
                if len(image_bytes) >= 256 * 1024:
                    logging.info("Skipping emoji %s: source is over the upload size limit", name)
                    continue

                emoji = await guild.create_custom_emoji(
                    name=name,
                    image=image_bytes,
                    reason="The Seven Deadly Seas Helper automatic emoji pack"
                )
                existing_by_name[name] = emoji
                guild_uploads[key] = emoji.id
                uploaded_count += 1
                mapping_changed = True
            except discord.Forbidden:
                logging.info("The Seven Deadly Seas Helper lacks permission to upload emoji %s to %s", name, guild.id)
                break
            except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                logging.exception("The Seven Deadly Seas Helper could not upload emoji %s to %s", name, guild.id)

    if mapping_changed:
        save_config()
    logging.info("The Seven Deadly Seas Helper uploaded %s custom emojis to guild %s", uploaded_count, guild.id)


async def upload_application_emoji_pack():
    """Upload the configured emoji pack once to the bot application, not servers."""
    if not TOKEN or not bot.user:
        raise RuntimeError("The bot must be logged in and BOT_TOKEN must be configured.")

    sources = []
    for key, markup in SOURCE_EMOJIS.items():
        match = re.fullmatch(r"<(?P<animated>a?):(?P<name>[A-Za-z0-9_]+):(?P<id>\d+)>", markup)
        if match:
            sources.append((key, match.group("name")[:32], int(match.group("id")), match.group("animated") == "a"))

    application_id = getattr(bot, "application_id", None) or bot.user.id
    base_url = f"https://discord.com/api/v10/applications/{application_id}/emojis"
    headers = {"Authorization": f"Bot {TOKEN}"}
    timeout = aiohttp.ClientTimeout(total=30)
    uploaded = 0
    skipped = []

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(base_url) as response:
            if response.status != 200:
                detail = (await response.text())[:500]
                raise RuntimeError(f"Could not list the bot application's emojis (HTTP {response.status}): {detail}")
            existing_data = await response.json()
        existing_by_name = {item["name"]: item for item in existing_data.get("items", [])}

        for key, name, source_id, animated in sources:
            item = existing_by_name.get(name)
            if item is None:
                extension = "gif" if animated else "png"
                url = f"https://cdn.discordapp.com/emojis/{source_id}.{extension}?size=128"
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            skipped.append(f"{name} (source HTTP {response.status})")
                            continue
                        if response.content_length and response.content_length >= 256 * 1024:
                            skipped.append(f"{name} (over 256 KiB)")
                            continue
                        image_bytes = await response.read()
                    if len(image_bytes) >= 256 * 1024:
                        skipped.append(f"{name} (over 256 KiB)")
                        continue

                    import base64
                    mime = "image/gif" if animated else "image/png"
                    payload = {"name": name, "image": f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"}
                    async with session.post(base_url, json=payload) as response:
                        if response.status not in (200, 201):
                            detail = (await response.text())[:300]
                            skipped.append(f"{name} (upload HTTP {response.status}: {detail})")
                            continue
                        item = await response.json()
                    existing_by_name[name] = item
                    uploaded += 1
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    skipped.append(f"{name} ({type(exc).__name__})")
                    continue

            emoji_id = item.get("id")
            if emoji_id:
                prefix = "a" if item.get("animated", animated) else ""
                config.setdefault("application_emojis", {})[key] = f"<{prefix}:{name}:{emoji_id}>"

    apply_application_emoji_map()
    save_config()
    return uploaded, skipped, len(config.get("application_emojis", {}))


def oauth_is_configured():
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and OAUTH_REDIRECT_URI)


def oauth_page(title, message, status=200):
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{safe_title}</title></head>"
        "<body style='margin:0;background:#10131b;color:#eef2ff;font:16px sans-serif;"
        "display:grid;min-height:100vh;place-items:center'>"
        "<main style='max-width:560px;padding:28px;background:#1b2130;border-radius:16px'>"
        f"<h1>{safe_title}</h1><p>{safe_message}</p>"
        "</main></body></html>"
    )
    return web.Response(text=page, status=status, content_type="text/html")


def new_oauth_state(mode, source_guild_id, target_guild_id=None):
    expired = [key for key, value in OAUTH_STATES.items() if value["expires"] < time.time()]
    for key in expired:
        OAUTH_STATES.pop(key, None)
    state = secrets.token_urlsafe(32)
    OAUTH_STATES[state] = {
        "mode": mode,
        "source_guild_id": str(source_guild_id),
        "target_guild_id": str(target_guild_id) if target_guild_id else None,
        "expires": time.time() + 600,
    }
    return state


def make_discord_oauth_url(state, scopes):
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": " ".join(scopes),
        "state": state,
    }
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


async def oauth_verify_start(request):
    if not oauth_is_configured():
        return oauth_page("Verification unavailable", "The bot owner has not finished configuring OAuth.", 503)
    try:
        source_id = int(request.query.get("guild_id", ""))
    except ValueError:
        return oauth_page("Invalid verification link", "This link is missing a valid server ID.", 400)
    if bot.get_guild(source_id) is None:
        return oauth_page("Verification unavailable", "The bot is not in the verification server.", 404)
    backup_id = config.get("backup_guild", {}).get("id")
    backup = bot.get_guild(int(backup_id)) if backup_id else None
    if backup is None:
        return oauth_page("Verification unavailable", "The bot owner has not configured a backup server yet.", 503)
    state = new_oauth_state("verify", source_id, backup.id)
    authorization_url = html.escape(make_discord_oauth_url(state, ["identify", "guilds.join"]), quote=True)
    target_name = html.escape(backup.name)
    body = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Verify account</title></head><body style='margin:0;background:#10131b;color:#eef2ff;font:16px sans-serif;display:grid;min-height:100vh;place-items:center'>"
        "<main style='max-width:560px;padding:28px;background:#1b2130;border-radius:16px'>"
        f"<h1>Verify for {html.escape(source.name)}</h1><p>If you continue, Discord will ask you to authorize identity verification and allow this bot to add your account to <b>{target_name}</b> later when the bot owner runs the member transfer command. "
        "This is optional. Your refresh token is stored encrypted so the bot can complete that later request.</p>"
        f"<a href='{authorization_url}' style='display:inline-block;padding:12px 18px;background:#356bd6;color:white;border-radius:8px;text-decoration:none'>Authorize with Discord</a>"
        "</main></body></html>"
    )
    return web.Response(text=body, content_type="text/html")


def encrypt_oauth_secret(value):
    key = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("OAUTH_TOKEN_ENCRYPTION_KEY is not configured")
    from cryptography.fernet import Fernet
    return Fernet(key.encode("utf-8")).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_oauth_secret(value):
    key = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("OAUTH_TOKEN_ENCRYPTION_KEY is not configured")
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(key.encode("utf-8")).decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Saved OAuth token cannot be decrypted; check the encryption key.") from exc


async def oauth_callback(request):
    code = request.query.get("code")
    state = request.query.get("state")
    flow = OAUTH_STATES.pop(state, None) if state else None
    if not code or not flow or flow["expires"] < time.time():
        return oauth_page("Authorization expired", "Please return to Discord and start again.", 400)
    if not oauth_is_configured():
        return oauth_page("Verification unavailable", "OAuth is not configured correctly.", 503)

    source_id = int(flow["source_guild_id"])
    source_guild = bot.get_guild(source_id)
    if source_guild is None:
        return oauth_page("Verification unavailable", "The bot is no longer in the verification server.", 404)

    timeout = aiohttp.ClientTimeout(total=20)
    access_token = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        token_data = {
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OAUTH_REDIRECT_URI,
        }
        async with session.post("https://discord.com/api/v10/oauth2/token", data=token_data) as response:
            if response.status != 200:
                logging.warning("Discord OAuth code exchange failed with HTTP %s", response.status)
                return oauth_page("Authorization failed", "Discord could not complete authorization. Please try again.", 400)
            token = await response.json()
            access_token = token.get("access_token")

        if not access_token:
            return oauth_page("Authorization failed", "Discord did not return an access token.", 400)

        async with session.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as response:
            if response.status != 200:
                return oauth_page("Authorization failed", "Could not verify your Discord account. Please retry.", 400)
            user = await response.json()

        user_id = str(user["id"])
        try:
            await source_guild.fetch_member(int(user_id))
        except discord.NotFound:
            return oauth_page("Not a server member", "Join the verification server first, then verify again.", 403)
        except (discord.Forbidden, discord.HTTPException):
            logging.exception("Could not confirm verification-server membership")
            return oauth_page("Verification unavailable", "The bot cannot check server membership right now.", 503)

        if flow["mode"] != "verify":
            return oauth_page("Invalid authorization", "This authorization flow is no longer supported. Restart verification.", 400)
        if "guilds.join" not in token.get("scope", "").split():
            return oauth_page("Permission missing", "Approve the requested guilds.join permission, then start verification again.", 403)
        backup_id = config.get("backup_guild", {}).get("id")
        consent_backup_id = flow.get("target_guild_id")
        if not backup_id or str(backup_id) != str(consent_backup_id) or bot.get_guild(int(backup_id)) is None:
            return oauth_page("Verification expired", "The configured backup server changed. Restart verification so the destination is clear.", 410)
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return oauth_page("Authorization incomplete", "Discord did not return the permission needed for later server joining. Please retry and approve all requested access.", 400)
        try:
            encrypted_refresh_token = encrypt_oauth_secret(refresh_token)
        except (RuntimeError, ImportError, ValueError) as exc:
            logging.error("Could not securely store OAuth authorization: %s", exc)
            return oauth_page("Verification unavailable", "The bot owner has not configured secure OAuth token storage.", 503)

        config.setdefault("verified_members", {}).setdefault(str(source_id), {})[user_id] = {
            "verified_at": int(time.time()),
            "backup_guild_id": str(backup_id),
            "refresh_token": encrypted_refresh_token,
        }
        save_config()
        backup = bot.get_guild(int(backup_id))
        return oauth_page("Verified", f"You are verified in {source_guild.name}. You authorized the bot to add your account to {backup.name} when the owner runs >pullmembers. You can revoke this authorization in Discord's Authorized Apps settings.")


async def start_oauth_webserver():
    global OAUTH_WEB_RUNNER
    if OAUTH_WEB_RUNNER is not None:
        return
    if not oauth_is_configured():
        logging.warning("OAuth web routes are disabled; configure DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, PUBLIC_BASE_URL, and the matching Discord redirect URI.")
        return
    port = int(os.getenv("SERVER_PORT", "0"))
    if not port:
        logging.warning("OAuth web routes are disabled because SERVER_PORT is not set by the host.")
        return
    async def health(request):
        return oauth_page("SLS Helper", "OAuth verification service is online.")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/verify", oauth_verify_start)
    app.router.add_get("/oauth/callback", oauth_callback)
    OAUTH_WEB_RUNNER = web.AppRunner(app)
    await OAUTH_WEB_RUNNER.setup()
    site = web.TCPSite(OAUTH_WEB_RUNNER, "0.0.0.0", port)
    await site.start()
    logging.info("OAuth web server started on host port %s", port)


def oauth_link_view(label, url):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.link, url=url))
    return view


@bot.command(name="verify")
async def verify_command(ctx):
    if ctx.guild is None:
        await ctx.reply("Run `>verify` in the server you want to verify for.", mention_author=False)
        return
    if not oauth_is_configured() or not PUBLIC_BASE_URL:
        await ctx.reply("⚠️ Verification is not configured yet. The bot owner needs to set up its OAuth callback on Bot-Hosting.net.", mention_author=False)
        return
    if not os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY"):
        await ctx.reply("⚠️ Secure OAuth storage is not configured yet. The bot owner needs to set `OAUTH_TOKEN_ENCRYPTION_KEY` on Bot-Hosting.net.", mention_author=False)
        return
    backup_id = config.get("backup_guild", {}).get("id")
    backup = bot.get_guild(int(backup_id)) if backup_id else None
    if backup is None:
        await ctx.reply("⚠️ The bot owner must configure the backup server before members verify, so the authorization destination is clear.", mention_author=False)
        return
    url = f"{PUBLIC_BASE_URL}/verify?{urlencode({'guild_id': ctx.guild.id})}"
    embed = discord.Embed(
        title=f"{EMOJIS['shield']} Verify your Discord account",
        description=(
            f"Press **Verify with Discord** to confirm your account and membership here. Discord will also ask permission for the bot to add you to **{backup.name}** later when the owner runs `>pullmembers`. "
            "This is optional. The bot stores your refresh token encrypted so the owner can use `>pullmembers` later without asking you to authorize a second time."
        ),
        color=THEME_BLUE,
    )
    await ctx.reply(embed=embed, view=oauth_link_view("Verify with Discord", url), mention_author=False)


@bot.command(name="setbackupserver")
async def set_backup_server_command(ctx, target: discord.Guild):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner can configure the backup server.", color=discord.Color.red()), mention_author=False)
        return
    if bot.get_guild(target.id) is None:
        await ctx.reply("⚠️ The bot must already be in that backup server. Ask its owner/admin to install it first.", mention_author=False)
        return
    previous = config.get("backup_guild", {}).get("id")
    config["backup_guild"] = {"id": str(target.id)}
    if previous and str(previous) != str(target.id):
        for members in config.setdefault("verified_members", {}).values():
            for member_id, record in list(members.items()):
                if isinstance(record, dict) and str(record.get("backup_guild_id")) != str(target.id):
                    members.pop(member_id, None)
    save_config()
    await ctx.reply(embed=discord.Embed(
        description=f"{EMOJIS['success']} Backup server set to **{target.name}**. New verifications will clearly authorize joining this server. If you changed the destination, members must verify again.",
        color=THEME_BLUE,
    ), mention_author=False)


@bot.command(name="pullmembers")
async def pull_members_command(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner can pull verified members.", color=discord.Color.red()), mention_author=False)
        return
    if ctx.guild is None:
        await ctx.reply("Run this command inside the source server where members verified.", mention_author=False)
        return
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY"):
        await ctx.reply("⚠️ OAuth client credentials and the encryption key must be configured on Bot-Hosting.net.", mention_author=False)
        return
    backup_id = config.get("backup_guild", {}).get("id")
    backup = bot.get_guild(int(backup_id)) if backup_id else None
    if backup is None:
        await ctx.reply("⚠️ Set a backup server first with `>setbackupserver <server ID>`; the bot must already be in it.", mention_author=False)
        return

    members = config.get("verified_members", {}).get(str(ctx.guild.id), {})
    eligible = [
        (member_id, record) for member_id, record in members.items()
        if isinstance(record, dict)
        and record.get("refresh_token")
        and str(record.get("backup_guild_id")) == str(backup.id)
    ]
    if not eligible:
        await ctx.reply(f"ℹ️ No members with valid `guilds.join` authorization were found for **{backup.name}**. Ask them to run `>verify` first.", mention_author=False)
        return

    status = await ctx.reply(f"🔄 Processing **{len(eligible)}** verified, authorized members for **{backup.name}**…", mention_author=False)
    timeout = aiohttp.ClientTimeout(total=25)
    added = already_there = failed = not_in_source = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for member_id, record in eligible:
            try:
                try:
                    await ctx.guild.fetch_member(int(member_id))
                except discord.NotFound:
                    members.pop(member_id, None)
                    not_in_source += 1
                    save_config()
                    continue
                old_refresh = decrypt_oauth_secret(record["refresh_token"])
                async with session.post(
                    "https://discord.com/api/v10/oauth2/token",
                    data={
                        "client_id": DISCORD_CLIENT_ID,
                        "client_secret": DISCORD_CLIENT_SECRET,
                        "grant_type": "refresh_token",
                        "refresh_token": old_refresh,
                    },
                ) as response:
                    if response.status != 200:
                        record.pop("refresh_token", None)
                        failed += 1
                        save_config()
                        continue
                    token = await response.json()

                access_token = token.get("access_token")
                rotated_refresh = token.get("refresh_token") or old_refresh
                if not access_token:
                    failed += 1
                    continue
                record["refresh_token"] = encrypt_oauth_secret(rotated_refresh)
                save_config()

                async with session.put(
                    f"https://discord.com/api/v10/guilds/{backup.id}/members/{member_id}",
                    headers={"Authorization": f"Bot {TOKEN}"},
                    json={"access_token": access_token},
                ) as response:
                    if response.status == 201:
                        added += 1
                    elif response.status == 204:
                        already_there += 1
                    else:
                        failed += 1
                        logging.warning("OAuth guild join for verified member %s failed with HTTP %s", member_id, response.status)
                await asyncio.sleep(1.0)
            except (RuntimeError, ImportError, ValueError, KeyError, aiohttp.ClientError, asyncio.TimeoutError):
                failed += 1
                logging.exception("Could not process a verified member OAuth authorization")
    await status.edit(content=f"✅ Member transfer finished for **{backup.name}**: **{added}** added, **{already_there}** already there, **{not_in_source}** no longer in the source server, **{failed}** could not be added.")


def economy_channel_allowed(ctx):
    """Restrict all economy commands to the server's configured economy channel."""
    economy_commands = {
        "economy", "balance", "daily", "work", "beg", "crime", "deposit", "withdraw",
        "pay", "leaderboard", "shop", "buy", "inventory", "sell", "bankupgrade", "rob",
        "slots", "betflip", "addmoney", "removemoney", "setmoney", "addbank", "setbank",
        "resetmoney", "reseteco"
    }
    if ctx.guild is None or ctx.command is None or ctx.command.name not in economy_commands:
        return True
    channel_id = guild_setting("economy_channels", ctx.guild.id)
    return not channel_id or ctx.channel.id == int(channel_id)


@bot.check
async def enforce_economy_channel(ctx):
    is_owner = is_bot_owner(ctx)
    if not is_owner and config.get("maintenance", {}).get("enabled", False):
        raise commands.CheckFailure("The Seven Deadly Seas Helper is currently in maintenance mode.")
    if not is_owner and ctx.command is not None:
        command_name = ctx.command.qualified_name.lower()
        disabled_commands = config.get("disabled_commands", {})
        if disabled_commands.get("__all__") or disabled_commands.get(command_name):
            raise commands.CheckFailure(f"The `{command_name}` command is disabled.")
    if not economy_channel_allowed(ctx):
        raise commands.CheckFailure("Economy commands can only be used in the configured economy channel.")
    return True


# =========================
# TICKET SYSTEM INTERACTIVE VIEWS
# =========================

class TicketPanelActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.green, emoji="👋", custom_id="claim_ticket_btn")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not user_is_staff_interaction(interaction):
            await interaction.response.send_message(f"{EMOJIS['error']} Only staff members can claim tickets.", ephemeral=True)
            return

        button.disabled = True
        button.label = f"Claimed by {interaction.user.name}"
        button.style = discord.ButtonStyle.secondary

        staff_role_id = guild_setting("staff_roles", interaction.guild.id)
        staff_role = interaction.guild.get_role(int(staff_role_id)) if staff_role_id else discord.utils.find(lambda r: "staff team" in r.name.lower(), interaction.guild.roles)
        ping_target = staff_role.mention if staff_role else ""

        await interaction.response.edit_message(view=self)
        await interaction.channel.send(f"{EMOJIS['success']} {ping_target} Ticket has been claimed by {interaction.user.mention}!")
        await audit(interaction.guild, f"Ticket claimed: {interaction.channel.name} by {interaction.user}")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"{EMOJIS['warning']} Closing ticket in 5 seconds...", ephemeral=False)
        await audit(interaction.guild, f"Ticket closed by {interaction.user} | Channel: {interaction.channel.name}")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Closed by {interaction.user}")
        except discord.HTTPException:
            pass

def user_is_staff_interaction(interaction):
    if interaction.user.guild_permissions.kick_members:
        return True
    sup_role_id = guild_setting("supervisor_roles", interaction.guild.id)
    if sup_role_id and any(r.id == int(sup_role_id) for r in interaction.user.roles):
        return True
    staff_role_id = guild_setting("staff_roles", interaction.guild.id)
    if staff_role_id and any(r.id == int(staff_role_id) for r in interaction.user.roles):
        return True
    return any("staff" in r.name.lower() or "trial" in r.name.lower() or "mod" in r.name.lower() for r in interaction.user.roles)


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Support", value="support", description="General help and support inquiries", emoji=discord.PartialEmoji.from_str(EMOJIS['support'])),
            discord.SelectOption(label="Report", value="report", description="Report hackers in The Seven Deadly Seas", emoji=discord.PartialEmoji.from_str(EMOJIS['warning'])),
            discord.SelectOption(label="Purchase Error", value="purchase_error", description="Report an in-game purchase error", emoji=discord.PartialEmoji.from_str(EMOJIS['purchase']))
        ]
        super().__init__(placeholder="Select a ticket category...", min_values=1, max_values=1, options=options, custom_id="ticket_category_select")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        category_name = self.values[0]
        category_display = {
            "support": "Support",
            "report": "Report",
            "purchase_error": "Purchase Error"
        }.get(category_name.lower(), category_name.title())

        # Check if user already has a ticket open containing their username
        existing_channel = discord.utils.get(guild.text_channels, name=f"-{member.name.lower()}-")
        if not existing_channel:
            existing_channel = discord.utils.find(lambda c: f"-{member.name.lower()}-" in c.name, guild.text_channels)
            
        if existing_channel:
            await interaction.response.send_message(f"{EMOJIS['error']} You already have an open ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }

        supervisor_role_id = guild_setting("supervisor_roles", guild.id)
        if supervisor_role_id:
            sup_role = guild.get_role(int(supervisor_role_id))
            if sup_role:
                overwrites[sup_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        staff_role_id = guild_setting("staff_roles", guild.id)
        staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else discord.utils.find(lambda r: "staff team" in r.name.lower(), guild.roles)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        # Fetch category mapping
        cat_data = config.get("ticket_categories", {}).get(str(guild.id), {})
        category_keys = ("purchase_error", "purchase") if category_name == "purchase_error" else (category_name.lower(),)
        cat_id = next((cat_data.get(key) for key in category_keys if cat_data.get(key)), None) if isinstance(cat_data, dict) else None
        
        category = guild.get_channel(int(cat_id)) if cat_id else None
        if not category:
            category = discord.utils.get(guild.categories, name="Support Tickets")
        if not category:
            category = await guild.create_category("Support Tickets")

        # Calculate next ticket number based on existing channels in the guild
        existing_tickets = [c for c in guild.text_channels if "-" in c.name and c.name.split("-")[0].isdigit()]
        ticket_number = len(existing_tickets) + 1

        # Format: (ticket number)-(username)-(ticket category name) -> e.g. 1-silent-support
        clean_username = "".join(c for c in member.name.lower() if c.isalnum() or c in "-_")
        channel_name = f"{ticket_number}-{clean_username}-{category_name.lower()}"

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Category: {category_display} | Ticket for {member} ({member.id})"
        )

        embed = discord.Embed(
            title=f"{EMOJIS['ticket']} Support Ticket — {category_display}",
            description=f"Hello {member.mention}!\nYou selected **{category_display}**. Support staff will be with you shortly.\n\nClick **Claim Ticket** to take ownership or **Close Ticket** when finished.",
            color=THEME_GOLD if category_name == "purchase_error" else THEME_BLUE,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="The Seven Deadly Seas Helper • Ticket System")

        await channel.send(content=staff_role.mention if staff_role else "", embed=embed, view=TicketPanelActionsView())
        await interaction.response.send_message(f"{EMOJIS['success']} Your ticket has been created: {channel.mention}", ephemeral=True)
        await audit(guild, f"Ticket created ({category_display}) by {member} ({member.id}) | Channel: {channel.name}")


class TicketDropdownView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


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
    embed.set_footer(text=f"{guild.name} • The Seven Deadly Seas Helper System")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def change_staff_role(ctx, member, target_role, action):
    if target_role is None:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} The specified or configured role could not be found in this server.", color=discord.Color.red()))
        return

    if member.id == ctx.author.id or member.id == ctx.guild.owner_id or target_role.is_default() or target_role.managed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Action unauthorized or invalid role.", color=discord.Color.orange()))
        return

    try:
        if action == "promote":
            await member.add_roles(target_role, reason=f"Staff promotion by {ctx.author}")
            result = "promoted"
        else:
            await member.remove_roles(target_role, reason=f"Staff demotion by {ctx.author}")
            result = "demoted"

        await send_promodemo_embed(ctx.guild, member, target_role, result, ctx.author)
        await audit(ctx.guild, f"Staff {result}: {member} | Role: {target_role.name} | By: {ctx.author}")
        emoji = EMOJIS['promo'] if result == "promoted" else EMOJIS['demo']
        await ctx.reply(embed=discord.Embed(description=f"{emoji} {member.mention} was **{result}**: {target_role.mention}", color=discord.Color.green() if result == "promoted" else discord.Color.red()))
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Missing permissions to modify roles.", color=discord.Color.red()))


# =========================
# STAFF TRAINING COMMANDS
# =========================

class TrainingControlView(discord.ui.View):
    def __init__(self, trainee_id, trainer_id):
        super().__init__(timeout=None)
        self.trainee_id = trainee_id
        self.trainer_id = trainer_id

    @discord.ui.button(label="Pass Training", style=discord.ButtonStyle.green, custom_id="pass_training_btn")
    async def pass_training(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.trainer_id and interaction.user.id != interaction.guild.owner_id and interaction.user.id != OWNER_ID:
            await interaction.response.send_message(f"{EMOJIS['error']} Only the designated trainer can complete this evaluation.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        user_id = str(self.trainee_id)

        if "trainings" not in config:
            config["trainings"] = {}
        if guild_id not in config["trainings"]:
            config["trainings"][guild_id] = {}

        config["trainings"][guild_id][user_id] = {
            "status": "PASSED",
            "trainer": str(interaction.user),
            "timestamp": discord.utils.utcnow().isoformat()
        }
        save_config()

        trainee = interaction.guild.get_member(self.trainee_id)
        trainee_mention = trainee.mention if trainee else f"<@{self.trainee_id}>"

        embed = discord.Embed(
            title=f"{EMOJIS['success']} Training Passed!",
            description=f"{trainee_mention} has successfully passed staff training!",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name=f"{EMOJIS['user']} Trainee", value=trainee_mention, inline=True)
        embed.add_field(name=f"{EMOJIS['shield']} Trainer", value=interaction.user.mention, inline=True)
        embed.set_footer(text="The Seven Deadly Seas Helper • Staff Training System")

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)
        await audit(interaction.guild, f"Staff Training Passed: {trainee_mention} | Trainer: {interaction.user}")

    @discord.ui.button(label="Fail Training", style=discord.ButtonStyle.red, custom_id="fail_training_btn")
    async def fail_training(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.trainer_id and interaction.user.id != interaction.guild.owner_id and interaction.user.id != OWNER_ID:
            await interaction.response.send_message(f"{EMOJIS['error']} Only the designated trainer can complete this evaluation.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        user_id = str(self.trainee_id)

        if "trainings" not in config:
            config["trainings"] = {}
        if guild_id not in config["trainings"]:
            config["trainings"][guild_id] = {}

        config["trainings"][guild_id][user_id] = {
            "status": "FAILED",
            "trainer": str(interaction.user),
            "timestamp": discord.utils.utcnow().isoformat()
        }
        save_config()

        trainee = interaction.guild.get_member(self.trainee_id)
        trainee_mention = trainee.mention if trainee else f"<@{self.trainee_id}>"

        embed = discord.Embed(
            title=f"{EMOJIS['demo']} Training Failed",
            description=f"{trainee_mention} did not pass staff training at this time.",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name=f"{EMOJIS['user']} Trainee", value=trainee_mention, inline=True)
        embed.add_field(name=f"{EMOJIS['shield']} Trainer", value=interaction.user.mention, inline=True)
        embed.set_footer(text="The Seven Deadly Seas Helper • Staff Training System")

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)
        await audit(interaction.guild, f"Staff Training Failed: {trainee_mention} | Trainer: {interaction.user}")


@bot.command(name="train")
@commands.guild_only()
async def train(ctx, trainee: discord.Member):
    """Starts an interactive training module for a staff member."""
    if not user_is_supervisor(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only staff supervisors can initiate training sessions.", color=discord.Color.red()))
        return

    embed = discord.Embed(
        title=f"{EMOJIS['shield']} Staff Training Session Initiated",
        description=f"Welcome {trainee.mention} to your official staff training session!",
        color=THEME_BLUE,
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name=f"{EMOJIS['user']} Trainee Info",
        value=f"{trainee.mention}\nID: `{trainee.id}`",
        inline=True
    )
    embed.add_field(
        name=f"{EMOJIS['shield']} Trainer",
        value=ctx.author.mention,
        inline=True
    )

    embed.add_field(
        name=f"{EMOJIS['gear']} Training Modules & Curriculum",
        value=(
            f"{EMOJIS['bullet']} **1. Moderation Quotas:** Max 3 kicks/bans per 24 hours.\n"
            f"{EMOJIS['bullet']} **2. Moderation Tools:** Use `>warn`, `>mute`, `>clear`, `>kick`, and `>ban` appropriately.\n"
            f"{EMOJIS['bullet']} **3. Ticket Management:** Assist users politely and close tickets using button controls.\n"
            f"{EMOJIS['bullet']} **4. Hierarchy & Respect:** Do not attempt commands on higher ranks."
        ),
        inline=False
    )

    embed.set_footer(text=f"{ctx.guild.name} • Trainer Controls Below", icon_url=ctx.author.display_avatar.url)

    view = TrainingControlView(trainee_id=trainee.id, trainer_id=ctx.author.id)
    await ctx.send(embed=embed, view=view)
    await audit(interaction=ctx.guild, message=f"Staff Training Started: {trainee} ({trainee.id}) by {ctx.author}")


@bot.command(name="trainings")
@commands.guild_only()
async def trainings(ctx, member: discord.Member = None):
    """Checks the training status of a staff member."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    m = member or ctx.author
    guild_id = str(ctx.guild.id)
    user_id = str(m.id)

    record = config.get("trainings", {}).get(guild_id, {}).get(user_id)

    embed = discord.Embed(
        title=f"{EMOJIS['audit']} Staff Training Record",
        color=THEME_BLUE,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=m.display_avatar.url)

    if not record:
        embed.description = f"{m.mention} has no recorded training sessions yet."
    else:
        status_emoji = EMOJIS['success'] if record['status'] == "PASSED" else EMOJIS['demo']
        embed.description = f"Training status for {m.mention}:"
        embed.add_field(name=f"{status_emoji} Status", value=f"**{record['status']}**", inline=True)
        embed.add_field(name=f"{EMOJIS['shield']} Evaluated By", value=record['trainer'], inline=True)

    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.reply(embed=embed)


# =========================
# MANAGEMENT & STAFF COMMANDS
# =========================

@bot.command(name="promo")
@commands.guild_only()
async def promo(ctx, staff: discord.Member, target_role: discord.Role = None):
    """Promotes a staff member."""
    if not user_is_supervisor(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    if target_role is None:
        ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id))
        if not ladder:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No rank ladder configured. Add roles using `>ranks add @role`", color=discord.Color.orange()))
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

        target_role = ctx.guild.get_role(ladder[next_index])

    await change_staff_role(ctx, staff, target_role, "promote")


@bot.command(name="demo")
@commands.guild_only()
async def demo(ctx, staff: discord.Member, target_role: discord.Role = None):
    """Demotes a staff member from their highest ladder rank."""
    if not user_is_supervisor(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    if target_role is None:
        ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id))
        if not ladder:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} No rank ladder configured. Add roles using `>ranks add @role`", color=discord.Color.orange()))
            return

        user_role_ids = [r.id for r in staff.roles]
        
        matched_index = -1
        for idx, r_id in enumerate(ladder):
            if r_id in user_role_ids:
                matched_index = idx

        if matched_index == -1:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {staff.mention} does not have any rank from the ladder!", color=discord.Color.orange()))
            return

        target_role = ctx.guild.get_role(ladder[matched_index])

    await change_staff_role(ctx, staff, target_role, "demote")


@bot.command(name="super")
@commands.guild_only()
async def super_cmd(ctx, member: discord.Member):
    """Assigns the supervisor role."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    role_id = guild_setting("supervisor_roles", ctx.guild.id)
    if not role_id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} Supervisor role not set up.", color=discord.Color.orange()))
        return
    assigned_role = ctx.guild.get_role(int(role_id))
    if not assigned_role:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Configured supervisor role no longer exists.", color=discord.Color.red()))
        return
    await member.add_roles(assigned_role, reason=f"Supervisor assigned by {ctx.author}")
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Assigned supervisor role to {member.mention}.", color=discord.Color.green()))


@bot.command(name="superintro")
@commands.guild_only()
async def superintro(ctx):
    """Deletes the trigger message and introduces supervisor commands."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass

    if not user_is_supervisor(ctx):
        err_msg = await ctx.send(embed=discord.Embed(description=f"{EMOJIS['error']} Only staff supervisors can use this command.", color=discord.Color.red()))
        await err_msg.delete(delay=5)
        return

    embed = discord.Embed(
        title=f"{EMOJIS['welcome']} Welcome Staff Supervisor!",
        description=(
            f"Welcome to **The Seven Deadly Seas Helper Supervisor System**! {EMOJIS['shield']}\n"
            "As a Supervisor, you have permission to manage the staff hierarchy using automated rank ladder commands."
        ),
        color=THEME_BLUE,
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name=f"{EMOJIS['promo']} `>promo @member [@role]`",
        value=(
            f"{EMOJIS['bullet']} Promotes a staff member.\n"
            f"{EMOJIS['bullet']} **Auto Ladder:** If no role is given, advances them to the next step up."
        ),
        inline=False
    )

    embed.add_field(
        name=f"{EMOJIS['demo']} `>demo @member [@role]`",
        value=(
            f"{EMOJIS['bullet']} Demotes a staff member.\n"
            f"{EMOJIS['bullet']} **Auto Ladder:** If no role is given, strips their highest rank to drop them down."
        ),
        inline=False
    )

    embed.add_field(
        name=f"{EMOJIS['gear']} `>train @member`",
        value=f"{EMOJIS['bullet']} Starts an interactive staff training session.",
        inline=False
    )

    embed.set_footer(text=f"Requested by {ctx.author.display_name} • The Seven Deadly Seas Helper Guide", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="staffintro")
@commands.guild_only()
async def staffintro(ctx):
    """Deletes the trigger message and introduces staff moderation commands."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass

    if not user_is_staff(ctx):
        err_msg = await ctx.send(embed=discord.Embed(description=f"{EMOJIS['error']} Only staff members can use this command.", color=discord.Color.red()))
        await err_msg.delete(delay=5)
        return

    embed = discord.Embed(
        title=f"{EMOJIS['welcome']} Welcome to The Seven Deadly Seas Helper Staff Team!",
        description=(
            f"Hello {ctx.author.mention}! Thank you for keeping our community safe and active. {EMOJIS['shield']}\n"
            "Here is a quick introduction to your staff moderation tools and commands."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name=f"{EMOJIS['warning']} Moderation & Safety Commands",
        value=(
            f"{EMOJIS['bullet']} `>kick @member [reason]` — Kicks a disruptive user (Max 3/24h).\n"
            f"{EMOJIS['bullet']} `>ban @member [reason]` — Permanently bans a rule-breaker (Max 3/24h).\n"
            f"{EMOJIS['bullet']} `>unban <user_id> [reason]` — Unbans a user by ID.\n"
            f"{EMOJIS['bullet']} `>mute @member [reason]` — Times out a user for 10 minutes.\n"
            f"{EMOJIS['bullet']} `>unmute @member [reason]` — Removes timeout from a member.\n"
            f"{EMOJIS['bullet']} `>warn @member [reason]` — Issues a formal warning.\n"
            f"{EMOJIS['bullet']} `>unwarn @member [index]` — Removes a warning.\n"
            f"{EMOJIS['bullet']} `>clear [amount]` — Purges chat messages."
        ),
        inline=False
    )

    embed.set_footer(text=f"Requested by {ctx.author.display_name} • The Seven Deadly Seas Helper Guide", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="staff")
@commands.guild_only()
async def staff_cmd(ctx, member: discord.Member):
    """Assigns Staff Team and Trial Mod/Moderator roles automatically."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    staff_team_role = discord.utils.find(lambda r: "staff team" in r.name.lower(), ctx.guild.roles)
    trial_mod_role = discord.utils.find(lambda r: "trial mod" in r.name.lower() or "trial moderator" in r.name.lower(), ctx.guild.roles)

    found_roles = [r for r in [staff_team_role, trial_mod_role] if r]
    if not found_roles:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Could not find 'Staff Team' or 'Trial Mod/Moderator' roles in this server.", color=discord.Color.red()))
        return

    await member.add_roles(*found_roles, reason=f"Staff roles assigned by {ctx.author}")
    assigned_mentions = ", ".join([r.mention for r in found_roles])
    
    embed = discord.Embed(
        title=f"{EMOJIS['success']} Staff Team Roles Assigned",
        description=f"Successfully assigned {assigned_mentions} to {member.mention}.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.reply(embed=embed)
    await audit(ctx.guild, f"Staff roles ({assigned_mentions}) given to {member} ({member.id}) by {ctx.author}")


@bot.command(name="role")
@commands.guild_only()
async def role_cmd(ctx, member: discord.Member, target_role: discord.Role):
    """Toggles a role on a member, ensuring users cannot manage roles higher or equal to their own."""
    is_owner = (ctx.author.id == OWNER_ID or ctx.author.id == ctx.guild.owner_id)

    if not is_owner and not user_is_supervisor(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    if target_role.position >= ctx.guild.me.top_role.position:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I cannot manage that role because it is higher than or equal to my highest role.", color=discord.Color.red()))
        return

    if not is_owner and target_role.position >= ctx.author.top_role.position:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You cannot manage a role that is higher than or equal to your own highest role.", color=discord.Color.red()))
        return

    if target_role in member.roles:
        try:
            await member.remove_roles(target_role, reason=f"Role toggled off by {ctx.author}")
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['demo']} Removed {target_role.mention} from {member.mention}.", color=discord.Color.red()))
        except discord.Forbidden:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Missing permissions to remove that role.", color=discord.Color.red()))
    else:
        try:
            await member.add_roles(target_role, reason=f"Role toggled on by {ctx.author}")
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['promo']} Added {target_role.mention} to {member.mention}.", color=discord.Color.green()))
        except discord.Forbidden:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Missing permissions to assign that role.", color=discord.Color.red()))


@bot.command(name="steal")
@commands.guild_only()
async def steal(ctx, emoji: str, name: str = None):
    """Steals an emoji into the server."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

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
# TICKET SETUP COMMANDS
# =========================

@bot.command(name="sendticket")
@commands.guild_only()
async def sendticket(ctx, channel: discord.TextChannel = None):
    """Deploys the persistent ticket selection dropdown panel to a channel."""
    if not user_is_supervisor(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only owners or supervisors can deploy the ticket panel.", color=discord.Color.red()))
        return

    target_channel = channel or ctx.channel

    embed = discord.Embed(
        title=f"{EMOJIS['ticket']} The Seven Deadly Seas Helper",
        description=(
            f"Welcome to the **{ctx.guild.name}** ticket center! {EMOJIS['shield']}\n\n"
            f"Please select a category from the dropdown menu below to open a private ticket with our staff team:\n\n"
            f"{EMOJIS['bullet']} {EMOJIS['support']} **Support** — General help & assistance\n"
            f"{EMOJIS['bullet']} {EMOJIS['warning']} **Report** — Report hackers in The Seven Deadly Seas\n"
            f"{EMOJIS['bullet']} {EMOJIS['purchase']} **Purchase Error** — In-game purchase problems"
        ),
        color=THEME_GOLD,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="The Seven Deadly Seas Helper • Support System", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

    await target_channel.send(embed=embed, view=TicketDropdownView())
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Successfully deployed ticket panel to {target_channel.mention}!", color=discord.Color.green()))


@bot.command(name="setstaffrole")
@commands.guild_only()
async def setstaffrole(ctx, role: discord.Role):
    """Sets the staff role that can view, claim, and get pinged in tickets."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    if "staff_roles" not in config:
        config["staff_roles"] = {}
    config["staff_roles"][str(ctx.guild.id)] = role.id
    save_config()

    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Ticket staff role set to {role.mention}.", color=discord.Color.green()))


@bot.command(name="setcategory")
@commands.guild_only()
async def setcategory(ctx, ticket_type: str, category: discord.CategoryChannel):
    """Sets the Discord category for support, report, or purchase_error tickets."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    ticket_type = ticket_type.lower()
    if ticket_type == "purchase":
        ticket_type = "purchase_error"  # Backward-compatible alias for existing setup instructions.
    if ticket_type not in ["support", "report", "purchase_error"]:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Invalid type! Choose from: `support`, `report`, `purchase_error`.\nExample: `>setcategory report #CategoryName`", color=discord.Color.red()))
        return

    guild_id = str(ctx.guild.id)
    if "ticket_categories" not in config or not isinstance(config["ticket_categories"], dict):
        config["ticket_categories"] = {}
    
    if guild_id not in config["ticket_categories"] or not isinstance(config["ticket_categories"][guild_id], dict):
        config["ticket_categories"][guild_id] = {}

    config["ticket_categories"][guild_id][ticket_type] = category.id
    save_config()

    display_type = {"purchase_error": "Purchase Error"}.get(ticket_type, ticket_type.capitalize())
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Category for **{display_type}** tickets set to **{category.name}**.", color=THEME_GOLD))


@bot.command(name="setmodmail")
@commands.guild_only()
async def setmodmail(ctx, channel: discord.TextChannel):
    """Set the staff-only text channel where DM conversations open threads."""
    can_configure = (
        is_bot_owner(ctx)
        or user_is_supervisor(ctx)
        or ctx.author.guild_permissions.manage_guild
    )
    if not can_configure:
        await ctx.reply(embed=discord.Embed(
            description=f"{EMOJIS['error']} You need Manage Server or a supervisor role to configure modmail.",
            color=THEME_BLUE
        ))
        return

    bot_member = ctx.guild.me
    permissions = channel.permissions_for(bot_member) if bot_member else None
    required_permissions = ("view_channel", "send_messages", "embed_links", "create_public_threads", "send_messages_in_threads")
    missing_permissions = [name.replace("_", " ") for name in required_permissions if not permissions or not getattr(permissions, name, False)]
    if missing_permissions:
        await ctx.reply(embed=discord.Embed(
            description=f"{EMOJIS['error']} I need these permissions in {channel.mention}: {', '.join(missing_permissions)}.",
            color=THEME_BLUE
        ))
        return

    if channel.permissions_for(ctx.guild.default_role).view_channel:
        await ctx.reply(embed=discord.Embed(
            description=f"{EMOJIS['error']} {channel.mention} is visible to @everyone. Make it staff-only before setting it as the modmail channel.",
            color=THEME_BLUE
        ))
        return

    config.setdefault("modmail_channels", {})[str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(
        description=f"{EMOJIS['success']} Modmail is set to {channel.mention}. New DMs from your server members will open staff threads there. Keep staff access configured on this private channel.",
        color=THEME_GOLD
    ))


def modmail_message_text(message):
    text = (message.content or "[No text content]").strip()
    if message.attachments:
        attachment_lines = [f"[{attachment.filename}]({attachment.url})" for attachment in message.attachments]
        text += "\n\n**Attachments:**\n" + "\n".join(attachment_lines)
    return text[:4000]


def is_modmail_staff(member, guild):
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_messages or member.guild_permissions.kick_members:
        return True
    staff_role_id = guild_setting("staff_roles", guild.id)
    supervisor_role_id = guild_setting("supervisor_roles", guild.id)
    configured_role_ids = {int(role_id) for role_id in (staff_role_id, supervisor_role_id) if role_id}
    return any(
        role.id in configured_role_ids
        or any(word in role.name.lower() for word in ("staff", "moderator", "mod", "trial"))
        for role in member.roles
    )


async def handle_modmail_dm(message):
    """Forward a user's DM into their server's existing or newly-created staff thread."""
    if message.author.bot:
        return

    routes = config.setdefault("modmail_threads", {})
    thread = None
    route_key = None
    for key, route in list(routes.items()):
        if not isinstance(route, dict) or int(route.get("user_id", 0)) != message.author.id:
            continue
        candidate = bot.get_channel(int(key))
        if isinstance(candidate, discord.Thread):
            thread, route_key = candidate, key
            break

    if thread is None:
        for guild_id, channel_id in config.setdefault("modmail_channels", {}).items():
            guild = bot.get_guild(int(guild_id))
            channel = guild.get_channel(int(channel_id)) if guild else None
            if guild is None or not isinstance(channel, discord.TextChannel):
                continue
            member = guild.get_member(message.author.id)
            if member is None:
                try:
                    member = await guild.fetch_member(message.author.id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            starter_embed = discord.Embed(
                title="Modmail Conversation Opened",
                description=f"A staff thread has been opened for **{member}** (`{member.id}`).",
                color=THEME_GOLD,
                timestamp=discord.utils.utcnow()
            )
            starter_embed.set_thumbnail(url=member.display_avatar.url)
            try:
                starter = await channel.send(embed=starter_embed, allowed_mentions=discord.AllowedMentions.none())
                safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", member.name).strip("-_") or str(member.id)
                thread = await starter.create_thread(name=f"modmail-{safe_name}"[:100], auto_archive_duration=10080)
                route_key = str(thread.id)
                routes[route_key] = {"guild_id": guild.id, "user_id": member.id}
                save_config()
                break
            except (discord.Forbidden, discord.HTTPException):
                logging.exception("Could not create a modmail thread for user %s in guild %s", message.author.id, guild.id)
                continue

    if thread is None:
        try:
            await message.author.send("Modmail is not configured in a server you share with this bot, or the bot cannot open a staff thread yet.")
        except discord.Forbidden:
            pass
        return

    if thread.archived:
        try:
            await thread.edit(archived=False)
        except discord.HTTPException:
            pass

    try:
        user_embed = discord.Embed(
            title="Message from user",
            description=modmail_message_text(message),
            color=THEME_BLUE,
            timestamp=discord.utils.utcnow()
        )
        user_embed.set_author(name=f"{message.author} • {message.author.id}", icon_url=message.author.display_avatar.url)
        await thread.send(embed=user_embed, allowed_mentions=discord.AllowedMentions.none())
        await message.author.send("📨 Your message has been sent to the staff team.")
    except (discord.Forbidden, discord.HTTPException):
        logging.exception("Could not forward modmail DM from user %s", message.author.id)


async def handle_modmail_thread_message(message):
    """Relay staff replies to the user without exposing the staff member's name."""
    if message.author.bot or not isinstance(message.channel, discord.Thread):
        return
    route = config.setdefault("modmail_threads", {}).get(str(message.channel.id))
    if not isinstance(route, dict):
        return
    guild = message.guild
    if guild is None or not is_modmail_staff(message.author, guild):
        return

    try:
        user = bot.get_user(int(route["user_id"])) or await bot.fetch_user(int(route["user_id"]))
        reply = f"🛡️ **Staff team:**\n{modmail_message_text(message)}"
        await user.send(reply[:2000], allowed_mentions=discord.AllowedMentions.none())
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, KeyError, ValueError):
        logging.exception("Could not relay staff modmail reply from thread %s", message.channel.id)


# =========================
# MODERATION COMMANDS
# =========================

@bot.command(name="kick")
@commands.guild_only()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    """Kicks a member (Staff capped at 3 per 24 hours with hierarchy checks)."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    if member.id == ctx.guild.owner_id or member.id == OWNER_ID:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You cannot kick the server owner or bot owner.", color=discord.Color.red()))
        return

    if member.id == ctx.author.id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot kick yourself.", color=discord.Color.orange()))
        return

    if not user_is_owner(ctx) and member.top_role.position >= ctx.author.top_role.position:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You cannot kick {member.mention} because their highest role is higher than or equal to yours.", color=discord.Color.red()))
        return

    allowed, count = check_and_increment_action_limit(ctx, "kicks", limit=3)
    if not allowed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You have reached your limit of **3 kicks per 24 hours**.", color=discord.Color.orange()))
        return

    try:
        await member.kick(reason=f"{reason} (Kicked by {ctx.author})")
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Kicked {member.mention} | Reason: {reason}", color=discord.Color.orange()))
        await audit(ctx.guild, f"Member kicked: {member} ({member.id}) | Reason: {reason} | By: {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permissions to kick that member.", color=discord.Color.red()))


@bot.command(name="ban")
@commands.guild_only()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    """Bans a member (Staff capped at 3 per 24 hours with hierarchy checks)."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    if member.id == ctx.guild.owner_id or member.id == OWNER_ID:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You cannot ban the server owner or bot owner.", color=discord.Color.red()))
        return

    if member.id == ctx.author.id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot ban yourself.", color=discord.Color.orange()))
        return

    if not user_is_owner(ctx) and member.top_role.position >= ctx.author.top_role.position:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You cannot ban {member.mention} because their highest role is higher than or equal to yours.", color=discord.Color.red()))
        return

    allowed, count = check_and_increment_action_limit(ctx, "bans", limit=3)
    if not allowed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You have reached your limit of **3 bans per 24 hours**.", color=discord.Color.orange()))
        return

    try:
        await member.ban(reason=f"{reason} (Banned by {ctx.author})")
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Banned {member.mention} | Reason: {reason}", color=discord.Color.red()))
        await audit(ctx.guild, f"Member banned: {member} ({member.id}) | Reason: {reason} | By: {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permissions to ban that member.", color=discord.Color.red()))


@bot.command(name="unban")
@commands.guild_only()
async def unban(ctx, user_id: int, *, reason="No reason provided"):
    """Unbans a user by their Discord User ID."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"{reason} (Unbanned by {ctx.author})")
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Successfully unbanned {user.mention} (`{user.id}`) | Reason: {reason}", color=discord.Color.green()))
        await audit(ctx.guild, f"User unbanned: {user} ({user.id}) | Reason: {reason} | By: {ctx.author}")
    except discord.NotFound:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Could not find a banned user with ID `{user_id}`.", color=discord.Color.red()))
    except discord.HTTPException:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Failed to unban the user.", color=discord.Color.red()))


@bot.command(name="mute")
@commands.guild_only()
async def mute(ctx, member: discord.Member, *, reason="No reason provided"):
    """Timeouts a member for 10 minutes."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    from datetime import timedelta
    try:
        await member.timeout(timedelta(minutes=10), reason=f"{reason} (Muted by {ctx.author})")
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Timed out {member.mention} for 10 minutes.", color=discord.Color.orange()))
        await audit(ctx.guild, f"Member timed out: {member} ({member.id}) | Reason: {reason} | By: {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permissions to timeout that member.", color=discord.Color.red()))


@bot.command(name="unmute")
@commands.guild_only()
async def unmute(ctx, member: discord.Member, *, reason="No reason provided"):
    """Removes timeout from a member."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    try:
        await member.timeout(None, reason=f"{reason} (Unmuted by {ctx.author})")
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Removed timeout from {member.mention}.", color=discord.Color.green()))
        await audit(ctx.guild, f"Member timeout removed: {member} ({member.id}) | Reason: {reason} | By: {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I lack permissions to unmute that member.", color=discord.Color.red()))


@bot.command(name="warn")
@commands.guild_only()
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    """Issues a formal warning to a member."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    if member.id == ctx.author.id or member.id == ctx.guild.owner_id:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot warn that user.", color=discord.Color.orange()))
        return

    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    if "warnings" not in config:
        config["warnings"] = {}
    if guild_id not in config["warnings"]:
        config["warnings"][guild_id] = {}
    if user_id not in config["warnings"][guild_id]:
        config["warnings"][guild_id][user_id] = []

    config["warnings"][guild_id][user_id].append({
        "reason": reason,
        "moderator": str(ctx.author),
        "timestamp": discord.utils.utcnow().isoformat()
    })
    save_config()

    warn_count = len(config["warnings"][guild_id][user_id])

    embed = discord.Embed(
        title=f"{EMOJIS['warning']} Member Warned",
        description=f"{member.mention} has been warned.\n**Reason:** {reason}\n**Total Warnings:** {warn_count}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Moderator: {ctx.author}")
    await ctx.reply(embed=embed)

    try:
        await member.send(embed=discord.Embed(title=f"{EMOJIS['warning']} You were warned in {ctx.guild.name}", description=f"**Reason:** {reason}", color=discord.Color.orange()))
    except discord.HTTPException:
        pass

    await audit(ctx.guild, f"Member warned: {member} ({member.id}) | Reason: {reason} | Total: {warn_count} | By: {ctx.author}")


@bot.command(name="unwarn")
@commands.guild_only()
async def unwarn(ctx, member: discord.Member, index: int = None):
    """Removes a warning from a member."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    if "warnings" not in config or guild_id not in config["warnings"] or user_id not in config["warnings"][guild_id] or not config["warnings"][guild_id][user_id]:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {member.mention} has no warnings recorded.", color=discord.Color.orange()))
        return

    user_warns = config["warnings"][guild_id][user_id]

    if index is None:
        config["warnings"][guild_id][user_id] = []
        save_config()
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Cleared all warnings for {member.mention}.", color=discord.Color.green()))
        await audit(ctx.guild, f"All warnings cleared for {member} ({member.id}) by {ctx.author}")
    else:
        if 1 <= index <= len(user_warns):
            removed = user_warns.pop(index - 1)
            save_config()
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Removed warning #{index} for {member.mention} (Reason: `{removed['reason']}`).", color=discord.Color.green()))
            await audit(ctx.guild, f"Warning #{index} removed for {member} ({member.id}) by {ctx.author}")
        else:
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Invalid warning index number.", color=discord.Color.red()))


@bot.command(name="clear")
@commands.guild_only()
async def clear(ctx, amount: int = 5):
    """Purges messages."""
    if not user_is_staff(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return

    await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(embed=discord.Embed(description=f"{EMOJIS['success']} Cleared {amount} messages.", color=discord.Color.green()))
    await msg.delete(delay=3)


# =========================
# UTILITY & INFO COMMANDS
# =========================

@bot.command(name="ping")
@commands.guild_only()
async def ping(ctx):
    """Shows bot latency."""
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Pong! Latency: `{round(bot.latency * 1000)}ms`", color=THEME_BLUE))


@bot.command(name="serverinfo")
@commands.guild_only()
async def serverinfo(ctx):
    """Shows server info."""
    g = ctx.guild
    embed = discord.Embed(title=f"{EMOJIS['gear']} {g.name} Information", color=THEME_BLUE)
    embed.add_field(name=f"{EMOJIS['user']} Owner", value=g.owner, inline=True)
    embed.add_field(name=f"{EMOJIS['ladder']} Members", value=g.member_count, inline=True)
    embed.add_field(name=f"{EMOJIS['success']} Created On", value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    await ctx.reply(embed=embed)


@bot.command(name="userinfo")
@commands.guild_only()
async def userinfo(ctx, member: discord.Member = None):
    """Shows user info."""
    m = member or ctx.author
    embed = discord.Embed(title=f"{EMOJIS['user']} {m} Info", color=m.color)
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="ID", value=m.id, inline=True)
    embed.add_field(name="Joined", value=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "N/A", inline=True)
    await ctx.reply(embed=embed)


@bot.command(name="avatar")
@commands.guild_only()
async def avatar(ctx, member: discord.Member = None):
    """Shows user avatar."""
    m = member or ctx.author
    embed = discord.Embed(title=f"{EMOJIS['user']} Avatar for {m}", color=THEME_BLUE)
    embed.set_image(url=m.display_avatar.url)
    await ctx.reply(embed=embed)


# =========================
# FUN & MISC COMMANDS
# =========================

@bot.command(name="coinflip")
async def coinflip(ctx):
    """Flips a coin."""
    result = random.choice(["Heads", "Tails"])
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} The coin landed on: **{result}**", color=THEME_BLUE))


@bot.command(name="roll")
async def roll(ctx):
    """Rolls a dice (1-6)."""
    result = random.randint(1, 6)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} You rolled a **{result}**!", color=THEME_BLUE))


# =========================
# SETUP COMMANDS (RANKS ADD / CLEAR)
# =========================

# =========================
# ECONOMY COMMANDS
# =========================

ECONOMY_EMOJI = EMOJIS["purchase"]
ECONOMY_ITEMS = {
    "laptop": {"price": 800, "description": "A shiny laptop. Sell it later if you need cash."},
    "phone": {"price": 450, "description": "A new phone for your collection."},
    "watch": {"price": 250, "description": "A stylish watch."},
    "trophy": {"price": 1200, "description": "A trophy to show off your wins."},
    "cookie": {"price": 50, "description": "A tasty cookie. Restore your luck (coming soon)."},
}


def economy_account(ctx):
    """Return a persistent account scoped to this server and member."""
    economy = config.setdefault("economy", {})
    guild_accounts = economy.setdefault(str(ctx.guild.id), {})
    account = guild_accounts.setdefault(str(ctx.author.id), {
        "wallet": 0, "bank": 0, "bank_level": 0, "inventory": [], "cooldowns": {}
    })
    account.setdefault("wallet", 0)
    account.setdefault("bank", 0)
    account.setdefault("bank_level", 0)
    account.setdefault("inventory", [])
    account.setdefault("cooldowns", {})
    return account


def economy_member_account(guild, member):
    economy = config.setdefault("economy", {})
    guild_accounts = economy.setdefault(str(guild.id), {})
    return guild_accounts.setdefault(str(member.id), {
        "wallet": 0, "bank": 0, "bank_level": 0, "inventory": [], "cooldowns": {}
    })


def economy_embed(title, description=None, color=None):
    return discord.Embed(
        title=f"{ECONOMY_EMOJI} {title}", description=description,
        color=color or THEME_BLUE
    )


def economy_bank_limit(account):
    return 10000 + int(account.get("bank_level", 0)) * 5000


def economy_cooldown(account, action, seconds):
    import time
    now = time.time()
    expires = float(account["cooldowns"].get(action, 0)) + seconds
    if now < expires:
        return int(expires - now) + 1
    account["cooldowns"][action] = now
    return 0


@bot.command(name="balance", aliases=["bal", "wallet"])
@commands.guild_only()
async def balance(ctx, member: discord.Member = None):
    """Show your wallet and bank, or another member's balance."""
    member = member or ctx.author
    account = economy_member_account(ctx.guild, member)
    embed = economy_embed(f"{member.display_name}'s Balance", color=THEME_BLUE)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name=f"{ECONOMY_EMOJI} Wallet", value=f"**{account['wallet']:,}** coins", inline=True)
    embed.add_field(name="🏦 Bank", value=f"**{account['bank']:,} / {economy_bank_limit(account):,}** coins", inline=True)
    embed.add_field(name="💰 Total", value=f"**{account['wallet'] + account['bank']:,}** coins", inline=False)
    save_config()
    await ctx.reply(embed=embed)


@bot.command(name="daily")
@commands.guild_only()
async def daily(ctx):
    account = economy_account(ctx)
    remaining = economy_cooldown(account, "daily", 86400)
    if remaining:
        await ctx.reply(embed=economy_embed("Daily Reward", f"{EMOJIS['warning']} Come back in **{remaining // 3600}h {(remaining % 3600) // 60}m**.", discord.Color.orange()))
        return
    amount = random.randint(250, 500)
    account["wallet"] += amount
    save_config()
    await ctx.reply(embed=economy_embed("Daily Reward Collected", f"{EMOJIS['success']} You received **{amount:,} coins**. Your wallet now has **{account['wallet']:,}** coins!", discord.Color.green()))


@bot.command(name="work", aliases=["job"])
@commands.guild_only()
async def work(ctx):
    account = economy_account(ctx)
    remaining = economy_cooldown(account, "work", 3600)
    if remaining:
        await ctx.reply(embed=economy_embed("Work", f"{EMOJIS['warning']} Your next shift starts in **{remaining // 60}m {remaining % 60}s**.", discord.Color.orange()))
        return
    jobs = ["delivered packages", "fixed a computer", "served drinks", "walked some dogs", "helped at the market"]
    amount = random.randint(80, 220)
    account["wallet"] += amount
    save_config()
    await ctx.reply(embed=economy_embed("Shift Complete", f"You {random.choice(jobs)} and earned **{amount:,} coins**! {EMOJIS['success']}", discord.Color.green()))


@bot.command(name="beg")
@commands.guild_only()
async def beg(ctx):
    account = economy_account(ctx)
    remaining = economy_cooldown(account, "beg", 300)
    if remaining:
        await ctx.reply(embed=economy_embed("Beg", f"{EMOJIS['warning']} Try again in **{remaining}s**.", discord.Color.orange()))
        return
    amount = random.randint(0, 100)
    account["wallet"] += amount
    save_config()
    if amount:
        message = f"A kind passerby gave you **{amount} coins**."
    else:
        message = "Nobody had spare change this time. Better luck next time!"
    await ctx.reply(embed=economy_embed("A Little Help", message, discord.Color.green()))


@bot.command(name="crime")
@commands.guild_only()
async def crime(ctx):
    account = economy_account(ctx)
    remaining = economy_cooldown(account, "crime", 1800)
    if remaining:
        await ctx.reply(embed=economy_embed("Crime", f"{EMOJIS['warning']} Lay low for **{remaining // 60}m {remaining % 60}s**.", discord.Color.orange()))
        return
    if random.random() < 0.55:
        amount = random.randint(100, 350)
        account["wallet"] += amount
        result = f"You got away with **{amount:,} coins**! {EMOJIS['success']}"
        color = discord.Color.green()
    else:
        amount = min(account["wallet"], random.randint(50, 200))
        account["wallet"] -= amount
        result = f"You got caught and paid a **{amount:,}-coin fine**. {EMOJIS['warning']}"
        color = discord.Color.red()
    save_config()
    await ctx.reply(embed=economy_embed("Risky Business", result, color))


@bot.command(name="deposit", aliases=["dep"])
@commands.guild_only()
async def deposit(ctx, amount: str):
    account = economy_account(ctx)
    amount = account["wallet"] if amount.lower() == "all" else int(amount)
    if amount <= 0 or amount > account["wallet"]:
        await ctx.reply(embed=economy_embed("Deposit", f"{EMOJIS['error']} Enter an amount up to your wallet balance (**{account['wallet']:,}**).", discord.Color.red()))
        return
    available = economy_bank_limit(account) - account["bank"]
    if amount > available:
        await ctx.reply(embed=economy_embed("Bank Full", f"Your bank has room for **{available:,}** more coins. Upgrade it with `>bankupgrade`.", discord.Color.orange()))
        return
    account["wallet"] -= amount
    account["bank"] += amount
    save_config()
    await ctx.reply(embed=economy_embed("Deposit Complete", f"{EMOJIS['success']} Deposited **{amount:,} coins** into your bank."))


@bot.command(name="withdraw", aliases=["with"])
@commands.guild_only()
async def withdraw(ctx, amount: str):
    account = economy_account(ctx)
    amount = account["bank"] if amount.lower() == "all" else int(amount)
    if amount <= 0 or amount > account["bank"]:
        await ctx.reply(embed=economy_embed("Withdraw", f"{EMOJIS['error']} Enter an amount up to your bank balance (**{account['bank']:,}**).", discord.Color.red()))
        return
    account["bank"] -= amount
    account["wallet"] += amount
    save_config()
    await ctx.reply(embed=economy_embed("Withdrawal Complete", f"{EMOJIS['success']} Withdrew **{amount:,} coins** to your wallet."))


@bot.command(name="pay", aliases=["give", "transfer"])
@commands.guild_only()
async def pay(ctx, member: discord.Member, amount: int):
    account = economy_account(ctx)
    if member.bot or member == ctx.author or amount <= 0 or account["wallet"] < amount:
        await ctx.reply(embed=economy_embed("Transfer", f"{EMOJIS['error']} Choose another member and a positive amount you can afford.", discord.Color.red()))
        return
    recipient = economy_member_account(ctx.guild, member)
    account["wallet"] -= amount
    recipient["wallet"] += amount
    save_config()
    await ctx.reply(embed=economy_embed("Transfer Complete", f"{EMOJIS['success']} Sent **{amount:,} coins** to {member.mention}."))


@bot.command(name="leaderboard", aliases=["lb", "rich"])
@commands.guild_only()
async def leaderboard(ctx):
    accounts = config.setdefault("economy", {}).get(str(ctx.guild.id), {})
    ranked = sorted(accounts.items(), key=lambda pair: int(pair[1].get("wallet", 0)) + int(pair[1].get("bank", 0)), reverse=True)[:10]
    if not ranked:
        await ctx.reply(embed=economy_embed("Richest Members", "No economy accounts yet. Try `>daily` to get started."))
        return
    rows = []
    for rank, (user_id, account) in enumerate(ranked, 1):
        total = int(account.get("wallet", 0)) + int(account.get("bank", 0))
        rows.append(f"**{rank}.** <@{user_id}> — **{total:,}** coins")
    await ctx.reply(embed=economy_embed("Server Rich List", "\n".join(rows)))


@bot.command(name="shop", aliases=["store"])
@commands.guild_only()
async def shop(ctx):
    embed = economy_embed("Shop", "Buy items with `>buy <item>`. Sell items with `>sell <item>`.\n")
    for item, details in ECONOMY_ITEMS.items():
        embed.add_field(name=f"{item.title()} — {details['price']:,} coins", value=details["description"], inline=False)
    await ctx.reply(embed=embed)


@bot.command(name="buy")
@commands.guild_only()
async def buy(ctx, item: str):
    item = item.lower()
    if item not in ECONOMY_ITEMS:
        await ctx.reply(embed=economy_embed("Shop", f"{EMOJIS['error']} That item is unavailable. Use `>shop` to see the catalog.", discord.Color.red()))
        return
    account = economy_account(ctx)
    price = ECONOMY_ITEMS[item]["price"]
    if account["wallet"] < price:
        await ctx.reply(embed=economy_embed("Not Enough Coins", f"You need **{price:,} coins**; your wallet has **{account['wallet']:,}**.", discord.Color.red()))
        return
    account["wallet"] -= price
    account["inventory"].append(item)
    save_config()
    await ctx.reply(embed=economy_embed("Purchase Complete", f"{EMOJIS['success']} You bought a **{item}** for **{price:,} coins**."))


@bot.command(name="inventory", aliases=["inv", "bag"])
@commands.guild_only()
async def inventory(ctx, member: discord.Member = None):
    member = member or ctx.author
    account = economy_member_account(ctx.guild, member)
    items = account.get("inventory", [])
    counts = {item: items.count(item) for item in set(items)}
    description = "\n".join(f"{ECONOMY_EMOJI} **{item.title()}** × {count}" for item, count in sorted(counts.items())) or "Your inventory is empty. Visit `>shop`!"
    await ctx.reply(embed=economy_embed(f"{member.display_name}'s Inventory", description))


@bot.command(name="sell")
@commands.guild_only()
async def sell(ctx, item: str):
    item = item.lower()
    account = economy_account(ctx)
    if item not in ECONOMY_ITEMS or item not in account["inventory"]:
        await ctx.reply(embed=economy_embed("Sell Item", f"{EMOJIS['error']} You don't own that item. Check `>inventory`.", discord.Color.red()))
        return
    account["inventory"].remove(item)
    amount = ECONOMY_ITEMS[item]["price"] // 2
    account["wallet"] += amount
    save_config()
    await ctx.reply(embed=economy_embed("Item Sold", f"{EMOJIS['success']} Sold **{item}** for **{amount:,} coins**."))


@bot.command(name="bankupgrade", aliases=["upgradebank"])
@commands.guild_only()
async def bankupgrade(ctx):
    account = economy_account(ctx)
    cost = 2000 + int(account["bank_level"]) * 2000
    if account["wallet"] < cost:
        await ctx.reply(embed=economy_embed("Bank Upgrade", f"Upgrade costs **{cost:,} coins**. Your wallet has **{account['wallet']:,}**.", discord.Color.red()))
        return
    account["wallet"] -= cost
    account["bank_level"] += 1
    save_config()
    await ctx.reply(embed=economy_embed("Bank Upgraded", f"{EMOJIS['success']} Capacity increased to **{economy_bank_limit(account):,} coins**!"))


@bot.command(name="rob")
@commands.guild_only()
async def rob(ctx, member: discord.Member):
    account = economy_account(ctx)
    if OWNER_ID and member.id == OWNER_ID:
        await ctx.reply(embed=economy_embed("Rob", f"{EMOJIS['shield']} The bot owner is protected from robbery.", discord.Color.orange()))
        return
    if member.bot or member == ctx.author:
        await ctx.reply(embed=economy_embed("Rob", f"{EMOJIS['error']} Pick another server member.", discord.Color.red()))
        return
    remaining = economy_cooldown(account, "rob", 3600)
    if remaining:
        await ctx.reply(embed=economy_embed("Rob", f"{EMOJIS['warning']} Wait **{remaining // 60}m {remaining % 60}s** before trying again.", discord.Color.orange()))
        return
    victim = economy_member_account(ctx.guild, member)
    if victim["wallet"] < 100:
        await ctx.reply(embed=economy_embed("Rob", "That member doesn't have enough in their wallet."))
        return
    if random.random() < 0.45:
        amount = min(victim["wallet"], random.randint(50, 250))
        victim["wallet"] -= amount
        account["wallet"] += amount
        message, color = f"You stole **{amount:,} coins** from {member.mention}!", discord.Color.green()
    else:
        fine = min(account["wallet"], random.randint(50, 200))
        account["wallet"] -= fine
        victim["wallet"] += fine
        message, color = f"You were caught! You paid {member.mention} a **{fine:,}-coin fine**.", discord.Color.red()
    save_config()
    await ctx.reply(embed=economy_embed("Robbery Result", message, color))


@bot.command(name="slots")
@commands.guild_only()
async def slots(ctx, bet: int):
    account = economy_account(ctx)
    if bet <= 0 or bet > 5000 or bet > account["wallet"]:
        await ctx.reply(embed=economy_embed("Slots", f"{EMOJIS['error']} Bet between 1 and 5,000 coins, up to your wallet balance.", discord.Color.red()))
        return
    symbols = ["🍒", "🍋", "🔔", "💎", "⭐"]
    result = [random.choice(symbols) for _ in range(3)]
    account["wallet"] -= bet
    if result[0] == result[1] == result[2]:
        winnings = bet * 5
        account["wallet"] += winnings
        message = f"{' | '.join(result)}\nJackpot! You won **{winnings:,} coins**."
        color = discord.Color.green()
    elif len(set(result)) == 2:
        winnings = bet * 2
        account["wallet"] += winnings
        message = f"{' | '.join(result)}\nTwo match! You won **{winnings:,} coins**."
        color = discord.Color.green()
    else:
        message = f"{' | '.join(result)}\nNo match this time. You lost **{bet:,} coins**."
        color = discord.Color.red()
    save_config()
    await ctx.reply(embed=economy_embed("Slot Machine", message, color))


@bot.command(name="betflip")
@commands.guild_only()
async def betflip(ctx, choice: str, bet: int):
    account = economy_account(ctx)
    choice = choice.lower()
    if choice not in ("heads", "tails") or bet <= 0 or bet > account["wallet"]:
        await ctx.reply(embed=economy_embed("Coinflip Bet", f"{EMOJIS['error']} Use `>betflip <heads/tails> <amount>` with a valid wallet bet.", discord.Color.red()))
        return
    result = random.choice(["heads", "tails"])
    if choice == result:
        account["wallet"] += bet
        message, color = f"It was **{result}**! You won **{bet:,} coins**.", discord.Color.green()
    else:
        account["wallet"] -= bet
        message, color = f"It was **{result}**. You lost **{bet:,} coins**.", discord.Color.red()
    save_config()
    await ctx.reply(embed=economy_embed("Coinflip Bet", message, color))


@bot.command(name="economy")
@commands.guild_only()
async def economy_help(ctx):
    description = (
        "`>balance [@member]` · `>daily` · `>work` · `>beg` · `>crime`\n"
        "`>deposit <amount/all>` · `>withdraw <amount/all>` · `>pay @member <amount>`\n"
        "`>leaderboard` · `>shop` · `>buy <item>` · `>sell <item>` · `>inventory [@member]`\n"
        "`>bankupgrade` · `>rob @member` · `>slots <bet>` · `>betflip <heads/tails> <bet>`\n\n"
        f"All balances are saved per server. Currency: {ECONOMY_EMOJI}"
    )
    if OWNER_ID and ctx.author.id == OWNER_ID:
        description += (
            "\n\n**Bot owner (per-server):** `>addmoney @member <amount> [wallet/bank]` · "
            "`>removemoney @member <amount> [wallet/bank]` · `>setmoney @member <amount> [wallet/bank]` · "
            "`>resetmoney @member` · `>addbank @member <amount>` · `>setbank @member <amount>` · `>reseteco @member`"
        )
    embed = economy_embed("Economy Commands", description)
    await ctx.reply(embed=embed)


async def owner_economy_reply(ctx, title, description, color=discord.Color.green()):
    await ctx.reply(embed=economy_embed(title, description, color))


@bot.command(name="addmoney", aliases=["addcash"])
@commands.guild_only()
async def addmoney(ctx, member: discord.Member, amount: int, pocket: str = "wallet"):
    """Bot owner: add coins to a member's wallet or bank in this server."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    pocket = pocket.lower()
    account = economy_member_account(ctx.guild, member)
    if amount <= 0 or pocket not in ("wallet", "bank"):
        await owner_economy_reply(ctx, "Usage", "Use `>addmoney @member <positive amount> [wallet/bank]`.", discord.Color.orange())
        return
    if pocket == "bank" and account["bank"] + amount > economy_bank_limit(account):
        await owner_economy_reply(ctx, "Bank Capacity", f"That exceeds the member's bank limit of **{economy_bank_limit(account):,}**.", discord.Color.orange())
        return
    account[pocket] += amount
    save_config()
    await owner_economy_reply(ctx, "Money Added", f"{EMOJIS['success']} Added **{amount:,} coins** to {member.mention}'s {pocket}.")


@bot.command(name="removemoney", aliases=["takemoney", "removecash"])
@commands.guild_only()
async def removemoney(ctx, member: discord.Member, amount: int, pocket: str = "wallet"):
    """Bot owner: remove coins from a member's wallet or bank in this server."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    pocket = pocket.lower()
    account = economy_member_account(ctx.guild, member)
    if amount <= 0 or pocket not in ("wallet", "bank"):
        await owner_economy_reply(ctx, "Usage", "Use `>removemoney @member <positive amount> [wallet/bank]`.", discord.Color.orange())
        return
    removed = min(amount, account[pocket])
    account[pocket] -= removed
    save_config()
    await owner_economy_reply(ctx, "Money Removed", f"Removed **{removed:,} coins** from {member.mention}'s {pocket}.")


@bot.command(name="setmoney", aliases=["setcash"])
@commands.guild_only()
async def setmoney(ctx, member: discord.Member, amount: int, pocket: str = "wallet"):
    """Bot owner: set a member's wallet or bank balance in this server."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    pocket = pocket.lower()
    account = economy_member_account(ctx.guild, member)
    if amount < 0 or pocket not in ("wallet", "bank"):
        await owner_economy_reply(ctx, "Usage", "Use `>setmoney @member <amount of 0 or more> [wallet/bank]`.", discord.Color.orange())
        return
    if pocket == "bank" and amount > economy_bank_limit(account):
        await owner_economy_reply(ctx, "Bank Capacity", f"The bank limit is **{economy_bank_limit(account):,}**. Upgrade the account or set its wallet instead.", discord.Color.orange())
        return
    account[pocket] = amount
    save_config()
    await owner_economy_reply(ctx, "Balance Set", f"Set {member.mention}'s {pocket} to **{amount:,} coins**.")


@bot.command(name="addbank")
@commands.guild_only()
async def addbank(ctx, member: discord.Member, amount: int):
    """Bot owner: add coins directly to a member's bank, respecting its capacity."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    account = economy_member_account(ctx.guild, member)
    if amount <= 0 or account["bank"] + amount > economy_bank_limit(account):
        await owner_economy_reply(ctx, "Bank Capacity", f"Enter a positive amount that fits the remaining **{economy_bank_limit(account) - account['bank']:,}** bank capacity.", discord.Color.orange())
        return
    account["bank"] += amount
    save_config()
    await owner_economy_reply(ctx, "Bank Funds Added", f"Added **{amount:,} coins** to {member.mention}'s bank.")


@bot.command(name="setbank")
@commands.guild_only()
async def setbank(ctx, member: discord.Member, amount: int):
    """Bot owner: set a member's bank balance without exceeding its capacity."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    account = economy_member_account(ctx.guild, member)
    if amount < 0 or amount > economy_bank_limit(account):
        await owner_economy_reply(ctx, "Bank Capacity", f"Choose an amount from 0 to **{economy_bank_limit(account):,}**.", discord.Color.orange())
        return
    account["bank"] = amount
    save_config()
    await owner_economy_reply(ctx, "Bank Set", f"Set {member.mention}'s bank to **{amount:,} coins**.")


@bot.command(name="resetmoney", aliases=["clearmoney"])
@commands.guild_only()
async def resetmoney(ctx, member: discord.Member):
    """Bot owner: clear a member's wallet and bank balances in this server."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    account = economy_member_account(ctx.guild, member)
    account["wallet"] = 0
    account["bank"] = 0
    save_config()
    await owner_economy_reply(ctx, "Balances Cleared", f"Cleared {member.mention}'s wallet and bank. Inventory and cooldowns were kept.", discord.Color.orange())


@bot.command(name="reseteco", aliases=["resetaccount"])
@commands.guild_only()
async def reseteco(ctx, member: discord.Member):
    """Bot owner: reset a member's entire economy account in this server."""
    if not is_bot_owner(ctx):
        await owner_economy_reply(ctx, "Owner Only", f"{EMOJIS['error']} This command is only for the configured bot owner.", discord.Color.red())
        return
    config.setdefault("economy", {}).setdefault(str(ctx.guild.id), {})[str(member.id)] = {
        "wallet": 0, "bank": 0, "bank_level": 0, "inventory": [], "cooldowns": {}
    }
    save_config()
    await owner_economy_reply(ctx, "Account Reset", f"Reset {member.mention}'s complete economy account in this server.", discord.Color.orange())

@bot.group(invoke_without_command=True)
@commands.guild_only()
async def ranks(ctx):
    """Shows current rank ladder configuration."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id), [])
    ladder_str = " -> ".join([f"<@&{r_id}>" for r_id in ladder]) if ladder else "Not configured. Use `>ranks add @role`"
    
    embed = discord.Embed(
        title=f"{EMOJIS['ladder']} Staff Rank Ladder",
        description=f"**Current Order (Lowest to Highest):**\n{ladder_str}",
        color=THEME_BLUE
    )
    await ctx.reply(embed=embed)


@ranks.command(name="add")
@commands.guild_only()
async def ranks_add(ctx, role: discord.Role):
    """Adds a role to the rank ladder sequence (lowest to highest)."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    if role.is_default() or role.managed:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You cannot add that role to the ladder.", color=discord.Color.orange()))
        return

    guild_id = str(ctx.guild.id)
    if "rank_ladders" not in config:
        config["rank_ladders"] = {}
    if guild_id not in config["rank_ladders"]:
        config["rank_ladders"][guild_id] = []

    if role.id in config["rank_ladders"][guild_id]:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} That role is already in the rank ladder.", color=discord.Color.orange()))
        return

    config["rank_ladders"][guild_id].append(role.id)
    save_config()

    ladder = config["rank_ladders"][guild_id]
    ladder_str = " -> ".join([f"<@&{r_id}>" for r_id in ladder])

    embed = discord.Embed(
        title=f"{EMOJIS['success']} Rank Added to Ladder",
        description=f"Added {role.mention} successfully!\n\n**Current Ladder:**\n{ladder_str}",
        color=discord.Color.green()
    )
    await ctx.reply(embed=embed)


@ranks.command(name="clear")
@commands.guild_only()
async def ranks_clear(ctx):
    """Clears the entire rank ladder configuration."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    guild_id = str(ctx.guild.id)
    if "rank_ladders" in config and guild_id in config["rank_ladders"]:
        config["rank_ladders"][guild_id] = []
        save_config()

    embed = discord.Embed(
        title=f"{EMOJIS['success']} Rank Ladder Cleared",
        description="The rank ladder sequence has been reset.",
        color=discord.Color.red()
    )
    await ctx.reply(embed=embed)


@bot.command(name="setstaffsupervisor")
@commands.guild_only()
async def setstaffsupervisor(ctx, assigned_role: discord.Role):
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    config["supervisor_roles"][str(ctx.guild.id)] = assigned_role.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Supervisor role set to {assigned_role.mention}.", color=discord.Color.green()))


@bot.command(name="setpromodemo")
@commands.guild_only()
async def setpromodemo(ctx, channel: discord.TextChannel):
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    config["promodemo_channels"][str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Promo channel set to {channel.mention}.", color=discord.Color.green()))


@bot.command(name="setaudit")
@commands.guild_only()
async def setaudit(ctx, channel: discord.TextChannel):
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return

    config["audit_channels"][str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Audit channel set to {channel.mention}.", color=discord.Color.green()))


@bot.command(name="setlogchan")
@commands.guild_only()
async def setlogchan(ctx, channel: discord.TextChannel):
    """Sets the channel for server event embed logs."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return
    try:
        existing_hooks = await channel.webhooks()
        webhook = discord.utils.find(lambda item: item.name == "The Seven Deadly Seas Helper Event Logs", existing_hooks)
        if webhook is None:
            webhook = await channel.create_webhook(name="The Seven Deadly Seas Helper Event Logs", reason=f"Configured by {ctx.author}")
    except discord.Forbidden:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I need Manage Webhooks permission to set up event logging in that channel.", color=discord.Color.red()))
        return
    except discord.HTTPException:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I couldn't create or find a logging webhook in that channel.", color=discord.Color.red()))
        return

    config.setdefault("log_channels", {})[str(ctx.guild.id)] = channel.id
    config.setdefault("log_webhooks", {})[str(ctx.guild.id)] = webhook.id
    LOG_WEBHOOKS[ctx.guild.id] = webhook
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Server event logs will be sent to {channel.mention}.", color=discord.Color.green()))


@bot.command(name="setecochan")
@commands.guild_only()
async def setecochan(ctx, channel: discord.TextChannel):
    """Restricts economy commands to the selected channel."""
    if not user_is_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the bot owner or server owner can use this command.", color=discord.Color.red()))
        return
    config.setdefault("economy_channels", {})[str(ctx.guild.id)] = channel.id
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Economy commands can now only be used in {channel.mention}.", color=discord.Color.green()))


@bot.command(name="anc")
@commands.guild_only()
async def anc(ctx):
    """Bot owner: announce custom bot commissions with the ticket selector."""
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return

    embed = discord.Embed(
        title=f"{EMOJIS['purchase']} Get a Custom Discord Bot",
        description=(
            "Need a custom bot for your server? I build Discord bots tailored to your needs, "
            "including moderation, economy, tickets, and custom features.\n\n"
            f"{EMOJIS['bullet']} Click the menu below and select **Support** to discuss a custom bot order.\n"
            f"{EMOJIS['bullet']} Tell me what features you want, and we can discuss your bot."
        ),
        color=THEME_GOLD,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{ctx.guild.name} • Custom Bot Orders")
    await ctx.send(
        content="@everyone",
        embed=embed,
        view=TicketDropdownView(),
        allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False)
    )


OWNER_PROTECTED_COMMANDS = {
    "shutdown", "restart", "enable", "disable", "maintenance", "ownerhelp",
    "addextraowner", "removeextraowner", "pullmembers", "setbackupserver"
}


@bot.command(name="ownerhelp")
async def ownerhelp(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    embed = discord.Embed(title=f"{EMOJIS['shield']} The Seven Deadly Seas Helper • Owner Commands", color=THEME_BLUE)
    embed.add_field(name="Process", value="`>shutdown` — stop the bot\n`>restart` — restart the bot", inline=False)
    embed.add_field(name="Access", value="`>disable <command>` — disable one command for everyone except bot owners\n`>disable all` — disable all commands for everyone except bot owners\n`>enable <command>` — re-enable one command\n`>enable all` — clear all disabled commands\n`>maintenance on/off` — block commands for everyone except bot owners", inline=False)
    embed.add_field(name="Presence & Info", value="`>setactivity <playing/watching/listening/competing> <text>` — set the bot activity\n`>setstatus <text>` — shortcut for Playing\n`>clearstatus` — clear its activity\n`>servercount` — show the number of servers", inline=False)
    embed.add_field(name="Emoji Setup", value="`>uploadappemojis` (or `>uploademojis`) — upload the configured emoji pack once to the bot application for cross-server use", inline=False)
    embed.add_field(name="Member Verification", value="`>setbackupserver <server ID>` — select the backup server\n`>verify` — authorize account verification and future backup-server joining\n`>pullmembers` — add verified members who granted `guilds.join` access", inline=False)
    embed.add_field(name="Extra Owners", value="`>addextraowner @user` — grant bot-owner access (primary owner only)\n`>removeextraowner @user` — revoke extra-owner access", inline=False)
    await ctx.reply(embed=embed)


@bot.command(name="addextraowner")
async def add_extra_owner(ctx, target: discord.User):
    if not is_primary_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured primary bot owner can add extra owners.", color=discord.Color.red()))
        return
    if target.id == OWNER_ID:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} You are already the primary bot owner.", color=THEME_BLUE))
        return
    extra_owners = config.setdefault("extra_owners", {})
    if extra_owners.get(str(target.id)):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {target.mention} is already an extra owner.", color=THEME_BLUE))
        return
    extra_owners[str(target.id)] = True
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} {target.mention} can now use bot-owner commands.", color=THEME_BLUE))


@bot.command(name="removeextraowner")
async def remove_extra_owner(ctx, target: discord.User):
    if not is_primary_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured primary bot owner can remove extra owners.", color=discord.Color.red()))
        return
    if config.setdefault("extra_owners", {}).pop(str(target.id), None) is None:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} {target} is not an extra owner.", color=THEME_BLUE))
        return
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} {target.mention} no longer has extra-owner access.", color=THEME_BLUE))


@bot.command(name="shutdown")
async def shutdown(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} The Seven Deadly Seas Helper is shutting down.", color=THEME_BLUE))
    await bot.close()


@bot.command(name="restart")
async def restart(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} The Seven Deadly Seas Helper is restarting.", color=THEME_BLUE))
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable, *sys.argv])


@bot.command(name="disable")
async def disable_command(ctx, *, command_name: str):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    if command_name.strip().lower() == "all":
        config.setdefault("disabled_commands", {})["__all__"] = True
        save_config()
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} All commands are disabled for everyone except bot owners. You can still use commands and run `>enable all`.", color=THEME_BLUE))
        return
    command = bot.get_command(command_name.strip())
    if command is None:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I couldn't find that command.", color=discord.Color.red()))
        return
    qualified_name = command.qualified_name.lower()
    if qualified_name in OWNER_PROTECTED_COMMANDS:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} That owner recovery command cannot be disabled.", color=THEME_BLUE))
        return
    config.setdefault("disabled_commands", {})[qualified_name] = True
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Disabled `>{qualified_name}` for everyone except bot owners.", color=THEME_BLUE))


@bot.command(name="enable")
async def enable_command(ctx, *, command_name: str):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    if command_name.strip().lower() == "all":
        config["disabled_commands"] = {}
        save_config()
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} All commands are enabled again.", color=THEME_BLUE))
        return
    command = bot.get_command(command_name.strip())
    if command is None:
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} I couldn't find that command.", color=discord.Color.red()))
        return
    qualified_name = command.qualified_name.lower()
    config.setdefault("disabled_commands", {}).pop(qualified_name, None)
    save_config()
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Enabled `>{qualified_name}`.", color=THEME_BLUE))


@bot.command(name="maintenance")
async def maintenance(ctx, mode: str):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    mode = mode.lower()
    if mode not in {"on", "off"}:
        await ctx.reply(embed=discord.Embed(description="Use `>maintenance on` or `>maintenance off`.", color=THEME_BLUE))
        return
    config.setdefault("maintenance", {})["enabled"] = mode == "on"
    save_config()
    state = "enabled; commands are blocked for everyone except bot owners" if mode == "on" else "disabled; commands are available again"
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Maintenance mode {state}.", color=THEME_BLUE))


@bot.command(name="setstatus")
async def setstatus(ctx, *, activity: str):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    activity = activity[:128]
    await bot.change_presence(activity=discord.Game(name=activity))
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Bot activity set to **{discord.utils.escape_markdown(activity)}**.", color=THEME_BLUE))


@bot.command(name="setactivity")
async def set_activity(ctx, activity_type: str, *, text: str):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return

    activity_types = {
        "playing": discord.ActivityType.playing,
        "watching": discord.ActivityType.watching,
        "listening": discord.ActivityType.listening,
        "competing": discord.ActivityType.competing,
    }
    activity_type = activity_type.lower()
    if activity_type not in activity_types:
        await ctx.reply(embed=discord.Embed(
            description=f"{EMOJIS['warning']} Choose `playing`, `watching`, `listening`, or `competing`. Example: `>setactivity watching Super League Stars`.",
            color=THEME_GOLD
        ))
        return

    text = text[:128]
    await bot.change_presence(activity=discord.Activity(type=activity_types[activity_type], name=text))
    await ctx.reply(embed=discord.Embed(
        description=f"{EMOJIS['success']} Bot activity set to **{activity_type.title()} {discord.utils.escape_markdown(text)}**.",
        color=THEME_BLUE
    ))


@bot.command(name="clearstatus")
async def clearstatus(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    await bot.change_presence(activity=None)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['success']} Cleared the bot activity.", color=THEME_BLUE))


@bot.command(name="servercount")
async def servercount(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Only the configured bot owner can use this command.", color=discord.Color.red()))
        return
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['user']} The Seven Deadly Seas Helper is in **{len(bot.guilds)} servers**.", color=THEME_BLUE))


@bot.command(name="staffsettings")
@commands.guild_only()
async def staffsettings(ctx):
    """Displays current server staff configurations and rank ladder."""
    if not user_is_supervisor(ctx):
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to view staff settings.", color=discord.Color.red()))
        return

    supervisor_id = guild_setting("supervisor_roles", ctx.guild.id)
    staff_role_id = guild_setting("staff_roles", ctx.guild.id)
    promo_id = guild_setting("promodemo_channels", ctx.guild.id)
    audit_id = guild_setting("audit_channels", ctx.guild.id)
    ladder = config.get("rank_ladders", {}).get(str(ctx.guild.id), [])

    supervisor = ctx.guild.get_role(int(supervisor_id)) if supervisor_id else None
    staff_role = ctx.guild.get_role(int(staff_role_id)) if staff_role_id else None
    promo_channel = ctx.guild.get_channel(int(promo_id)) if promo_id else None
    audit_channel = ctx.guild.get_channel(int(audit_id)) if audit_id else None
    
    ladder_str = " -> ".join([f"<@&{r_id}>" for r_id in ladder]) if ladder else "Not configured (`>ranks add @role`)"

    embed = discord.Embed(
        title=f"{EMOJIS['gear']} The Seven Deadly Seas Helper • Server Settings",
        color=THEME_BLUE,
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name=f"{EMOJIS['shield']} Supervisor Role",
        value=supervisor.mention if supervisor else "Not configured (`>setstaffsupervisor @role`)",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['shield']} Ticket Staff Role",
        value=staff_role.mention if staff_role else "Not configured (`>setstaffrole @role`)",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['announcement']} Announcement Channel",
        value=promo_channel.mention if promo_channel else "Not configured (`>setpromodemo #channel`)",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['audit']} Audit Log Channel",
        value=audit_channel.mention if audit_channel else "Not configured (`>setaudit #channel`)",
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['ladder']} Rank Ladder Sequence",
        value=ladder_str,
        inline=False
    )
    embed.add_field(
        name=f"{EMOJIS['warning']} Staff Daily Quota",
        value="Max **3 Kicks / 3 Bans per 24 hours** for Staff Members",
        inline=False
    )

    embed.set_footer(text=f"{ctx.guild.name} • Configuration Dashboard", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    await ctx.reply(embed=embed)


# =========================
# HELP COMMAND
# =========================

@bot.command(name="help")
async def help_command(ctx):
    e = lambda key: guild_emoji(ctx.guild, key)
    embed = discord.Embed(title=f"{e('gear')} The Seven Deadly Seas Helper • Command Center", description="Here are all available command modules:", color=THEME_BLUE)
    embed.add_field(name=f"{e('shield')} Staff & Management", value="`>promo`, `>demo`, `>super`, `>superintro`, `>staffintro`, `>staff`, `>role`, `>train`, `>trainings`, `>steal`, `>anc` (bot owner)", inline=False)
    embed.add_field(name=f"{e('warning')} Moderation", value="`>kick`, `>ban`, `>unban`, `>mute`, `>unmute`, `>warn`, `>unwarn`, `>clear`", inline=False)
    embed.add_field(name=f"{e('ticket')} Support & Modmail", value="`>sendticket [#channel]`, `>setstaffrole @role`, `>setcategory <support/report/purchase_error> #category`, `>setmodmail #channel`", inline=False)
    embed.add_field(name=f"{e('user')} Utility & Info", value="`>verify`, `>ping`, `>serverinfo`, `>userinfo`, `>avatar`", inline=False)
    embed.add_field(name=f"{e('success')} Fun & Games", value="`>coinflip`, `>roll`, `>slots <bet>`, `>betflip <heads/tails> <bet>`", inline=False)
    embed.add_field(name=f"{e('purchase')} Economy", value="`>economy` for the full list: balance, daily, work, beg, crime, deposit, withdraw, pay, leaderboard, shop, buy, sell, inventory, bankupgrade, rob, slots, betflip", inline=False)
    embed.add_field(name=f"{e('gear')} Setup", value="`>ranks add @role`, `>ranks clear`, `>setstaffsupervisor`, `>setpromodemo`, `>setaudit`, `>setlogchan #channel`, `>setecochan #channel`, `>staffsettings`", inline=False)
    if is_bot_owner(ctx):
        embed.add_field(name=f"{e('shield')} Bot Owner", value="`>ownerhelp`, `>shutdown`, `>restart`, `>disable <command/all>`, `>enable <command/all>`, `>maintenance on/off`, `>setactivity <type> <text>`, `>setstatus <text>`, `>clearstatus`, `>servercount`, `>uploadappemojis`, `>addextraowner @user`, `>removeextraowner @user`", inline=False)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="uploadappemojis", aliases=["uploademojis"])
async def upload_emojis_command(ctx):
    if not is_bot_owner(ctx):
        await ctx.reply("⛔ Bot owner only.", mention_author=False)
        return
    status_message = await ctx.reply("🔄 Uploading the configured emoji pack to the bot application…", mention_author=False)
    try:
        uploaded, skipped, total = await upload_application_emoji_pack()
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await status_message.edit(content=f"❌ Application emoji upload failed: `{str(exc)[:500]}`")
        return
    summary = f"✅ Added **{uploaded}** emoji(s) to the bot application. **{total}** app emoji(s) are now configured and will be used across the bot's servers."
    if skipped:
        summary += "\n⚠️ Skipped: " + ", ".join(skipped[:8])
        if len(skipped) > 8:
            summary += f" and {len(skipped) - 8} more."
    await status_message.edit(content=summary)


# =========================
# EVENTS & STARTUP
# =========================

@bot.event
async def on_ready():
    global APPLICATION_EMOJIS_SYNCED
    await start_oauth_webserver()
    bot.add_view(TicketDropdownView())
    bot.add_view(TicketPanelActionsView())
    if not APPLICATION_EMOJIS_SYNCED:
        try:
            uploaded, skipped, total = await upload_application_emoji_pack()
            APPLICATION_EMOJIS_SYNCED = True
            logging.info("Application emoji sync complete: added=%s configured=%s skipped=%s", uploaded, total, len(skipped))
        except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logging.warning("Application emoji auto-sync failed; owner can run >uploadappemojis: %s", exc)
    logging.info("The Seven Deadly Seas Helper logged in as %s", bot.user)


@bot.event
async def on_message(message):
    # Message creation is intentionally not logged to avoid high-volume log spam.
    if message.guild is None:
        await handle_modmail_dm(message)
    else:
        await handle_modmail_thread_message(message)
    await bot.process_commands(message)


@bot.event
async def on_message_edit(before, after):
    if after.guild and after.author.id != (bot.user.id if bot.user else None) and before.content != after.content:
        await log_server_event(
            after.guild,
            "Message Edited",
            f"**Author:** {after.author.mention} (`{after.author.id}`)\n"
            f"**Channel:** {after.channel.mention}\n**Before:**\n{(before.content or '[No text content]')[:1300]}\n"
            f"**After:**\n{(after.content or '[No text content]')[:1300]}\n[Jump to message]({after.jump_url})",
            discord.Color.orange()
        )


@bot.event
async def on_message_delete(message):
    if message.guild and message.author.id != (bot.user.id if bot.user else None):
        await log_server_event(
            message.guild,
            "Message Deleted",
            f"**Author:** {message.author.mention} (`{message.author.id}`)\n"
            f"**Channel:** {message.channel.mention}\n**Content:**\n{(message.content or '[No text content]')[:3000]}",
            discord.Color.red()
        )


@bot.event
async def on_raw_message_delete(payload):
    if payload.cached_message is None and payload.guild_id:
        guild = bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id) if guild else None
        await log_server_event(
            guild,
            "Message Deleted (Not Cached)",
            f"A message was deleted in {channel.mention if channel else f'<#{payload.channel_id}>'}.\n"
            f"**Message ID:** `{payload.message_id}`\nMessage content was unavailable because it was not cached.",
            discord.Color.red()
        )


@bot.event
async def on_member_join(member):
    await log_server_event(member.guild, "Member Joined", f"{member.mention} (`{member.id}`) joined the server.", discord.Color.green())


@bot.event
async def on_member_remove(member):
    await log_server_event(member.guild, "Member Left", f"**Member:** {member} (`{member.id}`)", discord.Color.orange())


@bot.event
async def on_member_update(before, after):
    changes = []
    if before.nick != after.nick:
        changes.append(f"**Nickname:** `{before.nick or before.name}` → `{after.nick or after.name}`")
    old_roles = {role.id: role for role in before.roles}
    new_roles = {role.id: role for role in after.roles}
    added = [role.mention for role_id, role in new_roles.items() if role_id not in old_roles]
    removed = [role.mention for role_id, role in old_roles.items() if role_id not in new_roles]
    if added:
        changes.append("**Roles added:** " + ", ".join(added))
    if removed:
        changes.append("**Roles removed:** " + ", ".join(removed))
    if changes:
        await log_server_event(after.guild, "Member Updated", f"**Member:** {after.mention} (`{after.id}`)\n" + "\n".join(changes), discord.Color.orange())


@bot.event
async def on_member_ban(guild, user):
    await log_server_event(guild, "Member Banned", f"**User:** {user} (`{user.id}`)", discord.Color.red())


@bot.event
async def on_member_unban(guild, user):
    await log_server_event(guild, "Member Unbanned", f"**User:** {user} (`{user.id}`)", discord.Color.green())


@bot.event
async def on_guild_channel_create(channel):
    mention = channel.mention if hasattr(channel, "mention") else channel.name
    await log_server_event(channel.guild, "Channel Created", f"**Channel:** {mention}\n**Type:** {type(channel).__name__}", discord.Color.green())


@bot.event
async def on_guild_channel_delete(channel):
    await log_server_event(channel.guild, "Channel Deleted", f"**Channel:** #{channel.name} (`{channel.id}`)\n**Type:** {type(channel).__name__}", discord.Color.red())


@bot.event
async def on_guild_channel_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` → `{after.name}`")
    if hasattr(before, "topic") and before.topic != after.topic:
        changes.append(f"**Topic:** `{before.topic or 'None'}` → `{after.topic or 'None'}`")
    if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
        changes.append(f"**Slowmode:** `{before.slowmode_delay}s` → `{after.slowmode_delay}s`")
    if before.category_id != after.category_id:
        changes.append(f"**Category:** `{before.category}` → `{after.category}`")
    if changes:
        mention = after.mention if hasattr(after, "mention") else after.name
        await log_server_event(after.guild, "Channel Updated", f"**Channel:** {mention}\n" + "\n".join(changes), discord.Color.orange())


@bot.event
async def on_guild_role_create(role):
    await log_server_event(role.guild, "Role Created", f"**Role:** {role.mention} (`{role.id}`)", discord.Color.green())


@bot.event
async def on_guild_role_delete(role):
    await log_server_event(role.guild, "Role Deleted", f"**Role:** {role.name} (`{role.id}`)", discord.Color.red())


@bot.event
async def on_guild_role_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(f"**Name:** `{before.name}` → `{after.name}`")
    if before.permissions != after.permissions:
        changes.append("**Permissions:** changed")
    if before.color != after.color:
        changes.append(f"**Color:** `{before.color}` → `{after.color}`")
    if before.hoist != after.hoist or before.mentionable != after.mentionable:
        changes.append(f"**Display settings:** hoist `{before.hoist}` → `{after.hoist}`, mentionable `{before.mentionable}` → `{after.mentionable}`")
    if changes:
        await log_server_event(after.guild, "Role Updated", f"**Role:** {after.mention} (`{after.id}`)\n" + "\n".join(changes), discord.Color.orange())


@bot.event
async def on_voice_state_update(member, before, after):
    old_channel = before.channel.name if before.channel else "Not in voice"
    new_channel = after.channel.name if after.channel else "Not in voice"
    if before.channel == after.channel and before.self_mute == after.self_mute and before.mute == after.mute and before.self_deaf == after.self_deaf and before.deaf == after.deaf and before.self_stream == after.self_stream and before.self_video == after.self_video:
        return
    await log_server_event(member.guild, "Voice State Updated", f"**Member:** {member.mention}\n**Voice channel:** `{old_channel}` → `{new_channel}`\nMute, deaf, stream, or camera state changed.", THEME_BLUE)


@bot.event
async def on_guild_emojis_update(guild, before, after):
    old_emojis = {emoji.id: emoji for emoji in before}
    new_emojis = {emoji.id: emoji for emoji in after}
    added = [str(emoji) for emoji_id, emoji in new_emojis.items() if emoji_id not in old_emojis]
    removed = [emoji.name for emoji_id, emoji in old_emojis.items() if emoji_id not in new_emojis]
    changes = []
    if added:
        changes.append("**Added:** " + " ".join(added))
    if removed:
        changes.append("**Removed:** " + ", ".join(removed))
    if changes:
        await log_server_event(guild, "Custom Emojis Updated", "\n".join(changes), discord.Color.orange())


@bot.event
async def on_invite_create(invite):
    channel = invite.channel.mention if invite.channel else "Unknown"
    await log_server_event(invite.guild, "Invite Created", f"**Code:** `{invite.code}`\n**Channel:** {channel}\n**Created by:** {invite.inviter or 'Unknown'}", discord.Color.green())


@bot.event
async def on_invite_delete(invite):
    channel = invite.channel.mention if invite.channel else "Unknown"
    await log_server_event(invite.guild, "Invite Deleted", f"**Code:** `{invite.code}`\n**Channel:** {channel}", discord.Color.orange())


@bot.event
async def on_webhooks_update(channel):
    await log_server_event(channel.guild, "Webhooks Updated", f"Webhook configuration changed in {channel.mention}.", discord.Color.orange())


@bot.event
async def on_raw_reaction_add(payload):
    if payload.guild_id and payload.user_id != (bot.user.id if bot.user else None):
        guild = bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id) if guild else None
        await log_server_event(
            guild,
            "Reaction Added",
            f"**User:** <@{payload.user_id}>\n**Channel:** {channel.mention if channel else f'<#{payload.channel_id}>'}\n"
            f"**Emoji:** {payload.emoji}\n**Message ID:** `{payload.message_id}`",
            discord.Color.green()
        )


@bot.event
async def on_raw_reaction_remove(payload):
    if payload.guild_id and payload.user_id != (bot.user.id if bot.user else None):
        guild = bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id) if guild else None
        await log_server_event(
            guild,
            "Reaction Removed",
            f"**User:** <@{payload.user_id}>\n**Channel:** {channel.mention if channel else f'<#{payload.channel_id}>'}\n"
            f"**Emoji:** {payload.emoji}\n**Message ID:** `{payload.message_id}`",
            discord.Color.orange()
        )


@bot.event
async def on_guild_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(f"**Server name:** `{before.name}` → `{after.name}`")
    if before.icon != after.icon:
        changes.append("**Server icon:** changed")
    if before.verification_level != after.verification_level:
        changes.append(f"**Verification level:** `{before.verification_level}` → `{after.verification_level}`")
    if before.description != after.description:
        changes.append("**Server description:** changed")
    if changes:
        await log_server_event(after, "Server Settings Updated", "\n".join(changes), discord.Color.orange())


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        if not is_bot_owner(ctx) and config.get("maintenance", {}).get("enabled", False):
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} The Seven Deadly Seas Helper is currently in maintenance mode.", color=THEME_BLUE))
            return
        disabled_commands = config.get("disabled_commands", {})
        if not is_bot_owner(ctx) and ctx.command is not None and (disabled_commands.get("__all__") or disabled_commands.get(ctx.command.qualified_name.lower())):
            await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['warning']} The `>{ctx.command.qualified_name}` command is disabled.", color=THEME_BLUE))
            return
        if not economy_channel_allowed(ctx):
            channel_id = guild_setting("economy_channels", ctx.guild.id)
            await ctx.reply(embed=economy_embed("Wrong Channel", f"{EMOJIS['warning']} Economy commands are only available in <#{channel_id}>.", THEME_BLUE))
            return
        await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} You don't have permission to use this command.", color=discord.Color.red()))
        return
    
    logging.exception("Command error:", exc_info=error)
    await ctx.reply(embed=discord.Embed(description=f"{EMOJIS['error']} Error: `{str(error)}`", color=discord.Color.red()))


load_config()
apply_application_emoji_map()
bot.run(TOKEN)
