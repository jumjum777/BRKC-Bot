"""Website scraper and knowledge base for BRKC Bot.

Scrapes lorainkartplex.com and blackriverkartclub.com to build
a knowledge base that Claude can use to answer questions.
Also loads historical race results data from CSV files.
"""

import asyncio
import csv
import logging
import time
from collections import defaultdict
from io import StringIO
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

from .config import KNOWLEDGE_URLS, KNOWLEDGE_REFRESH_INTERVAL

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"


CUSTOM_FACTS_FILE = DATA_DIR / "custom_facts.txt"


class KnowledgeBase:
    """Scrapes and stores website content for AI context."""

    def __init__(self):
        self.pages: dict[str, str] = {}
        self.race_summary: str = ""
        self.results_by_driver: dict[str, str] = {}
        self.laptimes_by_driver: dict[str, str] = {}
        self.all_drivers: list[str] = []
        self.last_refresh: float = 0
        self.custom_facts: list[str] = []
        self._load_race_data()
        self._load_custom_facts()

    # --- Custom facts management ---

    def _load_custom_facts(self):
        """Load custom facts from the text file."""
        if CUSTOM_FACTS_FILE.exists():
            lines = CUSTOM_FACTS_FILE.read_text(encoding="utf-8").strip().splitlines()
            self.custom_facts = [line for line in lines if line.strip()]
            logger.info("Loaded %d custom facts", len(self.custom_facts))
        else:
            self.custom_facts = []

    def _save_custom_facts(self):
        """Save custom facts to the text file."""
        CUSTOM_FACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CUSTOM_FACTS_FILE.write_text(
            "\n".join(self.custom_facts) + ("\n" if self.custom_facts else ""),
            encoding="utf-8",
        )

    def add_fact(self, fact: str) -> int:
        """Add a custom fact. Returns the line number (1-based)."""
        self.custom_facts.append(fact.strip())
        self._save_custom_facts()
        return len(self.custom_facts)

    def remove_fact(self, number: int) -> str:
        """Remove a fact by 1-based line number. Returns the removed fact."""
        if number < 1 or number > len(self.custom_facts):
            raise IndexError(f"No fact at line {number}. Valid range: 1-{len(self.custom_facts)}")
        removed = self.custom_facts.pop(number - 1)
        self._save_custom_facts()
        return removed

    def list_facts(self) -> list[tuple[int, str]]:
        """Return all facts as (number, text) tuples."""
        return [(i + 1, fact) for i, fact in enumerate(self.custom_facts)]

    def _load_race_data(self):
        """Load race results and lap times, build summary and per-driver indexes."""
        results_file = DATA_DIR / "brkc-results.csv"
        laptimes_file = DATA_DIR / "brkc-laptimes.csv"

        # --- Parse results ---
        driver_results: dict[str, list[str]] = defaultdict(list)
        round_winners: dict[str, dict[str, str]] = defaultdict(dict)  # {round: {class: winner}}
        class_drivers: dict[str, set[str]] = defaultdict(set)
        all_drivers_set: set[str] = set()

        if results_file.exists():
            raw = results_file.read_text(encoding="utf-8")
            reader = csv.DictReader(StringIO(raw))
            header = "Round,Date,Class,Session,Pos,Kart#,Driver,Class Code,Laps,Avg Speed,Gap,Best Lap,Best On Lap,Team"

            for row in reader:
                driver = row.get("Driver", "").strip()
                if not driver:
                    continue
                all_drivers_set.add(driver)

                cls = row.get("Class", "")
                session = row.get("Session", "")
                rnd = row.get("Round", "")
                pos = row.get("Pos", "")

                class_drivers[cls].add(driver)

                # Skip practice sessions to save tokens
                if session == "P":
                    continue

                line = ",".join(row.get(k, "") for k in header.split(","))
                driver_results[driver.lower()].append(line)

                # Track Final winners
                if session == "F" and pos == "1":
                    round_winners[rnd][cls] = driver

            logger.info("Loaded race results: %d drivers", len(all_drivers_set))
        else:
            logger.warning("Race results file not found: %s", results_file)

        # --- Parse lap times ---
        driver_laps: dict[str, list[str]] = defaultdict(list)

        if laptimes_file.exists():
            raw = laptimes_file.read_text(encoding="utf-8")
            for line in raw.strip().splitlines()[1:]:  # skip header
                parts = line.split(",")
                if len(parts) >= 5:
                    session = parts[3].strip()
                    if session == "P":
                        continue
                    driver = parts[4].strip()
                    if driver:
                        driver_laps[driver.lower()].append(line)
            logger.info("Loaded lap times for %d drivers", len(driver_laps))
        else:
            logger.warning("Lap times file not found: %s", laptimes_file)

        # --- Build compact summary ---
        summary_lines = ["=== 2025 BRKC SEASON SUMMARY ===\n"]

        summary_lines.append("CLASSES AND DRIVERS:")
        for cls, drivers in sorted(class_drivers.items()):
            summary_lines.append(f"  {cls}: {', '.join(sorted(drivers))}")

        summary_lines.append("\nFINAL WINNERS BY ROUND:")
        for rnd in sorted(round_winners.keys()):
            summary_lines.append(f"  {rnd}:")
            for cls, winner in sorted(round_winners[rnd].items()):
                summary_lines.append(f"    {cls}: {winner}")

        summary_lines.append(f"\nTotal drivers in data: {len(all_drivers_set)}")
        summary_lines.append(f"Rounds covered: {', '.join(sorted(set(r for r in round_winners.keys())))}")

        self.race_summary = "\n".join(summary_lines)
        self.results_by_driver = {k: "\n".join(v) for k, v in driver_results.items()}
        self.laptimes_by_driver = {k: "\n".join(v) for k, v in driver_laps.items()}
        self.all_drivers = sorted(all_drivers_set)

    def find_relevant_drivers(self, question: str) -> list[str]:
        """Find driver names mentioned in the question."""
        lower = question.lower()
        found = []
        for driver in self.all_drivers:
            # Match on last name or full name
            name_parts = driver.lower().split()
            if driver.lower() in lower:
                found.append(driver)
            elif any(part in lower.split() for part in name_parts if len(part) > 2):
                found.append(driver)
        return found

    async def refresh(self):
        """Scrape all configured URLs and store their text content."""
        logger.info("Refreshing knowledge base from websites...")
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_page(session, url) for url in KNOWLEDGE_URLS]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for url, result in zip(KNOWLEDGE_URLS, results):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch %s: %s", url, result)
            else:
                self.pages[url] = result

        self.last_refresh = time.time()
        logger.info("Knowledge base loaded: %d pages", len(self.pages))

    async def _fetch_page(self, session: aiohttp.ClientSession, url: str) -> str:
        """Fetch a single page and extract its text content."""
        headers = {"User-Agent": "BRKC-Bot/1.0 (Discord bot for Black River Kart Club)"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def needs_refresh(self) -> bool:
        """Check if the knowledge base is stale."""
        return time.time() - self.last_refresh > KNOWLEDGE_REFRESH_INTERVAL

    # Map keywords to URL patterns for smart page selection
    PAGE_KEYWORDS = {
        "garage": ["garages"],
        "storage": ["garages"],
        "rent": ["rentals"],
        "rental": ["rentals"],
        "simulator": ["simulators"],
        "sim racing": ["simulators"],
        "group": ["group-events"],
        "party": ["group-events"],
        "corporate": ["group-events"],
        "event": ["events", "group-events"],
        "fuel": ["race-fuel"],
        "gas": ["race-fuel"],
        "track": ["track-guidelines", "racing"],
        "guideline": ["track-guidelines"],
        "rule": ["track-guidelines"],
        "parking": ["parking-camping"],
        "camping": ["parking-camping"],
        "hotel": ["hotel-lodging"],
        "lodging": ["hotel-lodging"],
        "stay": ["hotel-lodging"],
        "team": ["team"],
        "staff": ["team"],
        "career": ["careers"],
        "job": ["careers"],
        "sponsor": ["sponsorships"],
        "news": ["news"],
        "tillotson": ["tillotson", "t4-series"],
        "t4": ["tillotson", "t4-series"],
        "compkart": ["compkart"],
        "praga": ["praga"],
        "kart republic": ["kart-republic"],
        "engine": ["engines"],
        "motor": ["engines"],
        "tire": ["tires"],
        "seat": ["seating"],
        "helmet": ["helmets"],
        "suit": ["driving-suits"],
        "rib": ["rib-protectors"],
        "gear": ["seating", "helmets", "rib-protectors"],
        "equipment": ["seating", "helmets", "rib-protectors"],
        "kartzone": ["kartzonena", "about-us"],
        "dealer": ["kartzonena", "about-us"],
        "buy": ["kartzonena"],
        "purchase": ["kartzonena"],
        "price": ["kartzonena"],
        "ship": ["shipping-policy"],
        "refund": ["refund-policy"],
        "return": ["refund-policy"],
        "class": ["brkc", "racing"],
        "race": ["brkc", "racing"],
        "schedule": ["brkc", "racing"],
        "membership": ["brkc"],
        "member": ["brkc"],
        "register": ["brkc"],
        "sign up": ["brkc"],
        "getting started": ["getting-started"],
        "beginner": ["getting-started"],
        "new to": ["getting-started"],
        "lorain": ["welcome-lorain"],
        "sprint series": ["greatlakessprintseries"],
        "glss": ["greatlakessprintseries"],
    }

    def _find_relevant_pages(self, question: str) -> list[str]:
        """Find which pages are relevant to the question."""
        lower = question.lower()
        relevant_patterns: set[str] = set()

        for keyword, patterns in self.PAGE_KEYWORDS.items():
            if keyword in lower:
                relevant_patterns.update(patterns)

        # Always include the homepage for general context
        relevant_urls = []
        for url in self.pages:
            is_homepage = url.rstrip("/").endswith((".com", "index.html"))
            matches = any(p in url for p in relevant_patterns)
            if is_homepage or matches:
                relevant_urls.append(url)

        # If no specific matches, include homepage + brkc page
        if not relevant_patterns:
            for url in self.pages:
                if any(p in url for p in ["lorainkartplex.com", "brkc"]):
                    if url not in relevant_urls:
                        relevant_urls.append(url)

        return relevant_urls

    def get_context(self, question: str = "") -> str:
        """Build a context string. Only includes relevant pages to save tokens."""
        sections = []

        # Custom facts first — these are owner-curated overrides/additions
        if self.custom_facts:
            facts_block = "=== CUSTOM FACTS (always treat as true) ===\n" + "\n".join(
                f"- {fact}" for fact in self.custom_facts
            )
            sections.append(facts_block)

        # Smart page selection
        if self.pages:
            relevant = self._find_relevant_pages(question) if question else list(self.pages.keys())[:3]
            for url in relevant:
                text = self.pages[url]
                truncated = text[:2000] if len(text) > 2000 else text
                sections.append(f"--- {url} ---\n{truncated}")
        else:
            sections.append("No website data available yet.")

        # Always include compact race summary
        if self.race_summary:
            sections.append(self.race_summary)

        # Include specific driver data if mentioned in the question
        if question:
            drivers = self.find_relevant_drivers(question)
            if drivers:
                header = "Round,Date,Class,Session,Pos,Kart#,Driver,Class Code,Laps,Avg Speed,Gap,Best Lap,Best On Lap,Team"
                for driver in drivers[:5]:  # Cap at 5 drivers to stay within limits
                    key = driver.lower()
                    driver_section = f"\n--- DETAILED DATA FOR: {driver} ---\n"

                    if key in self.results_by_driver:
                        driver_section += f"RESULTS ({header}):\n{self.results_by_driver[key]}\n"

                    if key in self.laptimes_by_driver:
                        driver_section += f"LAP TIMES (Round,Date,Class,Session,Driver,L1-L14):\n{self.laptimes_by_driver[key]}\n"

                    sections.append(driver_section)

        return "\n\n".join(sections)
