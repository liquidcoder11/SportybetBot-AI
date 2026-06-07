import asyncio
import aiohttp
import os
import re
import threading
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

@app.after_request
def add_ngrok_header(response):
    response.headers['ngrok-skip-browser-warning'] = 'true'
    return response

TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER       = os.getenv("TWILIO_NUMBER")
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY")
GEMINI_URL          = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
FOOTBALL_API_KEY    = os.getenv("FOOTBALL_API_KEY")
FOOTBALL_API_URL    = "https://sports.bzzoiro.com/api"
APIFOOTBALL_KEY     = os.getenv("APIFOOTBALL_KEY")
APIFOOTBALL_URL     = "https://v3.football.api-sports.io"

SPORTYBET_COOKIES = "_ntes_nnid=c0a483ee57ef99619953b430898d0691; device-id=9d5a43cd-d116-4d75-9170-b7af46384a9a; sb_country=ng; deviceId=260607231024bdid03738532; usrId=260607231024pdid03738533"

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
user_history = {}
user_code_data = {}  # Store raw selections per user

SYSTEM_PROMPT = """You are SportyBot AI, an elite sports betting analyst for Nigerian bettors. You have access to real live football data including fixtures, odds, form, and head to head stats.

CRITICAL FORMATTING RULES - WHATSAPP ONLY:
- NEVER use markdown tables (no | pipes)
- NEVER use ### or ## or # headers
- NEVER use ** bold markdown
- Only use plain text, bullet points and emojis
- Use dashes --- as dividers

CRITICAL BOOKING CODE RULES:
- NEVER invent or generate fake booking codes
- When trimming or splitting, you MUST respond with a special JSON block so the bot can generate a real code
- Always use ONLY the real teams and games from [BOOKING CODE DATA]
- NEVER swap or replace teams with different ones

FOR TRIM REQUEST:
When user asks to trim a ticket, respond in this EXACT format:

✅ Ticket Trimmed!

Original: X games | Xx odds
Trimmed: Y games | Yx odds

---

1️⃣ Home vs Away
🌍 Tournament
📈 Market - Pick
💰 Odds: 1.xx

[continue for each kept game]

---

📌 Generating your new SportyBet code...

KEPT_GAMES:[1,2,3,4,5]

The KEPT_GAMES line must list the game numbers from [BOOKING CODE DATA] to keep.

FOR SPLIT REQUEST:
When user asks to split a ticket, respond in this EXACT format for each slip:

✅ Ticket Split into X slips!

🎟 Slip 1 - Y games
1️⃣ Home vs Away
🌍 Tournament
📈 Market - Pick
💰 Odds: 1.xx

📌 Generating Slip 1 code...

SLIP_1_GAMES:[1,2,3,4,5]

🎟 Slip 2 - Y games
[games for slip 2]

SLIP_2_GAMES:[6,7,8,9,10]

FOR ACCUMULATOR:
✅ Accumulator Ready!

📊 Combined Odds: 112.4x
🎮 Total Games: 7

---

1️⃣ Flamengo vs Coritiba
🌍 Brazil - Brasileirao Serie A
📈 Market: 1X2
🎯 Pick: Flamengo Win
💰 Odds: 1.34
✅ Why: Flamengo are dominant at home

FOR TIPS:
🎯 Top 5 Safe Picks Today

---

1️⃣ Man City vs Arsenal
🌍 England - Premier League
📈 Market: Both Teams Score
🎯 Pick: BTTS Yes
💰 Odds: 1.80
🔥 Confidence: High
✅ Why: Both teams average 2+ goals per game

FOR BANKER:
🔒 Banker of the Day

---

⚽ Match: PSV vs Ajax
🌍 Netherlands - Eredivisie
📈 Market: Over 2.5 Goals
🎯 Pick: Over 2.5
💰 Odds: 1.65
🔥 Confidence: Very High

FOR H2H AND FORM:
📊 Match Analysis: Arsenal vs Chelsea

🔵 Arsenal - Last 5 Matches:
W | 2025-05-25: Southampton 1-2 Arsenal
Record: W2 D2 L1
Goals: Scored 7 | Conceded 6

⚔️ Head to Head:
2024-09-22: Arsenal 2-2 Chelsea

🎯 Prediction:
AI analysis and pick here

RULES:
- Always use this exact format
- Never write long paragraphs
- Include country and league for every match
- NEVER generate fake booking codes
- Keep responses under 600 words
- Consider ALL markets: BTTS, Over/Under, corners, cards, HT, Double Chance
- Always remember conversation history for follow-ups
- When given real form and H2H data use it for accurate predictions
- Always use ONLY the real teams and games from [BOOKING CODE DATA]"""


