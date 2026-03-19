"""BRKC Bot configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Bot Settings ---
BOT_NAME = "BRKC Bot"
COMMAND_PREFIX = "!"

# --- Owner Discord User ID ---
# Right-click your name in Discord (with Developer Mode on) → Copy User ID
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# --- Rate Limiting ---
# Max questions per user per minute
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "6"))
RATE_LIMIT_WINDOW = 60  # seconds

# --- Websites to scrape for knowledge ---
KNOWLEDGE_URLS = [
    # Lorain KartPlex (includes all BRKC info)
    "https://www.lorainkartplex.com",
    "https://www.lorainkartplex.com/racing.html",
    "https://www.lorainkartplex.com/rentals.html",
    "https://www.lorainkartplex.com/garages.html",
    "https://www.lorainkartplex.com/simulators.html",
    "https://www.lorainkartplex.com/group-events.html",
    "https://www.lorainkartplex.com/events.html",
    "https://www.lorainkartplex.com/news.html",
    "https://www.lorainkartplex.com/brkc.html",
    "https://www.lorainkartplex.com/getting-started.html",
    "https://www.lorainkartplex.com/race-fuel.html",
    "https://www.lorainkartplex.com/track-guidelines.html",
    "https://www.lorainkartplex.com/parking-camping.html",
    "https://www.lorainkartplex.com/hotel-lodging.html",
    "https://www.lorainkartplex.com/welcome-lorain.html",
    "https://www.lorainkartplex.com/sponsorships.html",
    "https://www.lorainkartplex.com/team.html",
    "https://www.lorainkartplex.com/careers.html",
    # Tillotson T4 kart info
    "https://tillotson.ie/t4-series/",
    # KartZone NA — on-site dealership & service center
    "https://www.kartzonena.com/pages/about-us",
    "https://www.kartzonena.com/collections/tillotson-chassis",
    "https://www.kartzonena.com/collections/compkart-chassis",
    "https://www.kartzonena.com/collections/praga-chassis",
    "https://www.kartzonena.com/collections/kart-republic-chassis",
    "https://www.kartzonena.com/collections/go-kart-racing-engines",
    "https://www.kartzonena.com/collections/go-kart-racing-tires",
    "https://www.kartzonena.com/collections/go-kart-seating",
    "https://www.kartzonena.com/collections/arai-helmets-and-accessories",
    "https://www.kartzonena.com/collections/rib-protectors",
    "https://www.kartzonena.com/collections/three-tenths-driving-suits",
    "https://www.kartzonena.com/policies/shipping-policy",
    "https://www.kartzonena.com/policies/refund-policy",
    # Great Lakes Sprint Series — hosted at KartPlex in 2026
    "https://www.greatlakessprintseries.com",
    "https://www.greatlakessprintseries.com/classes",
]

# --- AI Settings ---
AI_MODEL = "claude-haiku-4-5-20251001"
AI_MAX_TOKENS = 1024

# --- Cost Control ---
# Monthly token cap (input + output combined). Default: 5,000,000
MONTHLY_TOKEN_CAP = int(os.getenv("MONTHLY_TOKEN_CAP", "5000000"))

# --- Knowledge refresh interval (seconds) ---
KNOWLEDGE_REFRESH_INTERVAL = 86400  # Re-scrape once per day
