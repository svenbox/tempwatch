# API-anteckningar

Det vi vet om tjänsterna, och hur säkert. **Verifierat** betyder kontrollerat mot den
riktiga tjänsten med projektets egen koppling (november 2026). **Antaget** betyder att det
inte har kunnat kontrolleras. Adax och Ecowitt kan ändra sina API:er utan förvarning.

## Ecowitt (api.ecowitt.net, v3)
- `GET /api/v3/device/real_time?application_key=…&api_key=…&mac=…&call_back=outdoor,indoor&temp_unitid=1`
  (`temp_unitid=1` ger °C). **Verifierat.**
- `mac` är enhetens MAC-adress (konsolen som laddar upp), inte en nyckel.
- Svaret: `data.indoor.temperature` och `data.outdoor.temperature` är objekt
  `{"time": "<epoch-sekunder>", "unit": "℃", "value": "16.8"}`. Alla värden är textsträngar.
  **Verifierat.** Ute och inne har samma `time`, eftersom de kommer från samma uppladdning.
- `time` används för att upptäcka en sensor som slutat rapportera (vakthunden).

## Adax (api-1.adax.no/client-api)
**Inloggning.** `POST /auth/token`, form-kodad, `grant_type=password`, `username=<Account ID>`,
`password=<lösenordet>`. Account ID är ett nummer. Namnet som visas vid inloggningsuppgiften i
appen är bara en etikett och används inte. Fel värden ger `HTTP 400 invalid_grant`. **Verifierat.**

**Rum.** `GET /rest/v1/content/` ger `rooms` med `id`, `name`, `heatingEnabled`,
`targetTemperature`, `temperature` och `homeId`. Temperaturer är heltal **gånger 100**.
`heatingEnabled` betyder att värmen är *påslagen*, inte att elementet drar ström just nu.
**Verifierat.**

**Element.** `GET /rest/v1/content/?withEnergy=1` lägger till `devices` med `id`, `name`,
`roomId`, `homeId`, `type`, `energyWh` och `energyTime`. **Verifierat.**

**`energyWh` är energin sedan hel timme**, inte de senaste 60 minuterna (trots att Adax
dokumentation säger "past hour"). Värdet nollställs vid :00 och växer, och är nästan fullt vid
:48–:58. Därför är timmens energi det *största* värdet i timmen, och provet bör tas strax före
hel timme (projektet provtar vid :13, :28, :43 och :58). **Verifierat** genom att jämföra egna
timvärden mot Adax dygnssummor: avvikelse −9 % till +1 % (före ändringen −41 % till −45 %).

**`energyTime`** är tidpunkten då Adax räknade om, ungefär anropets tid. Svaret cachas i minst
ca 3 minuter. **Verifierat.**

**`/rest/v1/energy_log/{roomId}`** ger `points` med `fromTime`, `toTime` (ms) och `energyWh`.
- Punkterna är **dygn, inte timmar**, och dygnet går **04:00–04:00** svensk tid (03:00 UTC).
- Hela dygn **saknas** ibland, för alla rum samtidigt, och dubbletter förekommer. Därför är
  den inte primär energikälla. **Verifierat.**
- För ett av rummen ger anropet **alltid HTTP 502** (CGI-fel hos Adax). Ett fel hos Adax, som
  inte försvinner av omförsök. **Verifierat.**

**`POST /rest/v1/control/`** styr måltemperatur (`{"rooms": [{"id": …, "targetTemperature": "<×100 som text>"}]}`).
Finns i Adax dokumentation men används inte av projektet. **Inte provat.**

**Inte känt:** gränser för antal anrop, och om det finns scheman eller on/off per element.

## elprisetjustnu.se
- `GET https://www.elprisetjustnu.se/api/v1/prices/<år>/<MM-DD>_<SE1–SE4>.json` ger en lista med
  `SEK_per_kWh`, `EUR_per_kWh`, `time_start` och `time_end`, idag 96 kvartar per dygn. Öppet, ingen nyckel.
  **Verifierat.**
- Anrop med Pythons standard-`User-Agent` avvisas av Cloudflare (`HTTP 403, error code 1010`).
  Projektet skickar `tempwatch/1.0 (+https://github.com/svenbox/tempwatch)`. **Verifierat.**
- **Antaget:** priserna är exklusive moms. Ser kostnaden fel ut, kontrollera det först.

## Telegram
- `POST https://api.telegram.org/bot<token>/sendMessage`, form-kodad, `parse_mode=HTML`.
  Första raden i notisen skickas i fetstil, övrig text HTML-escapas. **Verifierat** (notisen kommer fram).
- Frostlarmets och vakthundens utskick använder samma funktion. **Inte provat mot riktigt larm**
  (bara med mockade anrop); se `docs/drift.md` för hur du provar.
