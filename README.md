# gta6-clipper

Gör automatiskt GTA 6-klipp från **Twitch** och **Kick** till YouTube Shorts och postar **3 per dag**.
Allt körs gratis på GitHub Actions, så din dator behöver inte vara på.

```
 prepare (varje timme)                               publish (varje timme)
 ───────────────────                                 ─────────────────────
 Twitch Helix + Kick → urval/rankning               Är en slot (12:00/17:00/20:30
   → yt-dlp → faster-whisper                         Stockholm) passerad och oanvänd?
   → ffmpeg 9:16 + undertexter + hook                  → reservera slot i DB → spara DB
   → Claude: titel/beskrivning/hashtags                → YouTube resumable upload
   → kö (buffert ≥ 6)                                   → markera postad → Discord
              │                                                 ▲
              └──────► GitHub Release "state" ◄─────────────────┘
                       (state.db + renderade .mp4)
```

---

> ## ⚠️ Viktigt om rättigheter, läs detta först
>
> Klippen är **andras innehåll**. Att Twitch/Kick låter tittare skapa klipp ger dig **inte** automatiskt
> rätt att ladda upp dem på YouTube. Det här verktyget:
>
> - tar **bara** klipp från streamers som har clipping påslaget (finns det klipp är clipping på) och som
>   antingen är **automatiskt upptäckta toppstreamers** (`streamers.policy: auto`) eller står i din
>   **allowlist**,
> - ger alltid credit (namn, kanallänk och länk till originalklippet) i beskrivningen och i bild.
>
> **Det är ändå ditt ansvar** att streamern godkänner reuploads. Många stora streamers uppmuntrar
> clip-kanaler, men långt ifrån alla.
>
> - Vill du vara helt säker sätter du `streamers.policy: allowlist` och lägger bara in streamers som
>   uttryckligen tillåter det (kolla deras panels/Discord eller fråga dem).
> - Om någon ber dig sluta, eller du får ett upphovsrättsanspråk: lägg till personen i
>   `streamers.blocklist` direkt.
> - Upprepade copyright-strikes kan leda till att YouTube-kanalen stängs.

---

