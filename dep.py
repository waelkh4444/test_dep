import asyncio
import os
import json
import gspread
import gspread.utils
from flask import Flask, jsonify
from playwright.async_api import async_playwright
import httpx
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ENV
creds_dict = json.loads(os.getenv("GOOGLE_SHEETS_CREDENTIALS"))
OPENAI_KEY = os.getenv("OPENAI_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Google Sheet init
gc = gspread.service_account_from_dict(creds_dict)
sh = gc.open("base_insee")
worksheet = sh.sheet1
headers = worksheet.row_values(1)

siren_col = headers.index("siren")
dirigeant_col = headers.index("Nom_dirigeant")
ca_col = headers.index("Chiffre_daffaire")
entreprise_col = headers.index("nom_entreprise")
linkedin_ent_col = headers.index("url_linkedin_entreprise")
linkedin_dir_col = headers.index("url_linkedin_dirigeant")

async def get_infogreffe_info(siren):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            url = f"https://www.infogreffe.fr/entreprise/{siren}"
            await page.goto(url, timeout=15000)
            await page.wait_for_timeout(3000)

            try:
                elem = await page.query_selector("//div[@data-testid='block-representant-legal']//div[contains(@class, 'textData')]")
                dirigeant = await elem.inner_text() if elem else "Non trouvé"
            except:
                dirigeant = "Non trouvé"

            try:
                ca_elem = await page.query_selector("div[data-testid='ca']")
                ca = await ca_elem.inner_text() if ca_elem else "Non trouvé"
            except:
                ca = "Non trouvé"
        except:
            dirigeant, ca = "Non trouvé", "Non trouvé"
        finally:
            await page.close()
            await browser.close()
        return dirigeant, ca

async def search_tavily(query, max_results=5):
    url = "https://api.tavily.com/search"
    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "query": query,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
        "max_results": max_results
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return [res["url"] for res in r.json().get("results", [])]

@app.route('/full_process', methods=['POST'])
async def full_process():
    rows = worksheet.get_all_values()
    updates = []
    count = 0

    for i, row in enumerate(rows[1:], start=2):
        siren = row[siren_col] if len(row) > siren_col else ""
        entreprise = row[entreprise_col] if len(row) > entreprise_col else ""
        dirigeant_val = row[dirigeant_col] if len(row) > dirigeant_col else ""
        ca_val = row[ca_col] if len(row) > ca_col else ""
        linkedin_ent = row[linkedin_ent_col] if len(row) > linkedin_ent_col else ""
        linkedin_dir = row[linkedin_dir_col] if len(row) > linkedin_dir_col else ""

        if not siren or not entreprise:
            continue

        # Étape 1: Scraping Infogreffe si nécessaire
        if not dirigeant_val or not ca_val:
            dirigeant, ca = await get_infogreffe_info(siren)
            updates.append({
                'range': gspread.utils.rowcol_to_a1(i, dirigeant_col + 1),
                'values': [[dirigeant]]
            })
            updates.append({
                'range': gspread.utils.rowcol_to_a1(i, ca_col + 1),
                'values': [[ca]]
            })
            dirigeant_val = dirigeant

        # Étape 2: Enrichissement LinkedIn
        if not linkedin_ent:
            urls = await search_tavily(f"{entreprise} LinkedIn company")
            url = next((u for u in urls if "linkedin.com/company/" in u), "non trouvé")
            updates.append({
                'range': gspread.utils.rowcol_to_a1(i, linkedin_ent_col + 1),
                'values': [[url]]
            })

        if dirigeant_val and not linkedin_dir:
            urls = await search_tavily(f"{dirigeant_val} {entreprise} LinkedIn")
            url = next((u for u in urls if "linkedin.com/in/" in u), "non trouvé")
            updates.append({
                'range': gspread.utils.rowcol_to_a1(i, linkedin_dir_col + 1),
                'values': [[url]]
            })

        count += 1
        await asyncio.sleep(1)

        if count >= 8:
            break

    if updates:
        worksheet.batch_update(updates)
    return jsonify({"status": "success", "updated_rows": count})

if __name__ == '__main__':
    app.run(debug=True)
