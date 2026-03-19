"""BRKC Bot — Discord bot for Black River Kart Club.

Answers member questions using AI + scraped website content.
"""

import asyncio
import datetime
import logging
import pathlib
import time
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from .config import DISCORD_TOKEN, BOT_NAME, OWNER_ID, RATE_LIMIT, RATE_LIMIT_WINDOW
from .knowledge import KnowledgeBase
from .ai import answer_question

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Bot setup — needs message content intent to read messages
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
knowledge = KnowledgeBase()

# Rate limiting: {user_id: [timestamp, timestamp, ...]}
user_requests: dict[int, list[float]] = defaultdict(list)


# ===== Usage stats (in-memory, resets daily) =====
class UsageStats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.questions_answered: int = 0
        self.errors: int = 0
        self.api_calls: int = 0
        self.askers: dict[str, int] = defaultdict(int)
        self.reset_time: float = time.time()

    def record_question(self, author_name: str):
        self.questions_answered += 1
        self.askers[author_name] += 1

    def record_error(self):
        self.errors += 1

    def record_api_call(self):
        self.api_calls += 1

    def top_askers(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self.askers.items(), key=lambda x: x[1], reverse=True)[:n]

    def summary(self) -> str:
        uptime = time.time() - self.reset_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)

        lines = [
            f"Questions answered: {self.questions_answered}",
            f"Errors: {self.errors}",
            f"API calls: {self.api_calls}",
            f"Tracking period: {hours}h {minutes}m",
        ]

        top = self.top_askers()
        if top:
            lines.append("Top askers:")
            for name, count in top:
                lines.append(f"  {name}: {count}")

        return "\n".join(lines)


stats = UsageStats()


def is_owner():
    """Check decorator — only allows the bot owner."""
    async def predicate(ctx: commands.Context):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)


def check_rate_limit(user_id: int) -> bool:
    """Returns True if the user is within rate limits."""
    now = time.time()
    # Clean old entries
    user_requests[user_id] = [t for t in user_requests[user_id] if now - t < RATE_LIMIT_WINDOW]
    if len(user_requests[user_id]) >= RATE_LIMIT:
        return False
    user_requests[user_id].append(now)
    return True


@bot.event
async def on_ready():
    """Called when bot connects to Discord."""
    logger.info("%s is online as %s", BOT_NAME, bot.user)
    if OWNER_ID:
        logger.info("Owner ID: %s", OWNER_ID)
    else:
        logger.warning("No OWNER_ID set — owner commands won't work!")
    await knowledge.refresh()
    refresh_knowledge.start()
    heartbeat.start()
    daily_summary.start()


@bot.event
async def on_member_join(member: discord.Member):
    """Welcome new members."""
    # Find the first text channel the bot can send to (usually #general)
    channel = member.guild.system_channel
    if not channel:
        for ch in member.guild.text_channels:
            if ch.permissions_for(member.guild.me).send_messages:
                channel = ch
                break
    if channel:
        await channel.send(
            f"Welcome to the server, {member.mention}! I'm the BRKC Bot — feel free to ask me any questions about the club, classes, schedule, pricing, or anything else. Just tag me or use !ask."
        )


HEARTBEAT_FILE = pathlib.Path("/tmp/bot-healthy")


@tasks.loop(seconds=30)
async def heartbeat():
    """Touch a file every 30s so Docker can verify the bot is alive."""
    HEARTBEAT_FILE.touch()


@tasks.loop(hours=24)
async def refresh_knowledge():
    """Re-scrape websites once per day."""
    await knowledge.refresh()


@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
async def daily_summary():
    """DM the owner a daily stats summary at midnight UTC, then reset."""
    if not OWNER_ID:
        return
    try:
        owner = await bot.fetch_user(OWNER_ID)
        if owner:
            await owner.send(f"**Daily Bot Summary**\n```\n{stats.summary()}\n```")
    except Exception as e:
        logger.warning("Failed to send daily summary DM: %s", e)
    stats.reset()


