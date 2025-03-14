# TeleTok

A Telegram bot that downloads and forwards TikTok and Instagram Reels videos.

## Features

- Downloads and forwards TikTok videos
- Downloads and forwards Instagram Reels
- Supports user authentication for private Instagram content
- Handles rate limiting and retries
- Clean error handling and user feedback

## Prerequisites

- Docker and Docker Compose installed on your system
- Telegram Bot Token (get it from [@BotFather](https://t.me/botfather))
- (Optional) Instagram credentials for private content access

## Local Development Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd TeleTok
```

2. Create your environment file:
```bash
cp stack.dev.env.example stack.dev.env
```

3. Edit `stack.dev.env` with your configuration:
```env
# Bot Configuration
API_TOKEN=your_telegram_bot_token
ALLOWED_IDS=  # Optional: Comma-separated list of allowed user IDs
REPLY_TO_MESSAGE=true  # Whether to reply to the original message
WITH_CAPTIONS=true  # Whether to include captions when forwarding

# Instagram Configuration (Optional)
INSTAGRAM_USERNAME=your_instagram_username
INSTAGRAM_PASSWORD=your_instagram_password
```

4. Run the bot using Docker Compose:
```bash
# Build and start the bot in development mode
docker compose -f compose.dev.yaml up --build

# Run in detached mode (background)
docker compose -f compose.dev.yaml up --build -d

# View logs when running in detached mode
docker compose -f compose.dev.yaml logs -f

# Stop the bot
docker compose -f compose.dev.yaml down
```

## Usage

1. Start a chat with your bot on Telegram
2. Send a TikTok or Instagram Reel link to the bot
3. The bot will download and forward the video to you

## Troubleshooting

- If you see 401 errors for Instagram, check your credentials in `stack.dev.env`
- If you see 403 errors, Instagram might be rate limiting the requests. Wait a few minutes and try again
- For other issues, check the Docker logs using `docker compose -f compose.dev.yaml logs -f`

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

