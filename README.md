# tempwatch

Telegram-notis kl 07:30 varje morgon med temperaturer (Ecowitt), rum med värme på
och måltemperatur (Adax), energiförbrukning och elkostnad senaste dygnet samt en
kontroll av element. Endast Python 3.12 och standardbiblioteket.

## Så fungerar det
- **Notisen** skickas kl 07:30 svensk tid (sommartid hanteras).
- **Provtagning** var 15:e minut: varje elements timenergi från Adax sparas i
  `/state/devices.jsonl` (rensas efter 14 dygn).
- **Energin** "sedan igår 07:30" räknas ur de egna proverna (medelvärde × timmar).
  Saknas prover används Adax dygnspunkter (markeras `– uppskattat`).
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
docker compose run --rm tempwatch python tempwatch.py --dry-run   # visa, skicka inte
docker compose run --rm tempwatch python tempwatch.py --once      # skicka nu
docker compose run --rm tempwatch python tempwatch.py --debug-price    # prisinställningar och spotpris
docker compose run --rm tempwatch python tempwatch.py --debug-devices  # råsvar från Adax (element)
docker compose run --rm tempwatch python tempwatch.py --debug-energy   # egna prover mot Adax dygnssumma
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
- Styrning av elementen via Telegram är inte byggd.
