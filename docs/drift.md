# Drift och felsökning

## Vad som händer
| När | Vad |
|---|---|
| Kl 07:30 (svensk tid) | Notis till Telegram |
| Minut :13, :28, :43, :58 | Provtagning av elementens energi, frostkontroll och vakthund, hjärtslag |
| Vid uppstart | En provtagning direkt |

Sommartid hanteras: notisen kommer alltid kl 07:30 svensk tid.

## Innehållet i notisen
```
Offerberg temp
Utomhus: -6,1 °C                        Ecowitt
Inomhus (köket): 16,8 °C                Ecowitts inomhussensor
Dessa rum har värme på:                 Adax-rum med värmen påslagen
  Kök: 16,9 °C (mål 18 °C)              aktuell temperatur och måltemperatur
Energiförbrukning sedan igår 07:30: 32,8 kWh (≈ 31,40 kr) [– uppskattat]
Elementkontroll: inga avvikelser        eller en lista, eller "samlar data (x/3 dygn)"
⚠ …                                     bara om något delvis misslyckades
```
- **Energi** = summan över alla element av timmarnas största värde. `– uppskattat` betyder att
  några timmar saknade prov och fylldes med elementets medel. Faller tillbaka på Adax dygnspunkter
  om fler än fyra timmar saknas.
- **Kostnad** = energi × (medelspot för perioden + påslag) × (1 + moms). Det är en uppskattning:
  spotpriset är ett oviktat medelvärde, och Adax visar inte när på dygnet elementen drog.
- **Elementkontroll** jämför varje element med sitt eget normalläge de senaste dygnen. Element
  som varit nästan avstängda (under `ANOMALY_MIN_BASELINE_WH`) jämförs inte. Kräver 3 dygn data.
- En `⚠`-rad betyder att en källa var otillgänglig. Notisen skickas ändå.

## Larm
| Larm | När | Efter |
|---|---|---|
| ⚠ Frostlarm | Ecowitts inomhussensor under `ALERT_INDOOR_BELOW_C` (8 °C) | Påminnelse var 6:e timme; ✅ när den är 1 °C över gränsen |
| ⚠ Frostlarmet är blint | Ecowitt oläsligt 3 kontroller i rad, eller inomhusmätning äldre än 90 min | Påminnelse var 12:e timme; ✅ när det fungerar igen |

Frostlarmet ignorerar föråldrad data. Ett larm som inte kunde skickas försöks igen vid nästa
provtagning. **Larmen kommer inte fram om containern, servern, nätet eller Telegram är nere.** Sätt
`HEARTBEAT_URL` till en extern övervakning (t.ex. healthchecks.io) för att upptäcka det.

## Filer och tillstånd (`/var/lib/tempwatch` → `/state`)
| Fil | Innehåll |
|---|---|
| `devices.jsonl` | Provlogg, en rad per element och provtagning. Rensas på rader äldre än 14 dygn |
| `snapshots.json` | Reservdata för energi via Adax dygnspunkter |
| `alert.json` | Frostlarmets tillstånd |
| `watch.json` | Vakthundens tillstånd |

Katalogen ska ägas av `1000:1000`. Hemligheter ligger bara i `/etc/tempwatch/tempwatch.env`
(`root:docker`, `640`), aldrig i repot.

## Kommandon
```bash
cd /home/svante/docker/tempwatch
docker compose run --rm tempwatch python tempwatch.py --dry-run        # visa notisen, skicka inte
docker compose run --rm tempwatch python tempwatch.py --once          # skicka nu
docker compose run --rm tempwatch python tempwatch.py --check-frost --dry-run   # frostläge och vakthund
docker compose run --rm tempwatch python tempwatch.py --debug-ecowitt # råsvar och mätningens ålder
docker compose run --rm tempwatch python tempwatch.py --debug-energy  # egna prover mot Adax dygnssumma
docker compose run --rm tempwatch python tempwatch.py --debug-devices # råsvar från Adax (element)
docker compose run --rm tempwatch python tempwatch.py --debug-semantics # hur energyWh beter sig över timmen
docker compose run --rm tempwatch python tempwatch.py --debug-price   # prisinställningar och spotpris
docker compose logs --tail 50                                        # fel från containern
```
`docker compose up -d --build` startar om containern (efter `git pull`). Gör det inte efter
kl 07:30 om du vill vara säker på dagens notis, eftersom en omstart då hoppar över den.

## Provar du larmet?
Sätt tillfälligt en gräns över rumstemperaturen och vänta en provtagning:
```bash
echo 'ALERT_INDOOR_BELOW_C=20' | sudo tee -a /etc/tempwatch/tempwatch.env >/dev/null && docker compose up -d
# … ett larm ska komma. Ta sedan bort raden:
sudo sed -i '/^ALERT_INDOOR_BELOW_C=20$/d' /etc/tempwatch/tempwatch.env && docker compose up -d
# … och ✅-beskedet ska komma vid nästa provtagning.
```

## Felsökning
| Symptom | Trolig orsak | Gör |
|---|---|---|
| `Adax otillgänglig (HTTP 400 invalid_grant)` | Fel Account ID eller lösenord (etiketten är inte ID) | Kontrollera `ADAX_CLIENT_ID` (siffror) och `ADAX_CLIENT_SECRET`; inga citattecken |
| `Adax otillgänglig (HTTP 502 …)` | Fel hos Adax | Försöker om två gånger; kvarstår det, vänta |
| `Spotpris otillgängligt (HTTP 403 … 1010)` | Cloudflare avvisar klienten | Kontrollera att koden är uppdaterad (har egen User-Agent) |
| Ingen kostnad, `(elpris ej inställt)` | Varken `ELECTRICITY_AREA` eller `ELECTRICITY_PRICE_SEK_PER_KWH` satt | Se `.env.example` |
| `Elementkontroll: samlar data (x/3 dygn)` | För lite provlogg | Vänta; kontrollera att `devices.jsonl` växer |
| `Kan inte spara tillstånd` / ingen `devices.jsonl` | `/var/lib/tempwatch` saknas eller fel ägare | `sudo mkdir -p /var/lib/tempwatch && sudo chown 1000:1000 /var/lib/tempwatch` |
| Ingen notis kl 07:30 | Containern stoppad eller omstartad efter 07:30 | `docker compose ps`, `docker compose logs --tail 50` |
| Energin ser dubbelt/halvt så stor ut som förväntat | Adax ändrat vad `energyWh` betyder | Kör `--debug-semantics` och `--debug-energy` |

## Underhåll
Följande ändras av någon annan, och kostnaden i notisen blir fel om de inte uppdateras
(`ELECTRICITY_ADDON_SEK_PER_KWH`, kr/kWh exkl. moms, summan av nätavgiftens rörliga del,
energiskatt, elhandlarens påslag och övriga rörliga avgifter, från din faktura):
- energiskatten, vid årsskiften,
- nätbolagets överföringsavgift,
- elhandlarens påslag.

## Kända begränsningar
- Adax `energy_log` har hål och ger ett rum alltid 502, därför används egna prover.
- Medelspotpriset är oviktat. Priset på dygnets dyra timmar slår inte igenom.
- Elementkontrollen vet inget om måltemperatur eller väder och kan ge falsklarm när värmen startar för säsongen.
- Frostlarmet gäller bara Ecowitts inomhussensor, inte Adax rumstemperaturer.
- Styrning av elementen är inte byggd.