async def fetch_football(endpoint, params=""):
    url = FOOTBALL_API_URL + endpoint + ("?" + params if params else "")
    headers = {"Authorization": "Token " + FOOTBALL_API_KEY, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    return data.get("results", data.get("response", []))
                return data if isinstance(data, list) else []
    except Exception:
        return []


async def apifootball_get(endpoint, params=""):
    url = APIFOOTBALL_URL + endpoint + ("?" + params if params else "")
    headers = {"x-apisports-key": APIFOOTBALL_KEY, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
                return data.get("response", [])
    except Exception:
        return []


async def get_team_id(team_name):
    teams = await apifootball_get("/teams", "name=" + team_name.replace(" ", "+"))
    priority = ["England", "Spain", "Italy", "Germany", "France", "Nigeria", "Ghana", "Cameroon", "Morocco"]
    for t in teams:
        if t["team"]["country"] in priority:
            return t["team"]["id"], t["team"]["name"]
    if teams:
        return teams[0]["team"]["id"], teams[0]["team"]["name"]
    return None, None


async def get_team_form(team_id, team_name):
    season = datetime.now().year if datetime.now().month >= 8 else datetime.now().year - 1
    fixtures = await apifootball_get("/fixtures", "team=" + str(team_id) + "&season=" + str(season))
    completed = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
    last5 = completed[-5:]
    if not last5:
        return team_name + " - No recent matches found"
    lines = [team_name + " - Last 5 Matches:"]
    wins = draws = losses = goals_for = goals_against = 0
    for f in last5:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        hg = f["goals"]["home"] or 0
        ag = f["goals"]["away"] or 0
        date = f["fixture"]["date"][:10]
        is_home = home == team_name
        gf, ga = (hg, ag) if is_home else (ag, hg)
        if gf > ga:
            result = "W"
            wins += 1
        elif gf == ga:
            result = "D"
            draws += 1
        else:
            result = "L"
            losses += 1
        goals_for += gf
        goals_against += ga
        lines.append(result + " | " + date + ": " + home + " " + str(hg) + "-" + str(ag) + " " + away)
    lines.append("Record: W" + str(wins) + " D" + str(draws) + " L" + str(losses))
    lines.append("Goals: Scored " + str(goals_for) + " | Conceded " + str(goals_against))
    return "\n".join(lines)


async def get_h2h(team1_id, team2_id, team1_name, team2_name):
    season = datetime.now().year if datetime.now().month >= 8 else datetime.now().year - 1
    fixtures = await apifootball_get("/fixtures/headtohead", "h2h=" + str(team1_id) + "-" + str(team2_id) + "&season=" + str(season))
    if not fixtures:
        return "No H2H data found for this season"
    lines = ["Head to Head (" + team1_name + " vs " + team2_name + "):"]
    t1_wins = t2_wins = draws = 0
    for f in fixtures:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        hg = f["goals"]["home"] or 0
        ag = f["goals"]["away"] or 0
        date = f["fixture"]["date"][:10]
        lines.append(date + ": " + home + " " + str(hg) + "-" + str(ag) + " " + away)
        if hg > ag:
            if home == team1_name:
                t1_wins += 1
            else:
                t2_wins += 1
        elif hg == ag:
            draws += 1
        else:
            if away == team1_name:
                t1_wins += 1
            else:
                t2_wins += 1
    lines.append(team1_name + " wins: " + str(t1_wins))
    lines.append(team2_name + " wins: " + str(t2_wins))
    lines.append("Draws: " + str(draws))
    return "\n".join(lines)


async def get_advanced_stats(home_team, away_team):
    home_id, home_name = await get_team_id(home_team)
    away_id, away_name = await get_team_id(away_team)
    if not home_id or not away_id:
        return ""
    results = await asyncio.gather(
        get_team_form(home_id, home_name),
        get_team_form(away_id, away_name),
        get_h2h(home_id, away_id, home_name, away_name)
    )
    return "\n\n".join(results)


async def get_fixtures_by_date(date_str, label=""):
    fixtures = await fetch_football("/events/", "date=" + date_str)
    if not fixtures:
        return "No fixtures found for " + (label or date_str)
    shown = []
    for f in fixtures:
        home = f.get("home_team", "?")
        away = f.get("away_team", "?")
        league_info = f.get("league", {})
        league = league_info.get("name", "?") if isinstance(league_info, dict) else str(league_info)
        country = league_info.get("country", "") if isinstance(league_info, dict) else ""
        odds_h = f.get("odds_home", "?")
        odds_d = f.get("odds_draw", "?")
        odds_a = f.get("odds_away", "?")
        shown.append(country + " - " + league + ": " + home + " vs " + away + " | H=" + str(odds_h) + " D=" + str(odds_d) + " A=" + str(odds_a))
    return "Fixtures for " + (label or date_str) + " (" + str(len(shown)) + " matches):\n\n" + "\n".join(shown[:20])


async def get_todays_fixtures():
    return await get_fixtures_by_date(datetime.now().strftime("%Y-%m-%d"), "Today")


async def get_tomorrows_fixtures():
    return await get_fixtures_by_date((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"), "Tomorrow")


async def ask_ai(user_message, history):
    contents = []
    contents.append({"role": "user", "parts": [{"text": SYSTEM_PROMPT}]})
    contents.append({"role": "model", "parts": [{"text": "Understood! I am SportyBot AI, ready to help."}]})
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.7}}
    url = GEMINI_URL + "?key=" + GEMINI_API_KEY
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    data = await resp.json(content_type=None)
                    if "candidates" in data:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    elif "error" in data:
                        err = data["error"].get("message", "Unknown")
                        if "high demand" in err.lower() or "overloaded" in err.lower() or "quota" in err.lower():
                            await asyncio.sleep(4)
                            continue
                        return "AI error: " + err
                    return "Sorry I could not generate a response. Please try again."
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(4)
            else:
                return "AI error: " + str(e)
    return "Gemini is currently busy. Please try again in a moment."


async def create_sportybet_code(selections):
    """Create a real SportyBet booking code from selections array"""
    url = "https://www.sportybet.com/api/ng/orders/share"
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": "https://www.sportybet.com/ng/",
        "Cookie": SPORTYBET_COOKIES
    }
    payload = {
        "selections": selections,
        "stake": 100000
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=hdrs, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
                if data.get("bizCode") == 10000:
                    return data.get("data", {}).get("shareCode", "")
        return ""
    except Exception:
        return ""


async def try_fetch_sportybet(code):
    url = "https://www.sportybet.com/api/ng/orders/share/" + code.upper()
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sportybet.com/ng/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return "", []
                data = await resp.json(content_type=None)

        ticket_data = data.get("data", {})
        outcomes = ticket_data.get("outcomes", [])
        ticket = ticket_data.get("ticket", {})
        raw_selections = ticket.get("selections", [])
        display_odds = ticket.get("displayTotalOdds", "?")

        if outcomes:
            lines = ["Booking Code: " + code.upper(), "Bookie: SportyBet", "Total games: " + str(len(outcomes)), "", "Selections:"]
            for i, outcome in enumerate(outcomes, 1):
                home = outcome.get("homeTeamName", "?")
                away = outcome.get("awayTeamName", "?")
                tournament = outcome.get("sport", {}).get("category", {}).get("tournament", {}).get("name", "")
                markets = outcome.get("markets", [])
                market_name = ""
                pick_name = ""
                pick_odds = "?"
                if markets:
                    m = markets[0]
                    market_name = m.get("desc", "")
                    outcomes_list = m.get("outcomes", [])
                    if outcomes_list:
                        pick_name = outcomes_list[0].get("desc", "")
                        pick_odds = str(outcomes_list[0].get("odds", "?"))
                lines.append(str(i) + ". " + home + " vs " + away + " | " + tournament + " | " + market_name + " - " + pick_name + " @ " + pick_odds)
            lines.append("Combined Odds: " + display_odds + "x")
            return "\n".join(lines), raw_selections
        return "", []
    except Exception:
        return "", []


async def try_fetch_bet9ja(code):
    url = "https://coupon.bet9ja.com/desktop/feapi/CouponAjax/GetBookABetCouponV2?couponCode=" + code.upper()
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.bet9ja.com/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return "", []
                data = await resp.json(content_type=None)
        if data.get("R") != "OK":
            return "", []
        outcomes = data.get("D", {}).get("O", {})
        if not outcomes:
            return "", []
        lines = ["Booking Code: " + code.upper(), "Bookie: Bet9ja", "Total games: " + str(len(outcomes)), "", "Selections:"]
        total_odds = 1.0
        for i, (key, bet) in enumerate(outcomes.items(), 1):
            odds = float(bet.get("OD", 1))
            total_odds *= odds
            name = bet.get("E_NAME", "?")
            market = bet.get("MN", "")
            pick = bet.get("ON", "")
            lines.append(str(i) + ". " + name + " | " + market + " - " + pick + " @ " + str(odds))
        lines.append("Combined Odds: " + str(round(total_odds, 2)) + "x")
        return "\n".join(lines), []
    except Exception:
        return "", []


async def try_fetch_betking(code):
    url = "https://www.betking.com/api/sports/v1/bet/Booked/" + code.upper() + "/en"
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.betking.com/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return "", []
                data = await resp.json(content_type=None)
        if data.get("ResponseStatus") != 1:
            return "", []
        coupon = data.get("BookedCoupon", {})
        bets = coupon.get("Bets", [])
        if not bets:
            return "", []
        lines = ["Booking Code: " + code.upper(), "Bookie: BetKing", "Total games: " + str(len(bets)), "", "Selections:"]
        total_odds = 1.0
        for i, bet in enumerate(bets, 1):
            odds = float(bet.get("Price", 1))
            total_odds *= odds
            home = bet.get("HomeTeam", "?")
            away = bet.get("AwayTeam", "?")
            market = bet.get("MarketName", "")
            pick = bet.get("SelectionName", "")
            lines.append(str(i) + ". " + home + " vs " + away + " | " + market + " - " + pick + " @ " + str(odds))
        lines.append("Combined Odds: " + str(round(total_odds, 2)) + "x")
        return "\n".join(lines), []
    except Exception:
        return "", []


async def try_fetch_betpawa(code):
    url = "https://www.betpawa.ng/api/sportsbook/v3/booking-number/" + code.upper()
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        "Accept": "application/json",
        "X-Pawa-Brand": "betpawa-nigeria",
        "X-Pawa-Language": "en",
        "deviceType": "web",
        "Referer": "https://www.betpawa.ng/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return "", []
                data = await resp.json(content_type=None)
        items = data.get("items", [])
        if not items:
            return "", []
        lines = ["Booking Code: " + code.upper(), "Bookie: Betpawa", "Total games: " + str(len(items)), "", "Selections:"]
        total_odds = 1.0
        for i, item in enumerate(items, 1):
            odds = float(item.get("odds", 1))
            total_odds *= odds
            participants = item.get("eventInfo", {}).get("participants", [])
            home = participants[0]["name"] if len(participants) > 0 else "?"
            away = participants[1]["name"] if len(participants) > 1 else "?"
            market = item.get("marketName", "")
            pick = item.get("outcomeName", "")
            lines.append(str(i) + ". " + home + " vs " + away + " | " + market + " - " + pick + " @ " + str(odds))
        lines.append("Combined Odds: " + str(round(total_odds, 2)) + "x")
        return "\n".join(lines), []
    except Exception:
        return "", []


async def try_fetch_footballcom(code):
    url = "https://www.football.com/api/ng/orders/share/" + code.upper()
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.football.com/"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=hdrs, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return "", []
                data = await resp.json(content_type=None)
        ticket_data = data.get("data", {})
        outcomes = ticket_data.get("outcomes", [])
        ticket = ticket_data.get("ticket", {})
        raw_selections = ticket.get("selections", [])
        display_odds = ticket.get("displayTotalOdds", "?")
        if outcomes:
            lines = ["Booking Code: " + code.upper(), "Bookie: Football.com", "Total games: " + str(len(outcomes)), "", "Selections:"]
            for i, outcome in enumerate(outcomes, 1):
                home = outcome.get("homeTeamName", "?")
                away = outcome.get("awayTeamName", "?")
                tournament = outcome.get("sport", {}).get("category", {}).get("tournament", {}).get("name", "")
                markets = outcome.get("markets", [])
                market_name = pick_name = pick_odds = "?"
                if markets:
                    m = markets[0]
                    market_name = m.get("desc", "")
                    outcomes_list = m.get("outcomes", [])
                    if outcomes_list:
                        pick_name = outcomes_list[0].get("desc", "")
                        pick_odds = str(outcomes_list[0].get("odds", "?"))
                lines.append(str(i) + ". " + home + " vs " + away + " | " + tournament + " | " + market_name + " - " + pick_name + " @ " + pick_odds)
            lines.append("Combined Odds: " + display_odds + "x")
            return "\n".join(lines), raw_selections
        return "", []
    except Exception:
        return "", []


async def fetch_booking_code(code):
    fetchers = [
        try_fetch_sportybet,
        try_fetch_bet9ja,
        try_fetch_betking,
        try_fetch_betpawa,
        try_fetch_footballcom,
    ]
    for fetcher in fetchers:
        result, selections = await fetcher(code)
        if result:
            return result, selections
    return "", []


def extract_kept_games(reply, pattern):
    """Extract game numbers from Gemini reply e.g. KEPT_GAMES:[1,2,3]"""
    match = re.search(pattern + r':\[([0-9,\s]+)\]', reply)
    if match:
        nums = [int(x.strip()) for x in match.group(1).split(",") if x.strip().isdigit()]
        return nums
    return []


def extract_code(text):
    stopwords = {
        "SPLIT","INTO","TICKET","TICKETS","SLIPS","ODDS","GAMES","MAKE","THIS",
        "LOWER","TRIM","PICK","FROM","ANALYZE","CONVERT","SAFE","HAVE","CODE",
        "HELP","START","CLEAR","BETKING","SPORTYBET","PARIPESA","BETPAWA",
        "BETANO","1XBET","TIPS","TODAY","PREDICT","WHAT","WHO","WINS","BEST",
        "LIVE","SCORES","TOMORROW","FIXTURES","BANKER","BUILD","OVER","UNDER",
        "AROUND","ABOUT","REDUCE","CHANGE","GIVE","SEND","SHOW","LIST"
    }
    for m in re.findall(r"\b[A-Z0-9]{5,8}\b", text.upper()):
        if m not in stopwords and not m.isdigit():
            return m
    return None


async def process_message(user_number, text):
    if user_number not in user_history:
        user_history[user_number] = []
    history = user_history[user_number]
    text_lower = text.lower().strip()
    extra_context = ""

    if text_lower in ["hi", "hello", "hey", "start", "menu"]:
        return (
            "👋 Welcome to SportyBot AI!\n\n"
            "SportyBot AI helps you do all your betting work right here in chat — no more jumping between bookmakers, stats sites, and screenshots.\n\n"
            "---\n\n"
            "🤖 What I can do for you:\n\n"
            "• Analyze & predict matches with real stats\n"
            "• Build accumulators and safe picks\n"
            "• Give you a banker of the day\n"
            "• Split one slip into smaller slips\n"
            "• Trim a ticket down to your target odds\n"
            "• Convert tickets between bookmakers\n"
            "• Read and analyze any booking code\n"
            "• Research matches and suggest best picks\n\n"
            "---\n\n"
            "🚀 Best way to start:\n\n"
            "Just send any one of these:\n"
            "• A booking code (SportyBet, Bet9ja, BetKing, Betpawa)\n"
            "• A match you want analyzed\n"
            "• A simple instruction\n\n"
            "---\n\n"
            "💡 Try one of these:\n\n"
            "• Split this code into 2 slips: ABC123\n"
            "• Trim this ticket to around 20 odds\n"
            "• Convert this Bet9ja code to SportyBet\n"
            "• Analyze Arsenal vs Chelsea\n"
            "• Give me 5 safe picks today\n"
            "• Build me a 100 odds accumulator\n\n"
            "---\n\n"
            "📌 Tips for best results:\n\n"
            "• Include your target odds when trimming\n"
            "• Send the code together with your instruction\n"
            "• Be specific about what you want changed\n\n"
            "Type anything to get started! ⚽🔥"
        )

    if text_lower == "clear":
        user_history[user_number] = []
        user_code_data.pop(user_number, None)
        return "Chat history cleared!"

    if text_lower.startswith("h2h ") and " vs " in text_lower:
        teams = text_lower[4:].strip()
        parts = teams.split(" vs ")
        home = parts[0].strip().title()
        away = parts[1].strip().title()
        stats = await get_advanced_stats(home, away)
        if stats:
            prompt = "Real stats for " + home + " vs " + away + ":\n\n" + stats + "\n\nGive detailed prediction with form analysis, H2H, best market, confidence level. Format with emojis. No tables."
            reply = await ask_ai(prompt, history)
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            if len(history) > 20:
                user_history[user_number] = history[-20:]
            return reply
        return "Could not find stats for " + home + " vs " + away + ". Check team names."

    sports_keywords = ["predict", "tip", "pick", "safe", "sure", "banker", "accumulator",
                       "acca", "today", "odds", "bet", "game", "match", "fixture",
                       "win", "score", "goal", "over", "under", "btts", "double chance",
                       "top", "best", "recommend", "analysis", "build", "create", "100",
                       "trim", "split", "reduce", "convert", "change"]

    needs_fixtures = any(k in text_lower for k in sports_keywords)
    code = extract_code(text)

    if "tomorrow" in text_lower:
        fixtures = await get_tomorrows_fixtures()
        extra_context = "\n\n[TOMORROW FIXTURES]\n" + fixtures + "\n[END]"
    elif " vs " in text_lower and not code:
        words = text.split()
        home = away = ""
        for i, w in enumerate(words):
            if w.lower() == "vs":
                home = " ".join(words[max(0, i-2):i]).title()
                away = " ".join(words[i+1:i+3]).title()
                break
        if home and away:
            stats = await get_advanced_stats(home, away)
            if stats:
                extra_context = "\n\n[REAL STATS FOR " + home + " vs " + away + "]\n" + stats + "\n[END]"
            fixtures = await get_todays_fixtures()
            extra_context += "\n\n[TODAY FIXTURES]\n" + fixtures[:1500] + "\n[END]"
    elif code:
        fetch_data, raw_selections = await fetch_booking_code(code)
        if fetch_data:
            extra_context = "\n\n[BOOKING CODE DATA]\n" + fetch_data + "\n[END]"
            if raw_selections:
                user_code_data[user_number] = raw_selections
            print("Fetched code data for: " + code)
        else:
            extra_context = "\n\n[Could not fetch code " + code + ". Tell user code may be expired and still help.]"
            print("Could not fetch code: " + code)
    elif needs_fixtures:
        fixtures = await get_todays_fixtures()
        extra_context = "\n\n[TODAY FIXTURES WITH ODDS]\n" + fixtures[:2500] + "\n[END]"

    reply = await ask_ai(text + extra_context, history)

    # Check if Gemini wants to generate a real SportyBet code
    raw_selections = user_code_data.get(user_number, [])
    if raw_selections and "KEPT_GAMES:" in reply:
        kept_nums = extract_kept_games(reply, "KEPT_GAMES")
        if kept_nums:
            kept_selections = [raw_selections[i-1] for i in kept_nums if 0 < i <= len(raw_selections)]
            if kept_selections:
                new_code = await create_sportybet_code(kept_selections)
                reply = re.sub(r'KEPT_GAMES:\[[0-9,\s]+\]', '', reply).strip()
                if new_code:
                    reply += "\n\n📌 Your new SportyBet code: *" + new_code + "*"
                else:
                    reply += "\n\n⚠️ Could not generate code automatically. Please book these games manually on SportyBet."

    # Handle split with multiple slips
    if raw_selections and "SLIP_1_GAMES:" in reply:
        slip_num = 1
        while "SLIP_" + str(slip_num) + "_GAMES:" in reply:
            kept_nums = extract_kept_games(reply, "SLIP_" + str(slip_num) + "_GAMES")
            if kept_nums:
                kept_selections = [raw_selections[i-1] for i in kept_nums if 0 < i <= len(raw_selections)]
                if kept_selections:
                    new_code = await create_sportybet_code(kept_selections)
                    pattern = "SLIP_" + str(slip_num) + r"_GAMES:\[[0-9,\s]+\]"
                    if new_code:
                        reply = re.sub(pattern, "📌 Slip " + str(slip_num) + " Code: *" + new_code + "*", reply)
                    else:
                        reply = re.sub(pattern, "⚠️ Could not generate slip " + str(slip_num) + " code automatically.", reply)
            slip_num += 1

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 20:
        user_history[user_number] = history[-20:]
    return reply


def send_whatsapp_message(to, message):
    try:
        from_num = TWILIO_NUMBER if TWILIO_NUMBER.startswith('whatsapp:') else "whatsapp:" + TWILIO_NUMBER
        to_num = to if to.startswith('whatsapp:') else "whatsapp:" + to
        if len(message) > 1500:
            parts = [message[i:i+1500] for i in range(0, len(message), 1500)]
            for part in parts:
                twilio_client.messages.create(from_=from_num, body=part, to=to_num)
        else:
            twilio_client.messages.create(from_=from_num, body=message, to=to_num)
    except Exception as e:
        print("\n TWILIO SEND ERROR: " + str(e) + "\n")


@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number  = request.values.get("From", "")
    print("Message from " + from_number + ": " + incoming_msg)

    def process_and_reply():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            reply = loop.run_until_complete(process_message(from_number, incoming_msg))
            loop.close()
            send_whatsapp_message(from_number, reply)
        except Exception as e:
            print("THREAD ERROR: " + str(e))
            send_whatsapp_message(from_number, "Sorry something went wrong. Please try again.")

    threading.Thread(target=process_and_reply).start()
    return str(MessagingResponse())


@app.route("/", methods=["GET"])
def home():
    return "SportyBot AI WhatsApp is running!"


if __name__ == "__main__":
    print("Starting SportyBot AI WhatsApp with Advanced Stats...")
    print("Webhook: https://lunacy-deploy-attic.ngrok-free.dev/webhook")
    app.run(host="0.0.0.0", port=5000, debug=False)