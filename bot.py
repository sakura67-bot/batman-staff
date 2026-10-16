"""Resell2Gain | Rank Management — prefix commands start with >."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("r2g-rank-management")
TOKEN = os.getenv("DISCORD_TOKEN")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
ASSET_DIR = Path("emoji_assets")
BOT_TITLE = "Resell2Gain | Rank Management"

# Ten static emojis and four animated GIF emojis.
EMOJI_SPECS = {
    "r2g_staff": ("S", (108, 73, 180), (255, 230, 128), False),
    "r2g_settings": ("⚙", (67, 78, 112), (235,
