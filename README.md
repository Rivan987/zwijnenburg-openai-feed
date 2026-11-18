# Zwijnenburg Mode – OpenAI Ads productfeed

Zet de Channable-feed van Zwijnenburg Mode twee keer per dag om naar het OpenAI Ads-formaat (CSV) en publiceert het resultaat via GitHub Pages en/of SFTP.

## Bestanden

| Bestand | Plek in de repo | Wat het doet |
|---|---|---|
| `convert.py` | root | Haalt `https://files.channable.com/A04bq2JBmsGjyN7Di8zJPA==.xml` op en schrijft `dist/zwijnenburg_openai.csv`, `.csv.gz` en `report.txt` |
| `sftp_upload.py` | root | Uploadt de `.csv.gz` naar de OpenAI SFTP-root (alleen als secrets zijn ingesteld) |
| `feed.yml` | `.github/workflows/feed.yml` | Draait om 05:23 en 13:23 UTC, en handmatig via "Run workflow" |

## Installatie

1. Maak een nieuwe (private) repo, bijv. `zwijnenburg-openai-feed`.
2. Zet `convert.py`, `sftp_upload.py` en `README.md` in de root en `feed.yml` in `.github/workflows/`.
3. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
4. Actions → "OpenAI Ads feed – Zwijnenburg Mode" → **Run workflow**.
5. Controleer het rapport in de samenvatting van de run.
6. Feed-URL na de eerste run: `https://<github-gebruiker>.github.io/zwijnenburg-openai-feed/zwijnenburg_openai.csv`
   Die URL gebruik je in Ads Manager → Feeds → "Connect your feed via URL".
7. Zet de veiligheidsrem goed: Settings → Secrets and variables → Actions → tabblad **Variables** → `MIN_PRODUCTS` = ongeveer de helft van het aantal eligible producten uit de eerste run.

Let op: een GitHub Pages-site van een private repo is publiek bereikbaar (op betaalde plannen), tenzij je toegangsbeheer aanzet. De feed bevat alleen publieke productdata, dus dat is meestal geen probleem. Op een gratis account moet de repo publiek zijn om Pages te gebruiken.

### Optioneel: SFTP in plaats van (of naast) URL

Als Ads Manager SFTP-gegevens geeft, zet ze in Settings → Secrets and variables → Actions → **Secrets**:

- `OPENAI_SFTP_HOST`
- `OPENAI_SFTP_USER`
- `OPENAI_SFTP_PASSWORD`
- `OPENAI_SFTP_PORT` (optioneel, standaard 22)

De workflow uploadt dan elke run `zwijnenburg_openai.csv.gz` naar de root.

## Wat de converter doet

- Leest de RSS-feed (`<item>`, met `title`/`link`/`description` zonder `g:` en de rest als `g:`-velden).
- Mapt naar OpenAI-veldnamen: `item_id`, `title`, `description`, `url`, `brand`, `image_url`, `price`, `availability`, enz.
- **Varianten:** elke maat is een eigen rij. Varianten met dezelfde `g:item_group_id` krijgen `group_id`, `listing_has_variations=true` en `variant_dict` (bijv. `{"size":"XL"}`). Een groep met maar één maat wordt niet gegroepeerd.
- Neemt `color`, `size`, `gender`, `age_group`, `mpn`, `sale_price` (alleen als lager dan `price`) en verzendkosten NL (`shipping_price`) mee.
- `product_category` = `g:product_type` (bijv. "Heren > Overhemden"), want de numerieke Google-categorie zegt OpenAI niets.
- Verwijdert de Google-UTM's uit de links en zet er `utm_source=chatgpt&utm_medium=cpc&utm_campaign=openai_ads` voor in de plaats, zodat het verkeer in GA4 apart zichtbaar is.
- Vult `seller_name` ("Zwijnenburg Mode") en `seller_url`.
- `is_ads_eligible=true` voor in_stock / pre_order / backorder, `false` voor out_of_stock.
- Laat ongeldige GTIN's weg en slaat rijen over zonder id, titel, URL, afbeelding of geldige prijs.
- **Veiligheidsrem:** bij minder dan `MIN_PRODUCTS` eligible producten wordt niets gepubliceerd, zodat een kapotte bronfeed de live feed niet leegmaakt.

## Instellingen (environment variables)

| Variabele | Standaard | Betekenis |
|---|---|---|
| `MIN_PRODUCTS` | `500` | Minimum aantal eligible producten om te publiceren (instelbaar als repo-variable) |
| `KEEP_OUT_OF_STOCK` | `true` | `false` = uitverkochte maten helemaal weglaten |
| `UTM_PARAMS` | `utm_source=chatgpt&utm_medium=cpc&utm_campaign=openai_ads` | UTM's op de product-URL's; leeg = geen |
| `BRAND_FALLBACK` | `Zwijnenburg Mode` | Merk als een product er geen heeft |
| `TARGET_COUNTRIES` / `STORE_COUNTRY` | leeg | Alleen invullen (bijv. `NL`) als Ads Manager deze kolommen per rij eist |

## Controle

Na elke run staat het rapport in de samenvatting van de Actions-run en op `.../report.txt`: aantallen, productgroepen, sale-prijzen, overgeslagen rijen en meldingen.