# Keywords that suggest someone is asking about the club/karting
LISTEN_KEYWORDS = [
    "schedule", "race", "races", "racing", "class", "classes",
    "membership", "member", "sign up", "register", "registration",
    "price", "pricing", "cost", "how much", "fee", "fees",
    "kart", "karting", "go kart", "go-kart",
    "brkc", "kart club", "kartplex", "kart plex",
    "practice", "track", "lap", "laps",
    "rotax", "iame", "briggs", "tillotson", "206",
    "junior", "senior", "cadet", "mini max", "micro max",
    "weight", "age", "requirement", "requirements",
    "rent", "rental", "rentals",
    "storage", "garage",
    "academy", "lesson", "lessons", "training",
    "kartzone", "kart zone", "dealership",
    "sponsor", "sponsors",
    "location", "address", "directions", "where is",
    "hours", "open", "when do", "when does", "when is", "what time",
    "beginner", "getting started", "new to", "first time",
    "helmet", "gear", "equipment", "what do i need",
    "engine", "chassis", "tires", "fuel",
    "championship", "points", "standings", "results",
    "pit pass", "admission", "spectator",
    "lorain", "black river",
]


def looks_like_question(text: str) -> bool:
    """Check if a message looks like a karting-related question worth answering."""
    lower = text.lower()

    # Must contain a question mark or question-like phrasing
    is_question = (
        "?" in text
        or lower.startswith(("how", "what", "when", "where", "who", "why", "is there",
                             "are there", "do you", "does", "can i", "can you",
                             "anyone know", "anybody know", "do they", "is it"))
    )
    if not is_question:
        return False

    # Must contain at least one karting-related keyword
    return any(kw in lower for kw in LISTEN_KEYWORDS)


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages."""
    if message.author == bot.user:
        return
    if message.author.bot:
        return

    logger.debug("Message from %s: %s", message.author, message.content[:100])

    # Process commands first
    await bot.process_commands(message)

    # Skip if this was a command
    if message.content.startswith("!"):
        return

    # Direct mention or reply to bot — always respond
    is_direct = (
        bot.user in message.mentions
        or (
            message.reference
            and message.reference.resolved
            and message.reference.resolved.author == bot.user
        )
    )
    logger.info("is_direct=%s, mentions=%s, author_id=%s", is_direct, [str(m) for m in message.mentions], message.author.id)

    # General chat — chime in if it looks like a relevant question
    is_relevant = not is_direct and looks_like_question(message.content)

    if not is_direct and not is_relevant:
        return

    # Rate limit (owner is exempt)
    if message.author.id != OWNER_ID and not check_rate_limit(message.author.id):
        if is_direct:
            await message.reply("Slow down! You can ask me 6 questions per minute. Try again shortly.")
        return

    question = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not question:
        await message.reply("Hey! Ask me anything about BRKC — classes, schedule, pricing, getting started, and more!")
        return

    async with message.channel.typing():
        context = knowledge.get_context(question=question)
        stats.record_api_call()
        try:
            response = await asyncio.to_thread(
                answer_question, question, context,
                require_confidence=is_relevant and not is_direct,
            )
        except Exception:
            stats.record_error()
            logger.exception("Error answering question from %s", message.author)
            return

    if response:
        stats.record_question(message.author.display_name)
        await message.reply(response)


# ===== Regular commands =====

@bot.command(name="ask")
async def ask_command(ctx: commands.Context, *, question: str):
    """Ask the bot a question about BRKC. Usage: !ask <question>"""
    if ctx.author.id != OWNER_ID and not check_rate_limit(ctx.author.id):
        await ctx.reply("Slow down! You can ask me 6 questions per minute.")
        return

    async with ctx.typing():
        context = knowledge.get_context(question=question)
        stats.record_api_call()
        try:
            response = await asyncio.to_thread(answer_question, question, context)
        except Exception:
            stats.record_error()
            logger.exception("Error answering !ask from %s", ctx.author)
            await ctx.reply("Something went wrong. Try again in a moment.")
            return
    stats.record_question(ctx.author.display_name)
    await ctx.reply(response)


# ===== Owner-only commands =====

@bot.command(name="announce")
@is_owner()
async def announce_command(ctx: commands.Context, *, message: str):
    """Send an announcement as the bot. Usage: !announce <message>
    Supports @everyone and @here. Your command message gets deleted."""
    await ctx.message.delete()
    await ctx.send(message, allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True))


@bot.command(name="say")
@is_owner()
async def say_command(ctx: commands.Context, channel: discord.TextChannel, *, message: str):
    """Send a message to a specific channel. Usage: !say #channel <message>"""
    await ctx.message.delete()
    await channel.send(message, allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True))


