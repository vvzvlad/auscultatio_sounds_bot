# Auscultatio Sounds Bot

Telegram bot for learning auscultation sounds.

## Start with Docker Compose

1. Create a `docker-compose.yml` file:

   ```yaml
   volumes:
     data:
  
   services:
     bot:
       image: vvzvlad/auscultatio_sounds_bot:latest
       restart: unless-stopped
       volumes:
         - data:/app/data
       environment:
         - BOT_TOKEN=your_telegram_bot_token
   ```

2. Start the bot by running:

   ```bash
   docker-compose up -d
   ```

The bot will store all working files (e.g., statistics) in the `data` volume.

