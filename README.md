# ⭐️ StarPost Manager

> A production-grade local web studio running on your PC to create, edit, schedule, delete, and monitor **Telegram Stars paid-photo posts** for your Telegram channel with automated delivery, blurred previews, rate-limit backoff, and financial analytics.

---

## 📋 Table of Contents
1. [Overview & Architecture](#-overview--architecture)
2. [Prerequisites](#-prerequisites)
3. [Quickstart Setup Guide](#-quickstart-setup-guide)
   - [Virtual Environment Setup (Windows, macOS, Linux)](#virtual-environment-setup)
   - [Generating the Admin Password Hash](#generating-admin-password-hash)
   - [Environment Configuration (.env)](#environment-configuration)
4. [Telegram Channel & Bot Setup](#-telegram-channel--bot-setup)
   - [Creating Bot with @BotFather](#1-creating-the-bot-with-botfather)
   - [Adding Bot as Channel Administrator](#2-adding-the-bot-as-channel-administrator)
   - [Testing Connection & Diagnosing Permissions](#3-diagnosing-channel-permissions)
5. [MongoDB Atlas Setup & IP Whitelisting](#-mongodb-atlas-setup--ip-whitelisting)
6. [Running the Application](#-running-the-application)
7. [Core Features & Telegram Rules](#-core-features--telegram-rules)
   - [Paid Media Rules (sendPaidMedia)](#paid-media-rules)
   - [In-Place Caption Edit vs. "Delete & Repost" Flow](#in-place-caption-editing-vs-delete--repost)
   - [Bulk Sequential Post Scheduling](#bulk-sequential-scheduling)
   - [Sales & Stars Financial Sync](#sales--stars-financial-sync)
8. [Hidden Text Feature (Exclusive Content for Buyers)](#-hidden-text-feature-exclusive-content-for-buyers)
   - [Telegram Platform Constraints & Facts](#telegram-platform-constraints--facts)
   - [Mode A: Locked Text Card (Channel Album)](#mode-a-locked-text-card-inside-channel)
   - [Mode B: Bot DM Delivery (Deep Link & Long Polling)](#mode-b-bot-dm-delivery-real-text-message)
   - [Enabling the Bot Delivery Listener](#enabling-the-bot-delivery-listener)
   - [Buyers Management & CSV Export](#buyers-management--csv-export)
9. [Centralized Themes](#-centralized-themes)
10. [Extensible Plugin System](#-extensible-plugin-system)
   - [Working Example: example_watermark](#working-example-example_watermark)
   - [How to Write Your Own Plugin](#how-to-write-your-own-plugin)
11. [Running Automated Tests](#-running-automated-tests)
12. [Troubleshooting & FAQs](#-troubleshooting--faqs)

---

## 🚀 Overview & Architecture

StarPost Manager adheres to clean separation of concerns:
- **FastAPI + Uvicorn**: High-performance asynchronous backend.
- **Jinja2 + HTMX + Alpine.js + Tailwind CSS**: Zero-Node-build reactive UI with glassmorphism, instant feedback, and mobile LAN responsiveness.
- **MongoDB Atlas via Motor**: Fully asynchronous database storage with indexes.
- **APScheduler**: Persistent AsyncIOScheduler reloading pending jobs across restarts.
- **httpx**: Asynchronous HTTP client wrapping Telegram Bot API with automatic HTTP 429 exponential backoff.
- **Pillow**: Automatic image validation, format normalization (JPG/PNG/WEBP), dimension scaling, and compression.

### Project Layout
```text
starpost-manager/
├── app/
│   ├── config.py                 # Pydantic v2 settings & secrets masking
│   ├── db.py                     # Motor async client & index initialization
│   ├── main.py                   # App factory, lifespan, static & media mounts
│   ├── models/                   # Pydantic data models
│   │   ├── activity.py           # Activity log records
│   │   ├── feature_flag.py       # Feature flags & plugin toggles
│   │   ├── post.py               # Paid posts schema
│   │   ├── sale.py               # Telegram Stars transaction record
│   │   ├── setting.py            # App settings model
│   │   ├── template.py           # Caption and price templates
│   │   └── user.py               # User sessions & login requests
│   ├── repositories/             # MongoDB data access layer
│   │   ├── activity.py
│   │   ├── base.py
│   │   ├── feature_flags.py
│   │   ├── posts.py
│   │   ├── sales.py
│   │   ├── settings.py
│   │   ├── templates.py
│   │   └── users.py
│   ├── services/                 # Business logic
│   │   ├── auth.py               # Session signing, bcrypt & route guards
│   │   ├── media.py              # Pillow image validation & optimization
│   │   ├── posts.py              # Publish, edit caption, repost, duplicate
│   │   ├── scheduler.py          # APScheduler management & startup job reload
│   │   ├── stars.py              # getStarTransactions sync & CSV export
│   │   └── telegram.py           # Typed Bot API client & 429 retry backoff
│   ├── routers/                  # HTTP route handlers
│   │   ├── auth.py               # Login / logout routes
│   │   ├── dashboard.py          # Dashboard home & Chart.js API
│   │   ├── posts.py              # Post CRUD, publish, bulk mode, trash
│   │   ├── schedule.py           # Pipeline, calendar, pause/resume/reschedule
│   │   ├── settings.py           # Credentials, themes, test connection
│   │   ├── stats.py              # Sales table, sync, CSV export
│   │   └── templates.py          # Caption templates
│   ├── plugins/                  # Auto-loading plugin system
│   │   ├── manager.py            # Plugin discovery & hooks engine
│   │   └── example_watermark/    # Working watermark plugin
│   │       └── plugin.py
│   ├── static/
│   │   ├── css/theme.css         # Centralized CSS variables (3 themes)
│   │   └── js/app.js             # Toasts, Telegram HTML preview formatter
│   └── templates/                # Jinja2 templates & reusable components
│       ├── base.html             # Main dark-first responsive layout
│       ├── components/           # 9 Reusable UI macros
│       ├── dashboard.html        # KPI cards, Chart.js, feed, next scheduled
│       ├── error_db.html         # Atlas connection guide
│       ├── login.html            # Authentication view
│       ├── posts/                # Create, edit, view, trash
│       ├── schedule/             # List & visual calendar
│       ├── settings/             # Settings & connection tester
│       ├── stats/                # Sales transactions
│       └── templates/            # Template library
├── media/                        # Stored photos
├── scripts/
│   └── make_password_hash.py     # Bcrypt hash generator
├── tests/                        # Full test suite (25 tests)
├── .env.example                  # Environment template
├── pytest.ini                    # Test runner configuration
├── requirements.txt              # Production dependencies
└── run.py                        # Launch server & open browser
```

---

## 📦 Prerequisites
- **Python 3.11+** installed (`python --version`)
- A **Telegram Channel** where you have Administrator rights
- A **MongoDB Atlas** cluster (free M0 tier works great) or a local MongoDB instance

---

## 🛠 Quickstart Setup Guide

### Virtual Environment Setup

#### Windows (Command Prompt / PowerShell)
```powershell
cd starpost-manager
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

#### macOS & Linux (Terminal)
```bash
cd starpost-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

### Generating Admin Password Hash
StarPost Manager uses local bcrypt authentication. Generate a secure hash with the included utility:

```bash
# Windows
.\venv\Scripts\python scripts/make_password_hash.py

# macOS / Linux
python scripts/make_password_hash.py
```
Type your desired password when prompted. The script will print the exact bcrypt hash to paste into `.env`.

---

### Environment Configuration (.env)
Copy `.env.example` to `.env`:
```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Edit `.env` with your values:
```env
# MongoDB Atlas Connection URI
MONGODB_URI=mongodb+srv://<username>:<password>@cluster0.mongodb.net/starpost?retryWrites=true&w=majority
DB_NAME=starpost

# Security
SECRET_KEY=generate-a-random-secure-string-at-least-32-chars
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=$2b$12$... # Generated with make_password_hash.py

# Telegram Bot Token & Channel (or set via Settings in Dashboard)
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRstuVWXyz
TELEGRAM_CHANNEL_ID=@mychannel # or channel ID like -1001234567890

# Preferences
APP_HOST=127.0.0.1
APP_PORT=8000
DEFAULT_TIMEZONE=UTC
DEFAULT_THEME=midnight
DEFAULT_STAR_PRICE=50
STAR_SYNC_INTERVAL_MINUTES=15
```

---

## 🤖 Telegram Channel & Bot Setup

### 1. Creating the Bot with @BotFather
1. Open Telegram and search for [`@BotFather`](https://t.me/BotFather).
2. Send `/newbot`.
3. Provide a display name (e.g. `My Channel Paid Media Bot`).
4. Provide a unique username ending in `bot` (e.g. `my_paid_photo_bot`).
5. Copy the generated HTTP API **Bot Token** (format: `1234567890:AA...`).

### 2. Adding the Bot as Channel Administrator
1. Open your Telegram Channel settings.
2. Go to **Administrators** -> **Add Administrator**.
3. Search for your bot's username and add it.
4. Ensure the bot has the following administrator rights enabled:
   - ✅ **Post Messages** (Required to send paid photos)
   - ✅ **Edit Messages of Others** (Required to update captions)
   - ✅ **Delete Messages of Others** (Required for the Delete & Repost flow and Trash)
5. Save the administrator permissions.

### 3. Diagnosing Channel Permissions
In the StarPost Manager dashboard:
1. Navigate to **Settings**.
2. Click **"Test Bot & Channel Connection"**.
3. The diagnostics modal will check:
   - Bot Token validity (`getMe`)
   - Channel accessibility (`getChatMember`)
   - Active permissions breakdown (`can_post_messages`, `can_edit_messages`, `can_delete_messages`)
   - Direct advice if any permission is missing.

---

## 🍃 MongoDB Atlas Setup & IP Whitelisting

1. Log in to [MongoDB Atlas Console](https://cloud.mongodb.com).
2. Under **Security** in the left sidebar, click **Network Access**.
3. Click **"Add IP Address"**.
4. Choose **"Add Current IP Address"** to whitelist your home/office IP (or `0.0.0.0/0` for development).
5. Under **Database**, click **Connect** -> **Drivers** (Python / Motor) and copy your connection string.
6. Replace `<password>` with your database user password and paste into `.env` as `MONGODB_URI`.

> [!NOTE]
> If your IP changes or MongoDB Atlas is unreachable, StarPost Manager displays a friendly setup guide explaining how to update your IP whitelist without crashing.

---

## 🖥 Running the Application

To launch StarPost Manager and automatically open your default browser:

```bash
# Windows
.\venv\Scripts\python run.py

# macOS & Linux
python run.py
```

The web dashboard is now live at:
👉 **http://127.0.0.1:8000**

You can also access the dashboard from your phone or tablet on the same local Wi-Fi / LAN by navigating to `http://<YOUR_PC_LOCAL_IP>:8000`.

---

## 💡 Core Features & Telegram Rules

### Paid Media Rules (`sendPaidMedia`)
- Uses official Telegram Bot API `sendPaidMedia` method.
- Supports **1 to 10 photos** per post.
- Unlocks for **1 to 10,000 Telegram Stars**.
- Non-buyers see the blurred preview generated server-side by Telegram. (Telegram does not permit custom non-blurred thumbnails for paid content).

### In-Place Caption Editing vs. "Delete & Repost"
Per Telegram Bot API specifications:
- Once a paid-media post is published, **ONLY the caption can be edited** in-place (`editMessageCaption`).
- Photos and Star prices **cannot** be altered on an existing message.
- To change photos or the price of a published post, use StarPost Manager's **"Delete and Repost"** flow:
  1. Deletes the old message from the channel (`deleteMessage`).
  2. Publishes the updated media and pricing as a fresh post (`sendPaidMedia`).

### Bulk Sequential Scheduling
Upload dozens of photos simultaneously:
1. Toggle **Bulk Mode** on the Create Post page.
2. Choose your base caption, preset star price, and start date/time.
3. Choose an interval (e.g. `3.0` hours).
4. StarPost Manager will automatically split the photos into individual scheduled posts spaced 3 hours apart.

### Automatic Retries & 429 Handling
- If Telegram rate limits a request (`HTTP 429`), StarPost Manager reads `retry_after` and sleeps before retrying.
- If a scheduled post fails due to network or API errors, APScheduler retries up to **3 times with exponential backoff** (2 min, 5 min, 15 min) and logs clear failure reasons in the UI.

### Sales & Stars Financial Sync
- Periodic background job calls `getStarTransactions` to capture every unlock purchase.
- Sales table displays user ID, buyer username, date, star revenue, and matched post.
- Instant **CSV Export** for accounting and taxes.

---

## 🔒 Hidden Text Feature (Exclusive Content for Buyers)

StarPost Manager supports exclusive **Hidden Text** (up to 1,000 characters) revealed only to users who unlock your post with Telegram Stars. Perfect for secret download links, promo codes, private community invite links, premium commentary, or bonus keys.

```text
Post Flow Comparison:
[Mode A: Card]  Channel Post (Paid Album: Photos + Locked Card) ──> User pays in channel ──> Telegram unblurs card in-place
[Mode B: Bot]   Channel Post (Blurred Teaser + "Unlock" Button) ──> User clicks deep link ──> Bot delivers in Private DM
```

### Telegram Platform Constraints & Facts
StarPost Manager's architecture strictly adheres to Telegram Bot API rules without fragile workarounds:
1. **Channel Paid Media Limitation:** When paid media (`sendPaidMedia`) is posted into a **channel**, Telegram displays the caption to everyone and **never** emits a `purchased_paid_media` update to the bot. Therefore, a bot cannot deliver a follow-up message for channel purchases.
2. **Paid Media Immutability:** Once paid media is published, photos and price cannot be edited in-place; only the caption can be updated via `editMessageCaption`.
3. **Private Chat Payloads:** In private chats, `sendPaidMedia` accepts a `payload` parameter (0–128 bytes) which Telegram returns in the `purchased_paid_media` update upon successful payment.
4. **24-Hour Update Retention:** Telegram stores undelivered bot updates for up to **24 hours**. If StarPost Manager is temporarily shut down, queued purchase events are processed when restarted.

---

### Mode A: Locked Text Card (Inside Channel)
- **How it works:** StarPost Manager uses Pillow to render your hidden text onto a high-resolution graphic card matching the dimensions of your first uploaded photo.
- **Card Styles:** Choose from:
  - *Blurred Photo Background:* Darkened, Gaussian-blurred copy of your primary image.
  - *Gradient:* Elegant obsidian-indigo gradient backdrop.
  - *Solid:* Clean dark charcoal background.
- **Bundled Fonts:** High-legibility TrueType fonts are bundled under `app/static/fonts/`:
  - `DejaVuSans.ttf`
  - `NotoSans-Regular.ttf`
  > [!NOTE]
  > Complex writing systems (such as Bengali, Arabic, or Devanagari) require Pillow built with `libraqm` for advanced glyph shaping and ligation. If `libraqm` is not installed on the host OS, Pillow falls back gracefully to standard rendering.
- **Album Integration:** The rendered text card is appended as the **last item** of the channel's `sendPaidMedia` album (supporting up to 9 real photos + 1 card). Because Telegram server-side blurs all paid media for non-buyers, the text card is obscured until purchased.
- **Editing:** Marked internally with `is_text_card = True` and excluded from UI photo counts. Because Telegram does not permit swapping media items on published posts, changing hidden text in Mode A uses the **"Delete and Repost"** flow (automatically regenerating the card).
- **Live Preview:** Create and Edit pages include a live two-pane preview showing *"What buyers see"* alongside a simulated blurred approximation *"What others see"*.

---

### Mode B: Bot DM Delivery (Real Text Message)
- **How it works:**
  1. StarPost Manager publishes an auto-generated blurred teaser image (Pillow GaussianBlur with an *"Unlock for N Stars"* badge overlay), public caption, and an inline URL button labeled **"Unlock ⭐️"** linking to the bot's deep link:
     ```text
     https://t.me/<bot_username>?start=p_<post_id>
     ```
  2. When a viewer clicks the button, Telegram opens a private chat with your bot.
  3. The bot checks if the user has already bought the post. If already purchased, it immediately re-sends the full photos and hidden text for free.
  4. If not yet purchased, the bot sends `sendPaidMedia` directly to the user's private chat with `payload="u:<post_id>"` at the current post price.
  5. When the user completes payment, Telegram fires a `purchased_paid_media` update. The bot idempotently logs the purchase, increments post earnings, and sends the hidden text as a formatted message with `protect_content=True` (preventing forwarding or copying).
- **Zero-Repost Price & Text Edits:** In Mode B, the hidden text and Star price can be edited in the dashboard at any time **without reposting to the channel**.

---

### Enabling the Bot Delivery Listener
Mode B requires the background long-polling service (`app/services/bot_listener.py`) to process updates:
1. Navigate to **Settings** in the StarPost Manager dashboard.
2. In the **"Bot Delivery Listener"** card, click **"Start Listener"** (or enable the `bot_delivery` feature flag).
3. The header and Settings page display live status: `Bot listener: running` (green pill) or `stopped` (yellow pill).
4. On startup, the listener automatically runs `deleteWebhook` and polls `getUpdates` with a 50-second timeout, tracking update offsets in MongoDB `bot_state` with exponential backoff on network interruptions.

---

### Buyers Management & CSV Export
Track every customer across channel and bot unlocks:
- **Global Buyers Directory (`/buyers`):** Searchable table showing buyer name, Telegram username, numeric User ID, post title, Stars paid, and timestamp.
- **Post View Integration:** Every post view page features a direct **"Buyers (N)"** counter linking to that post's specific buyers list.
- **CSV Accounting Export (`/buyers/export`):** One-click download of all buyer transactions for tax, analytics, or CRM use.
- **Unified Financials:** Bot purchases and channel star transactions are unified on the Dashboard KPI cards and revenue charts.

---

## 🎨 Centralized Themes

All design tokens (colors, glassmorphism blur, radius, typography, shadows, gradients) reside in **one single file**:
`app/static/css/theme.css`

StarPost Manager includes 3 built-in themes:
1. **Midnight** (Default): Deep dark slate, blue accents, soft ambient glow.
2. **Neon**: Cyberpunk dark violet with electric purple and magenta accents.
3. **Clean Light**: High-contrast, clean day mode with crisp borders.

Switch themes instantly from the sidebar footer or under **Settings**. Selected theme is persisted in MongoDB and applied via `<html data-theme="...">`.

---

## 🔌 Extensible Plugin System

Every subfolder inside `app/plugins/` containing a `plugin.py` file with a `register(app, ctx)` function is automatically discovered and loaded on server startup.

A plugin can:
- Mount new FastAPI routers (`ctx.register_router(router)`)
- Add sidebar menu items (`ctx.add_nav_item(label, url, icon)`)
- Add dashboard widgets (`ctx.add_dashboard_widget(template)`)
- Register processing hooks (`ctx.add_hook(hook_name, callback)`)
- Schedule background jobs

Plugins are connected to the `feature_flags` collection and can be toggled on or off directly from the **Settings** page.

### Working Example: `example_watermark`
Located in `app/plugins/example_watermark/plugin.py`:
- Hooks into image processing to stamp an automatic semi-transparent authenticity watermark pill (`★ StarPost Exclusive`) onto photos.
- Adds a **"Watermark Tool"** testing lab route to test images before posting.

### How to Write Your Own Plugin
1. Create a directory: `app/plugins/my_custom_plugin/`
2. Create `plugin.py`:
```python
from fastapi import APIRouter

my_router = APIRouter(prefix="/plugins/my-plugin")

@my_router.get("")
def my_page():
    return {"status": "ok", "message": "Hello from custom plugin!"}

def register(app, ctx):
    ctx.register_router(my_router)
    ctx.add_nav_item("My Plugin", "/plugins/my-plugin", icon="sparkles")
```
3. Restart StarPost Manager. Your plugin will appear in the sidebar and feature flags!

---

## 🧪 Running Automated Tests

StarPost Manager comes with an extensive end-to-end test suite covering Telegram API mocking with `respx`, post creation/editing/publishing/deletion, scheduler reloads, Pillow image optimization, hidden text card rendering, teaser generation, bot listener `/start` handling, purchase idempotency, timezone conversions, HTML sanitization, bulk prefix, and auth:

```bash
# Windows
.\venv\Scripts\pytest -v

# macOS / Linux
pytest -v
```

All 37 tests run in ~3 seconds using in-memory mock databases and require no live Telegram token or active MongoDB connection.

---

## ❓ Troubleshooting & FAQs

### Q: Why do photos look blurred in Telegram?
**A:** This is Telegram's official paid media design. Telegram automatically blurs paid photos until the viewer pays the required Telegram Stars. Once unlocked, the high-resolution photo is permanently revealed to that user.

### Q: Why can't I edit photos or prices after publishing?
**A:** The Telegram Bot API restricts changes on published paid media to caption editing only (`editMessageCaption`). Use the **"Delete & Repost"** button to automatically delete the message and publish the revised version.

### Q: What if my bot reports "Not enough rights"?
**A:** Ensure your bot was added as an **Administrator** to the channel (not just a member) and that **"Post Messages"**, **"Edit Messages"**, and **"Delete Messages"** are turned ON. Run the connection test in **Settings** to see exact missing flags.

### Q: How do I access this tool from my mobile phone?
**A:** Find your PC's local IP address (e.g. `192.168.1.50`). In `.env`, keep `APP_HOST=0.0.0.0` or run with your local IP, then open `http://192.168.1.50:8000` on your phone's browser while connected to the same Wi-Fi.

---

## 🔮 Suggested Next 10 Advanced Features

1. **Auto-Repost Best Sellers**: Automatically detect posts with the highest Stars-to-view ratios and schedule periodic reminder reposts.
2. **A/B Star Price Testing**: Split-test price points (e.g. 25 Stars vs. 50 Stars) across twin photo drops to measure conversion elasticity.
3. **AI Caption & Tag Generation with Gemini**: Native Gemini API integration to analyze photo contents and generate viral, emoji-rich Telegram captions.
4. **Subscriber & Buyer Analytics**: Cohort analysis identifying "Whales" (top Star spenders) with loyalty badges and automated thank-you messages.
5. **Multi-Channel Management**: Manage multiple Telegram channels, private VIP channels, and broadcast groups from a single dropdown switcher.
6. **Encrypted Cloud Backup & Export**: One-click encrypted zip export of all uploaded media, database documents, and transaction histories.
7. **Interactive Buy-Button Inline Bot**: Companion inline bot allowing buyers to browse a paid catalog and unlock content directly from private DMs.
8. **Auto-Expire & Self-Destructing Drops**: Scheduled automatic deletion of limited-time photo drops (e.g. "Available for 24 hours only").
9. **Progressive Web App (PWA) Mode**: Web manifest and service worker configuration allowing the dashboard to be installed like a native app on iOS and Android.
10. **Watermark Customization UI**: In-dashboard visual watermark designer to customize font, logo opacity, tiling, and corner placement.
