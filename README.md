# tempwatch

Telegram-notis kl 07:30 varje morgon med temperaturer (Ecowitt), rum med värme på
och måltemperatur (Adax), energiförbrukning och elkostnad senaste dygnet samt en
kontroll av element. Endast Python 3.12 och standardbiblioteket.

## Så fungerar det
- **Notisen** skickas kl 07:30 svensk tid (sommartid hanteras).
- **Provtagning** vid minut :13, :28, :43 och :58 varje timme: varje elements `energyWh`
  från Adax sparas i `/state/devices.jsonl` (rensas efter 14 dygn). Adax `energyWh` är
  energin *sedan hel timme* (nollställs :00), så provet vid :58 visar timmens fulla energi.
- **Energin** "sedan igår 07:30" = summan av timmarnas största värde (pågående timme: hittills).
  Några timmar utan prov fylls med medelvärde (`– uppskattat`); saknas fler används Adax
  dygnspunkter, som ibland saknar hela dygn.
- **Frostlarm:** vid varje provtagning kontrolleras Ecowitts inomhussensor. Under 8 °C
  skickas ett larm direkt i Telegram (gräns `ALERT_INDOOR_BELOW_C`), en påminnelse var 6:e timme
  medan det är kallt, och ett besked när temperaturen är 1 °C över gränsen igen.
- **Vakthund:** larmar om Ecowitt inte går att läsa 3 kontroller i rad (ca 45 min) eller om
  inomhussensorn inte rapporterat på 90 minuter, och meddelar när det fungerar igen. Föråldrad
  temperatur används aldrig för frostlarmet. Sätt `HEARTBEAT_URL` till en extern övervakning
  (t.ex. healthchecks.io) för att även upptäcka att containern, nätet eller Telegram har slutat fungera.
- **Elkostnad** = energi × (medelspotpris i elområdet + påslag) × (1 + moms).
- **Elementkontroll** jämför varje element med sitt eget normalläge (kräver 3 dygn data).

## Installation på servern
```bash
cd /home/svante/docker && git clone https://github.com/svenbox/tempwatch
sudo mkdir -p /etc/tempwatch /var/lib/tempwatch
sudo chown 1000:1000 /var/lib/tempwatch
sudo cp tempwatch/.env.example /etc/tempwatch/tempwatch.env
sudo chown root:docker /etc/tempwatch/tempwatch.env && sudo chmod 640 /etc/tempwatch/tempwatch.env
sudo nano /etc/tempwatch/tempwatch.env     # fyll i nycklar, aldrig i git
cd tempwatch && docker compose up -d --build
```

## Kommandon
```bash
docker compose run --rm tempwatch python tempwatch.py --check-frost --dry-run  # visa frostläget och vakthunden, skicka inte
docker compose run --rm tempwatch python tempwatch.py --debug-ecowitt  # råsvar och mätningens ålder
docker compose run --rm tempwatch python tempwatch.py --dry-run   # visa, skicka inte
docker compose run --rm tempwatch python tempwatch.py --once      # skicka nu
docker compose run --rm tempwatch python tempwatch.py --debug-price    # prisinställningar och spotpris
docker compose run --rm tempwatch python tempwatch.py --debug-devices  # råsvar från Adax (element)
docker compose run --rm tempwatch python tempwatch.py --debug-energy   # egna prover mot Adax dygnssumma
docker compose run --rm tempwatch python tempwatch.py --debug-semantics # hur energyWh beter sig över timmen
```

## Inställningar
Se `.env.example`. Tomt värde betyder "inte använt". Hemligheter ligger bara i
`/etc/tempwatch/tempwatch.env`, aldrig i repot.

## Tester
```bash
pip install pytest && python -m pytest tests
```
Alla externa anrop mockas; inga nycklar behövs. CI kör samma tester vid varje push.

## Kända begränsningar
- Adax `energy_log` saknar ibland hela dygn, därför är de egna proverna primär källa.
- Medelspotpriset är ett oviktat medelvärde över perioden, inte viktat mot när elementen drog.
- Elementkontrollen kan inte avgöra om förbrukningen beror på ändrad måltemperatur eller väder.
- Frost- och vakthundslarmen går via Telegram. Utan `HEARTBEAT_URL` upptäcks inte att själva containern eller
  Telegram har slutat fungera.
- Styrning av elementen via Telegram är inte byggd.