## Innehåll
1. [Hur det fungerar](#hur-det-fungerar)
2. [Testa lokalt utan API-nycklar](#testa-lokalt-utan-api-nycklar)
3. [Steg-för-steg: koppla in riktiga API:er](#steg-för-steg-koppla-in-riktiga-apier)
4. [Konfiguration](#konfiguration)
5. [Lagring: varför GitHub Releases](#lagring-varför-github-releases)
6. [Begränsningar](#begränsningar)
7. [Köra på Oracle Cloud Always Free (Docker)](#köra-på-oracle-cloud-always-free-docker)
8. [Lägga till TikTok/Instagram senare](#lägga-till-tiktokinstagram-senare)
9. [Felsökning](#felsökning)

---

## Hur det fungerar

### Plattformar som källor
| Plattform | Status | Kommentar |
|---|---|---|
| **Twitch** | ✅ fullt stöd | Officiellt Helix-API: app access token via client credentials, sedan Get Games och Get Clips. |
| **Kick** | ✅ i mån av möjlighet | Kick har inget officiellt clip-API. Vi använder kick.com:s publika endpoint. Den kan blockeras av Cloudflare från GitHubs servrar, och då fortsätter körningen med enbart Twitch (en varning loggas). Fungerar bäst på en egen VM. |
| YouTube Live | ❌ medvetet inte med | Att ladda upp andras YouTube-innehåll på YouTube fastnar direkt i Content ID. YouTube blockerar dessutom ofta nedladdningar från datacenter-IP:n. |

### Automatisk val av streamers
Med `streamers.policy: auto` (standard) gör systemet en gång per dygn så här:
1. Hämtar de mest sedda GTA 6-klippen för de senaste `discovery_days` (7) dagarna från varje plattform.
2. Summerar visningarna per streamer.
3. Tillåter de `top_n` (25) streamers per plattform som har flest klippvisningar.

Du behöver alltså inte lägga in några namn. Kör `clipper status` om du vill se vilka som valts.
Allowlist läggs till ovanpå, och blocklist vinner alltid.

### Urval
- Klipp från de senaste 24 timmarna med minst `min_views` visningar och 15–60 sekunder långa.
- **Bara engelska:** Twitch-klipp filtreras på kanalens språk, och dessutom kontrollerar Whisper det
  språk som faktiskt talas (`selection.spoken_languages`). Klipp på andra språk hoppas över, och
  streamers vars klipp upprepade gånger är på fel språk tas bort automatiskt.
- Klippen rankas efter **views per timme** sedan de skapades, så att nya virala klipp går före gamla.
- Högst ett klipp per streamer och körning ger variation.
- Klipp som redan finns i databasen ignoreras alltid.

### Rendering (tillför eget värde)
- **9:16, 1080x1920, max 45 s.** Den blurrade bakgrunden är samma video förstorad, och gameplay ligger
  skarpt i mitten, lätt inzoomat.
- **Facecam** läggs under gameplay om du anger kamerans position för streamern i config.
- **Hook-intro**: de första 0,5 s visar en fryst första bildruta med en hook-text som Claude har skrivit.
  Hooken ligger kvar överst i 3 s.
- **Ord-för-ord-undertexter** från faster-whisper (CPU, int8). Upp till 3 ord visas åt gången, och
  ordet som sägs just nu är gult. Texten ligger i nedre tredjedelen.
- **Credit i bild**, t.ex. `twitch.tv/streamer`.

### Titel och beskrivning
Claude (`metadata.model`) får transkriptet och klippets metadata och returnerar strukturerad JSON:
- en hook-titel på max 70 tecken som slutar på `#Shorts`,
- en kort beskrivning,
- 3–5 hashtags.

Credit-blocket med streamer, kanallänk och originalklipp läggs alltid till av koden, aldrig av AI:n.
Om API:et fallerar används en enkel mall.

### Schemaläggning och idempotens
- Slottiderna anges i `Europe/Stockholm`. GitHub-cron körs i UTC varje timme, och koden räknar själv ut
  om en slot är aktuell. Sommar- och vintertid hanteras därför automatiskt.
- En slot som missas (t.ex. för att GitHub var nere) postas inom `grace_hours`. Efter det hoppas den
  över och tas aldrig igen. Det garanterar **högst 3 per dygn**.
- **Klipp postas aldrig två gånger:**
  - Slotten reserveras, och reservationen **sparas innan** uppladdningen startar.
  - Tabellen `posts` har primärnyckeln `(klipp, plattform)`.
  - Varje video får en dold tagg, `clipref…`. Om ett jobb kraschar mitt i en uppladdning letar nästa
    körning upp videon på kanalen i stället för att ladda upp den igen.

---

## Testa lokalt utan API-nycklar

Kräver Python 3.11+ och ffmpeg.

```bash
pip install -e ".[dev]"
```

```bash
pytest -q
```

```bash
clipper demo --days 2
```

`clipper demo` är en **dry-run end-to-end** med mockad Twitch, Kick, Claude och YouTube. Den simulerar
två dygn med timvisa körningar, renderar riktiga videor med ffmpeg och skriver ut vad som skulle ha
postats. Videorna hamnar i `work/demo-data/videos/`.

Med riktiga nycklar men utan uppladdning (rör inte det riktiga tillståndet):

```bash
clipper --dry-run prepare
```

```bash
clipper --dry-run publish
```

---

## Steg-för-steg: koppla in riktiga API:er

### 1. Skapa en Twitch-app (client id/secret)
1. Gå till <https://dev.twitch.tv/console/apps> och logga in. Tvåfaktorsautentisering måste vara
   påslagen på Twitch-kontot.
2. Klicka **Register Your Application**.
   - **Name:** t.ex. `gta6-clipper-<dittnamn>` (namnet måste vara unikt).
   - **OAuth Redirect URLs:** `http://localhost` (används inte, men fältet är obligatoriskt).
   - **Category:** *Application Integration*.
   - **Client Type:** *Confidential*.
3. Klicka **Create** och sedan **Manage**.
4. Kopiera **Client ID**. Klicka **New Secret** och kopiera **Client Secret**.

### 2. Google Cloud: YouTube Data API, OAuth-klient och refresh token
1. Gå till <https://console.cloud.google.com/> och skapa ett nytt projekt, t.ex. `gta6-clipper`.
2. Gå till **APIs & Services → Library**, sök på **YouTube Data API v3** och klicka **Enable**.
3. Gå till **APIs & Services → OAuth consent screen** (i nya gränssnittet heter det *Google Auth Platform*).
   - **User type / Audience:** *External*.
   - Fyll i appnamn och din e-post.
   - **Scopes / Data access:** lägg till `.../auth/youtube.upload` och `.../auth/youtube.readonly`.
   - **Viktigt:** under **Audience / Publishing status**, klicka **Publish app** så att den står som
     **In production**. Appen kan vara overifierad, det räcker eftersom bara du loggar in (du får en
     varning om "unverified app", klicka *Advanced → Go to …*).
     Står appen kvar i *Testing* **slutar refresh token att fungera efter 7 dagar**.
4. Gå till **APIs & Services → Credentials → Create credentials → OAuth client ID**.
   - **Application type:** *Desktop app*.
   - Klicka **Download JSON** och spara filen som `client_secret.json`. Lägg den **inte** i repot,
     den är gitignorerad.
5. Kör det här på din egen dator:
   ```bash
   pip install google-auth-oauthlib
   ```
   ```bash
   python scripts/get_youtube_refresh_token.py client_secret.json
   ```
   Logga in med **Google-kontot som äger YouTube-kanalen**. Om kanalen är ett varumärkeskonto väljer
   du den kanalen. Skriptet skriver ut `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` och
   `YOUTUBE_REFRESH_TOKEN`.

### 3. Ansök om YouTube API Compliance Audit
Nya API-projekt får bara ladda upp **privata** videor. YouTube låser dem som privata, oavsett vad du
begär, tills projektet har godkänts i en audit.

1. Läs [YouTube API Services Terms](https://developers.google.com/youtube/terms/api-services-terms-of-service)
   och [Developer Policies](https://developers.google.com/youtube/terms/developer-policies).
2. Fyll i formuläret **YouTube API Services – Audit and Quota Extension Form**:
   <https://support.google.com/youtube/contact/yt_api_form>.
   - Beskriv ärligt vad verktyget gör: en intern automation som laddar upp redigerade klipp till din
     egen kanal, med credit, och bara från streamers som tillåter det.
   - Ange projektnummer (finns på projektets startsida i Google Cloud).
   - Bifoga gärna skärmdumpar av en färdig Short och av credit-texten.
3. Granskningen tar normalt några veckor. Fram till dess står `publish.privacy_status: private` kvar.
   Videorna laddas upp privat och du kan göra dem publika manuellt i YouTube Studio.
4. När projektet är godkänt ändrar du `privacy_status` till `public` i `config.yaml`. Om en video
   ändå blir låst loggas det och du får en varning i Discord.

**Kvot:** standardkvoten är 10 000 enheter per dag. En uppladdning kostar ungefär 1 600 enheter och
återhämtningskollen några enheter, så 3 uppladdningar per dag ryms.

### 4. Lägg in secrets i GitHub
1. Skapa ett **publikt** repo på GitHub (publika repon har obegränsat med gratis Actions-minuter)
   och pusha koden:
   ```bash
   git remote add origin https://github.com/<dittnamn>/gta6-clipper.git
   ```
   ```bash
   git push -u origin main
   ```
2. Gå till **Settings → Secrets and variables → Actions → New repository secret** och lägg in:

   | Secret | Från |
   |---|---|
   | `TWITCH_CLIENT_ID` | steg 1 |
   | `TWITCH_CLIENT_SECRET` | steg 1 |
   | `YOUTUBE_CLIENT_ID` | steg 2 |
   | `YOUTUBE_CLIENT_SECRET` | steg 2 |
   | `YOUTUBE_REFRESH_TOKEN` | steg 2 |
   | `ANTHROPIC_API_KEY` | <https://console.anthropic.com/> → API Keys |
   | `DISCORD_WEBHOOK_URL` | Discord: Serverinställningar → Integrationer → Webhooks → Ny webhook → Kopiera URL |

   `GITHUB_TOKEN` skapas automatiskt och ska inte läggas in.

   Secrets maskeras i loggarna och är aldrig tillgängliga i forkar eller pull requests från andra.

### 5. Aktivera workflows
1. Gå till **Settings → Actions → General** och kontrollera:
   - *Actions permissions:* "Allow all actions".
   - *Workflow permissions:* **"Read and write permissions"**. Det behövs för att spara state som
     release-assets.
2. Gå till fliken **Actions**. Klicka "I understand my workflows, go ahead and enable them" om GitHub
   frågar.
3. Testa först: **prepare → Run workflow** med *dry_run* ikryssat, och kontrollera loggen.
4. Kör sedan **prepare** utan dry-run några gånger tills bufferten har klipp. `clipper status` visar
   kön, och du ser videorna under **Releases → state**.
5. Klart. `prepare` och `publish` körs nu varje timme, och `publish` postar 12:00, 17:00 och 20:30
   svensk tid.

---

## Konfiguration

All konfiguration finns i `config.yaml`. Varje värde kan överskridas med en miljövariabel, t.ex.
`CLIPPER__PUBLISH__PRIVACY_STATUS=public`. Hemligheter läses **bara** från miljövariabler.

| Nyckel | Standard | Betydelse |
|---|---|---|
| `language` | `en` | Språk för titel, beskrivning och hook (`en`/`sv`) |
| `sources.twitch.clip_languages` | `["en"]` | Klippspråk på Twitch, tom lista = alla |
| `sources.kick.enabled` | `true` | Kick som extra källa |
| `selection.min_views` | `200` | Minsta antal visningar |
| `selection.min_duration_s` / `max_duration_s` | `15` / `60` | Längdfilter för klippen |
| `streamers.policy` | `auto` | `auto` = toppstreamers + allowlist, `allowlist` = bara allowlist |
| `streamers.allowlist` / `blocklist` | `[]` | Format `twitch:namn` eller `kick:namn` |
| `streamers.facecam` | `{}` | `"twitch:namn": {x: 0.0, y: 0.0, w: 0.25, h: 0.28}`, andelar av källbilden |
| `render.max_duration_s` | `45` | Maxlängd på en Short, inklusive intro |
| `transcribe.model` | `base` | `base` eller `small` (bättre men långsammare) |
| `metadata.model` | `claude-opus-5-5` | Claude-modell för titlar |
| `queue.buffer_min` / `buffer_target` | `6` / `8` | Varningsnivå respektive målnivå för kön |
| `schedule.slots` | `12:00, 17:00, 20:30` | Postningstider (Europe/Stockholm) |
| `schedule.grace_hours` | `3` | Hur sent en försenad slot får postas |
| `publish.privacy_status` | `private` | Ändra till `public` efter godkänd audit |

**Hitta facecam-position:** ta en skärmdump av en stream. Om kameran börjar 0 % från vänster och 0 %
uppifrån och täcker 25 % av bredden och 28 % av höjden blir det `{x: 0, y: 0, w: 0.25, h: 0.28}`.

---

## Lagring: varför GitHub Releases

| Alternativ | Fördelar | Nackdelar |
|---|---|---|
| **GitHub Release-assets** ✅ vald | Inga extra konton, upp till 2 GB per fil, enkelt API | Inga transaktioner. Löses genom att båda workflows delar `concurrency: clipper-state`. I ett publikt repo kan vem som helst ladda ner filerna. |
| Data-branch / Git LFS | Historik | Videor gör repot större för varje commit. LFS har bara 1 GB gratis bandbredd per månad. |
| Actions cache/artifacts | Inbyggt | Cachen rensas efter 7 dagar och artifacts är svåra att dela mellan workflows. Opålitligt. |
| Cloudflare R2 | Robust, 10 GB gratis | Kräver konto med betalkort och fler secrets |

Så här fungerar det:
- Databasen sparas som `state-<tid>.db`. En ny version laddas upp *innan* den gamla raderas, så en
  krasch lämnar alltid en giltig databas.
- Renderade klipp ligger som `.mp4` och raderas när de har postats.
- Videorna är små: H.264 med CRF 26 ger ungefär 8–15 MB per Short, så en kö på 8 klipp tar runt 100 MB.
- Lagringen ligger bakom interfacet `StateStore` i `src/clipper/storage/`, så det går att byta till
  R2/S3 senare.

**Radera inte releasen `state`.** Då försvinner historiken och systemet vet inte längre vad som har
postats.

---

## Begränsningar

- **Uppladdningar är privata tills API-auditen är godkänd** (se steg 3).
- **GitHub Actions-cron kan vara 5–30 minuter sen**, ibland mer vid hög belastning. Posten 17:00 kan
  alltså komma 17:10–17:40. Missas en slot helt postas den inom `grace_hours`, annars hoppas den över.
- **Schemalagda workflows i publika repon stängs av efter 60 dagar utan aktivitet i repot.**
  Pusha en commit (t.ex. en ändring i config) minst var 60:e dag, eller klicka *Enable workflow*
  under Actions om GitHub mejlar om det.
- **YouTubes policy för återanvänt innehåll ("reused content"):** kanaler som mest laddar upp andras
  material utan att tillföra något eget kan nekas eller förlora monetisering i YouTube Partner Program.
  - Verktyget tillför beskärning, undertexter, hook och credit, men det räcker inte alltid.
  - Lägg gärna till egen kommentar eller voice-over och egna reaktioner, och variera formatet.
  - **Massproducerade, repetitiva uppladdningar riskerar demonetisering.**
- **Upphovsrätt:** se varningen överst. Ta bort streamers som inte vill vara med, via blocklist.
- **Kick** kan blockeras från GitHubs IP-adresser. Då används bara Twitch.
- **Whisper på CPU** tar ungefär 10–40 s per klipp på en GitHub-runner med modellen `base`.
  Modellen cachas mellan körningar.
- **Kategorin "Grand Theft Auto VI"** måste finnas på Twitch. Innan spelet har kommit ut och kategorin
  har trafik hittas inga klipp (det loggas). Det är inget fel.

---

## Köra på Oracle Cloud Always Free (Docker)

Vill du ha realtidsbevakning, t.ex. för att läsa av live-chatten och hitta klipp när chatten exploderar,
eller slippa GitHubs cron-fördröjning, kan samma kod köras på en gratis VM.

1. Skapa ett konto på <https://www.oracle.com/cloud/free/>.
2. Gå till **Compute → Instances → Create**.
   - Image: *Ubuntu 24.04*.
   - Shape: *VM.Standard.A1.Flex* (Ampere ARM; Always Free ger upp till 4 OCPU och 24 GB RAM). Välj
     t.ex. 2 OCPU och 12 GB.
   - Lägg till din SSH-nyckel.
3. Logga in på VM:en:
   ```bash
   ssh ubuntu@<ip>
   ```
4. Installera Docker:
   ```bash
   sudo apt-get update && sudo apt-get install -y docker.io git
   ```
   ```bash
   sudo usermod -aG docker ubuntu
   ```
   Logga ut och in igen så att gruppändringen gäller.
5. Hämta koden och lägg in hemligheterna:
   ```bash
   git clone https://github.com/<dittnamn>/gta6-clipper.git && cd gta6-clipper
   ```
   ```bash
   cp .env.example .env && nano .env
   ```
   Fyll i alla nycklar i `.env`.
6. Bygg och starta:
   ```bash
   docker build -t gta6-clipper .
   ```
   ```bash
   docker run -d --name clipper --restart unless-stopped --env-file .env -v clipper-data:/data gta6-clipper
   ```
7. Följ loggen:
   ```bash
   docker exec clipper tail -f /data/clipper.log
   ```

I containern:
- används lokal lagring (`/data`-volymen) i stället för GitHub Releases,
- körs `prepare` varje timme och `publish` var 10:e minut, se `docker/crontab`,
- räknar slot-logiken fortfarande i Europe/Stockholm.

**Stäng av GitHub-workflows** (Actions → workflow → *Disable*) om du flyttar till VM:en, annars postar
båda.

**Realtidsbevakning av chatt:** Twitch-chatten nås via IRC/EventSub WebSocket. En framtida
`sources/twitch_chat.py` kan räkna meddelanden per sekund per kanal och, vid en topp, skapa ett klipp
via Helix *Create Clip*. Det kräver en användartoken med `clips:edit`. Resten av pipelinen (render,
kö, publicering) återanvänds oförändrad. Det kräver en process som alltid är igång, och därför passar
VM:en bättre än GitHub Actions.

---

## Lägga till TikTok/Instagram senare

Publicerarna implementerar `Publisher` i `src/clipper/publishers/base.py`:

```python
class TikTokPublisher(Publisher):
    name = "tiktok"
    def upload(self, video, meta, clip_key) -> UploadResult: ...
    def find_existing(self, clip_key) -> UploadResult | None: ...
```

Registrera klassen i `publishers/__init__.py:build_publishers` och lägg till `tiktok` i
`publish.platforms`. Databasen spårar postningar per `(klipp, plattform)`, så ingenting annat behöver
skrivas om.

---

## Felsökning

| Symptom | Åtgärd |
|---|---|
| Discord: "YouTube-token utgången" | Kör `scripts/get_youtube_refresh_token.py` igen och uppdatera `YOUTUBE_REFRESH_TOKEN`. Kontrollera att OAuth-appen är *In production*. |
| "YouTube-kvot överskriden" | Kvoten nollställs vid midnatt Pacific time. Kontrollera att inget annat använder projektet. |
| "Tom kö" / "Lågt buffertläge" | Det finns för få klipp. Sänk `selection.min_views`, höj `streamers.top_n` eller töm `sources.twitch.clip_languages`. |
| "Alla klippkällor misslyckades" | Kontrollera Twitch-nycklarna. Kick kan vara blockerad. |
| Videon blev privat fast `public` | Auditen är inte godkänd än (steg 3). |
| Vill se kön | Kör `clipper status` lokalt med samma secrets, eller titta under Releases → state. |

## Projektstruktur
```
src/clipper/
  cli.py          kommandon: prepare, publish, summary, status, discover, demo
  config.py       config.yaml + miljövariabler
  db.py           SQLite: clips, slots, posts, streamers
  selection.py    filter, rankning (views/h), auto-discovery
  scheduler.py    slots i lokal tidszon, sommar-/vintertid
  sources/        twitch.py (Helix), kick.py
  downloader.py   yt-dlp
  transcribe.py   faster-whisper
  subtitles.py    ASS ord-för-ord + hook + credit
  render.py       ffmpeg 9:16
  metadata.py     Claude + fallback
  publishers/     base.py (interface), youtube.py, dryrun.py
  storage/        github_release.py, local.py
  notify.py       Discord
  pipeline.py     orkestrering
  mocks.py        mockar för demo/tester
```
