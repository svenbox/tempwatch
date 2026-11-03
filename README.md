# tempwatch
Telegram-notis kl 07:30 varje morgon med ute-/innetemperatur (Ecowitt) och
rum med värme på (Adax). Inga beroenden utöver Python 3.12.

    cp .env.example /etc/tempwatch/tempwatch.env   # fyll i, chmod 640
    docker compose up -d --build
    docker compose run --rm tempwatch python tempwatch.py --dry-run

Energin räknas sedan igår 07:30. För exakt värde sparas dagens delsumma i `/state` (`sudo mkdir -p /var/lib/tempwatch && sudo chown 1000:1000 /var/lib/tempwatch`); utan den uppskattas värdet.

Valfritt: sätt `ELECTRICITY_PRICE_SEK_PER_KWH` för kostnad (annars visas bara kWh).

Tester: `python -m pytest tests`

Status: API-anropen mot Ecowitt/Adax/Telegram är skrivna efter dokumentation
från minnet och **inte testade mot riktiga tjänster**. Styrning av element via
prompt är inte byggd (avvaktar).