@bot.command(name="dm")
@is_owner()
async def dm_command(ctx: commands.Context, user: discord.Member, *, message: str):
    """DM a user as the bot. Usage: !dm @user <message>"""
    await ctx.message.delete()
    try:
        await user.send(message)
        await ctx.author.send(f"DM sent to {user.display_name}: {message}")
    except discord.Forbidden:
        await ctx.author.send(f"Couldn't DM {user.display_name} — they may have DMs disabled.")


@bot.command(name="refresh")
@is_owner()
async def refresh_command(ctx: commands.Context):
    """Force refresh the knowledge base (owner only)."""
    await ctx.reply("Refreshing knowledge base...")
    await knowledge.refresh()
    await ctx.reply(f"Done! Loaded {len(knowledge.pages)} pages.")


@bot.command(name="status")
@is_owner()
async def status_command(ctx: commands.Context, *, status: str):
    """Change the bot's status. Usage: !status <text>"""
    await bot.change_presence(activity=discord.Game(name=status))
    await ctx.message.delete()


@bot.command(name="stats")
@is_owner()
async def stats_command(ctx: commands.Context):
    """View current usage stats (owner only)."""
    await ctx.reply(f"```\n{stats.summary()}\n```")


# ===== Custom facts commands =====

@bot.command(name="addfact")
@is_owner()
async def addfact_command(ctx: commands.Context, *, fact: str):
    """Add a custom fact to the knowledge base. Usage: !addfact <fact>"""
    num = knowledge.add_fact(fact)
    await ctx.reply(f"Added fact #{num}: {fact}")


@bot.command(name="removefact")
@is_owner()
async def removefact_command(ctx: commands.Context, number: int):
    """Remove a custom fact by line number. Usage: !removefact <number>"""
    try:
        removed = knowledge.remove_fact(number)
        await ctx.reply(f"Removed fact #{number}: {removed}")
    except IndexError as e:
        await ctx.reply(str(e))


@bot.command(name="listfacts")
@is_owner()
async def listfacts_command(ctx: commands.Context):
    """List all custom facts. Usage: !listfacts"""
    facts = knowledge.list_facts()
    if not facts:
        await ctx.reply("No custom facts yet. Use `!addfact <fact>` to add one.")
        return

    lines = [f"**{num}.** {text}" for num, text in facts]
    message = "**Custom Facts:**\n" + "\n".join(lines)

    # Discord messages are capped at 2000 chars
    if len(message) > 2000:
        chunks = []
        current = "**Custom Facts:**\n"
        for line in lines:
            if len(current) + len(line) + 1 > 2000:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)
        for chunk in chunks:
            await ctx.reply(chunk)
    else:
        await ctx.reply(message)


# Error handler for owner-only commands
@announce_command.error
@say_command.error
@dm_command.error
@refresh_command.error
@status_command.error
@stats_command.error
@addfact_command.error
@removefact_command.error
@listfacts_command.error
async def owner_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply("Only the bot owner can use that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"Missing argument: `{error.param.name}`. Check `!help {ctx.command}` for usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(f"Invalid argument. Check `!help {ctx.command}` for usage.")


def main():
    """Entry point."""
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not set! Add it to your .env file.")
        return
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
