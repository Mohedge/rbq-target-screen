"""
Acquisition target screen (RBQ x REQ) - Streamlit front end, bilingual.

Run locally:   streamlit run app.py        (then open /eng or /fr)
Deploy:        push to GitHub, point Streamlit Community Cloud at app.py

One code base renders two pages, /eng and /fr, from the same `render(lang)`
function and the STRINGS table below. Any change made to the layout or logic
is therefore reflected in both languages by construction.
"""

import datetime as dt
import gc
import io
import json
import os
import tempfile
import time
import zipfile

import pandas as pd
import requests
import streamlit as st

import pipeline as pl

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RBQ_ZIP_URL = ("https://www.donneesquebec.ca/recherche/dataset/"
               "755b45d6-7aee-46df-a216-748a0191c79f/resource/"
               "32f6ec46-85fd-45e9-945b-965d9235840a/download/"
               "rdl01_extractiondonneesouvertes.zip")
RBQ_PAGE = "https://www.donneesquebec.ca/recherche/dataset/licencesactives"

# The REQ zip is served only by the Registraire's own site, to a person in a
# browser. Servers get 403; the page is also down at times (503). Automatic
# fetch is kept as a last option, but the normal path is a manual upload.
REQ_SOURCE_URL = ("https://www.registreentreprises.gouv.qc.ca/RQAnonymeGR/GR/GR03/"
                  "GR03A2_22A_PIU_RecupDonnPub_PC/FichierDonneesOuvertes.aspx")
REQ_PAGE = "https://www.donneesquebec.ca/recherche/dataset/registre-des-entreprises"
# Donnees Quebec's catalogue API reports when the Registraire last published the
# zip (resource "last_modified") and its cadence ("bimonthly" = twice a month).
REQ_CKAN_API = "https://www.donneesquebec.ca/recherche/api/3/action/package_show?id=registre-des-entreprises"
REQ_CADENCE_DAYS = 14

RBQ_CACHE_SECONDS = 24 * 3600        # licence list is updated daily

CACHE_DIR = os.path.join(tempfile.gettempdir(), "rbq_req_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
REQ_INDEX_PATH = os.path.join(CACHE_DIR, "req_index.csv")   # NEQ, Registration year
REQ_META_PATH = os.path.join(CACHE_DIR, "req_index.json")   # where it came from, when

GITHUB_URL = "https://github.com/Mohedge/rbq-target-screen"

# RBQ licence sub-categories (Regie du batiment du Quebec, annexes I to III).
# Source: rbq.gouv.qc.ca, "Liste des sous-categories", consulted 2026-09-11.
# (code, French title, English title)
SUBCATEGORIES = [
    ("annexe1", [
        ("1.1.1", "Batiments residentiels neufs vises a un plan de garantie, classe I",
                  "New residential buildings covered by a guarantee plan, class I"),
        ("1.1.2", "Batiments residentiels neufs vises a un plan de garantie, classe II",
                  "New residential buildings covered by a guarantee plan, class II"),
        ("1.2", "Petits batiments", "Small buildings"),
        ("1.3", "Batiments de tout genre", "Buildings of all kinds"),
        ("1.4", "Routes et canalisation", "Roads and pipelines"),
        ("1.5", "Structures d'ouvrages de genie civil", "Civil engineering structures"),
        ("1.6", "Ouvrages de genie civil immerges", "Submerged civil engineering works"),
        ("1.7", "Telecommunication, transport, transformation et distribution d'energie electrique",
                "Telecommunications, electrical power transmission, transformation and distribution"),
        ("1.8", "Installation d'equipements petroliers", "Petroleum equipment installation"),
        ("1.9", "Mecanique du batiment", "Building mechanical systems"),
        ("1.10", "Remontees mecaniques", "Ski lifts"),
    ]),
    ("annexe2", [
        ("2.1", "Puits fores", "Drilled wells"),
        ("2.2", "Ouvrages de captage d'eau non fores", "Non-drilled water intake works"),
        ("2.3", "Systemes de pompage des eaux souterraines", "Groundwater pumping systems"),
        ("2.4", "Systemes d'assainissement autonome", "On-site sewage systems"),
        ("2.6", "Pieux et fondations speciales", "Piles and special foundations"),
        ("2.8", "Sautage", "Blasting"),
        ("3.1", "Structures de beton", "Concrete structures"),
        ("4.1", "Structures de maconnerie", "Masonry structures"),
        ("5.1", "Structures metalliques et elements prefabriques de beton",
                "Steel structures and precast concrete elements"),
        ("6.1", "Charpentes de bois", "Wood framing"),
        ("10.0", "Systemes de chauffage localise a combustible solide", "Solid-fuel space heating systems"),
        ("11.1", "Tuyauterie industrielle ou institutionnelle sous pression",
                 "Industrial or institutional pressure piping"),
        ("13.1", "Protection contre la foudre", "Lightning protection"),
        ("13.2", "Systemes d'alarme incendie", "Fire alarm systems"),
        ("13.3", "Systemes d'extinction d'incendie", "Fire suppression systems"),
        ("13.4", "Systemes localises d'extinction incendie", "Localised fire suppression systems"),
        ("14.1", "Ascenseurs et monte-charges", "Elevators and freight lifts"),
        ("14.2", "Appareils elevateurs pour personnes a mobilite reduite", "Lifts for persons with reduced mobility"),
        ("14.3", "Autres types d'appareils elevateurs", "Other lifting devices"),
        ("15.1", "Systemes de chauffage a air pulse", "Forced-air heating systems"),
        ("15.2", "Systemes de bruleurs au gaz naturel", "Natural gas burner systems"),
        ("15.3", "Systemes de bruleurs a l'huile", "Oil burner systems"),
        ("15.4", "Systemes de chauffage hydronique", "Hydronic heating systems"),
        ("15.5", "Plomberie", "Plumbing"),
        ("15.6", "Propane", "Propane"),
        ("15.7", "Ventilation residentielle", "Residential ventilation"),
        ("15.8", "Ventilation", "Ventilation"),
        ("15.9", "Petits systemes de refrigeration", "Small refrigeration systems"),
        ("15.10", "Refrigeration", "Refrigeration"),
        ("16.0", "Electricite", "Electrical"),
        ("17.1", "Instrumentation, controle et regulation", "Instrumentation, control and regulation"),
    ]),
    ("annexe3", [
        ("2.5", "Excavation et terrassement", "Excavation and earthwork"),
        ("2.7", "Travaux d'emplacement", "Site work"),
        ("3.2", "Petits ouvrages de beton", "Small concrete works"),
        ("4.2", "Maconnerie non structurale, marbre et ceramique", "Non-structural masonry, marble and ceramic"),
        ("5.2", "Ouvrages metalliques", "Metal work"),
        ("6.2", "Travaux de bois et plastique", "Wood and plastic work"),
        ("7.0", "Isolation, etancheite, couvertures et revetements exterieurs",
                "Insulation, waterproofing, roofing and exterior cladding"),
        ("8.0", "Portes et fenetres", "Doors and windows"),
        ("9.0", "Travaux de finition", "Finishing work"),
        ("11.2", "Equipements et produits speciaux", "Special equipment and products"),
        ("12.0", "Armoires et comptoirs usines", "Manufactured cabinets and countertops"),
        ("13.5", "Installations speciales ou prefabriquees", "Special or prefabricated installations"),
        ("17.2", "Intercommunication, telephonie et surveillance", "Intercom, telephony and surveillance"),
    ]),
]

# ---------------------------------------------------------------------------
# Strings. One key, two languages. Add a key here and use t(key) in render().
# ---------------------------------------------------------------------------

STRINGS = {
  "en": {
    "page_title": "RBQ Radar",
    "title": "RBQ Radar: acquisition target screen (RBQ licences x REQ register)",
    "caption": "Deterministic filter, join, and score. Invents nothing. Every step reports its row count.",
    "switch": "Fr",
    "params": "Parameters",
    "subcat": "SUBCATEGORY (RBQ sub-category)",
    "subcat_help": "Type the code exactly as it appears in the RBQ file (16 = electrical, 15.5 = plumbing).",
    "subcat_ref": "RBQ sub-categories (reference)",
    "annexe1": "Annex I - General contractor",
    "annexe2": "Annex II - Specialised contractor",
    "annexe3": "Annex III - Specialised contractor",
    "muni": "MUNICIPALITIES (one per line)",
    "muni_help": "The territory: only companies whose RBQ 'Municipalite' matches one of these names are kept "
                 "(accents and capitals are ignored), so this list defines the corridor.",
    "year": "CURRENT_YEAR",
    "year_help": "The year used to compute each company's age (CURRENT_YEAR minus the REQ registration year).",
    "age": "AGE_THRESHOLD",
    "age_help": "The company age, in years, from which a firm is considered established.",
    "strong_age": "STRONG_AGE_THRESHOLD",
    "strong_age_help": "The company age, in years, from which a firm is considered long established, "
                       "the strongest sign of an owner nearing succession.",
    "bond": "HIGH_BOND (CAD)",
    "bond_help": "The RBQ bond amount ('Montant de la caution') that marks a firm holding a general licence; "
                 "the RBQ requires 40 000 CAD from general contractors and 20 000 CAD from specialised ones.",
    "subcat_thr": "SUBCAT_THRESHOLD",
    "subcat_thr_help": "The number of authorised RBQ sub-categories from which a firm is considered broad in its operations.",
    "rows": "OUTPUT_ROWS",
    "rows_help": "How many top-ranked companies go into the Targets sheet; the full corridor is still exported on its own sheet.",
    "req_join": "Join REQ register (registration year)",
    "req_join_help": "Ticked, the app looks up each company's registration year in the REQ register to compute its age; "
                     "unticked, it runs on RBQ data alone and age is left blank "
                     "(REQ data is licensed CC-BY-NC-SA 4.0, non-commercial).",
    "file1": "File 1: RBQ active licences",
    "file1_src": "Source: [Donnees Quebec]({url}), licence CC-BY 4.0, updated daily.",
    "rbq_fetch": "Fetch latest automatically",
    "rbq_upload": "Upload file (.csv or .zip)",
    "rbq_uploader": "rdl01_ExtractionDonneesOuvertes (.csv or .zip)",
    "file2": "File 2: REQ register (Nom.csv)",
    "file2_src": "Source: [Donnees Quebec]({url}), licence CC-BY-NC-SA 4.0, updated twice monthly. "
                 "Only Nom.csv is used; the other five files in the zip are ignored.",
    "req_reuse": "Reuse last uploaded REQ file",
    "req_upload": "Upload file (Nom.csv or the REQ .zip)",
    "req_fetch": "Try automatic fetch",
    "req_mode_help": "Automatic fetch is last on purpose: the Registraire refuses requests from servers (403) "
                     "and is often unavailable (503). Download the zip from Donnees Quebec in your browser, "
                     "then upload it here. Uploading the zip is lighter on memory than uploading Nom.csv.",
    "req_uploader": "Nom.csv or the full REQ zip",
    "req_none": "No REQ file has been uploaded to this app yet.",
    "run": "Run screen",
    "dl_rbq": "Downloading RBQ licence list from Donnees Quebec...",
    "cached_rbq": "Using RBQ file cached today.",
    "err_rbq": "Upload the RBQ file or choose automatic fetch.",
    "dl_req": "Attempting to download the REQ register from the Registraire...",
    "err_req_fetch": "Automatic REQ download failed: {err}\n\nThe Registraire serves the zip only to a person "
                     "in a browser. Download it from the [dataset page]({url}), then switch the REQ source "
                     "to Upload and provide the zip or Nom.csv.",
    "err_req": "Upload the REQ file, reuse the last one, or untick the REQ join.",
    "err_req_reuse": "No stored REQ file to reuse. Upload one first.",
    "building_index": "Reducing Nom.csv to a NEQ / registration-year index (kept for later runs)...",
    "index_saved": "REQ index stored: {n:,} enterprises, file dated {d}. Later runs can reuse it.",
    "running": "Running pipeline...",
    "done": "Done. {corridor:,} companies in corridor, top {n} shortlisted.",
    "download": "Download targets.xlsx",
    "targets": "Targets",
    "corridor": "Corridor ({n:,} rows)",
    "err_empty": "Stopped: the filter '{f}' emptied the dataset. Check the parameter and the exact "
                 "spelling of the values in the source file.",
    # freshness counter
    "fresh_title": "REQ file held by the app",
    "fresh_none": "None yet. Upload the REQ zip (or Nom.csv) once; later runs can reuse it until the "
                  "Registraire publishes a new version (about twice a month).",
    "fresh_held": "File held: version dated {held} (uploaded {up}).",
    "fresh_published": "Latest version published by the Registraire: {pub}.",
    "fresh_unknown_pub": "Latest published version: unknown (Donnees Quebec catalogue unreachable); "
                         "estimate based on a 14-day cadence.",
    "fresh_uptodate": "Up to date. Next publication expected around {next} (T-{days} days).",
    "fresh_stale": "A newer version has been available for {days} days (T+{days}). "
                   "Download it and upload it here to refresh.",
    "fresh_due_today": "A new publication is expected today (T-0).",
    "fresh_caption": "REQ file: {short}",
    "short_none": "none stored",
    "short_ok": "version {held}, up to date, next expected ~{next} (T-{days})",
    "short_stale": "version {held}, newer version out for {days} days (T+{days})",
    "short_due": "version {held}, new publication expected today (T-0)",
    # about
    "about": """
### About RBQ Radar (work in progress)

**Purpose.** A ground-level tool to build a first list of acquisition candidates in a construction
trade, before any outreach. It filters the RBQ's public list of active licences by trade and territory,
joins the Quebec enterprise register (REQ) to learn each company's age, and ranks the result. It invents
nothing: every step reports its row count and stops if a filter empties the data.

**Trial.** This is a prototype under trial. Results are a starting point for human verification
(RBQ, REQ, website, phone), not a qualified list.

**Ranking.** Each company gets points and the list is sorted by points, oldest registration first
on ties:
- age at or above STRONG_AGE_THRESHOLD: 3 points; otherwise age at or above AGE_THRESHOLD: 2 points
- RBQ bond equal to HIGH_BOND: 2 points
- authorised sub-categories at or above SUBCAT_THRESHOLD: 1 point

Maximum 6. Age needs the REQ join; without it, only bond and breadth score.

**Data limitation and the three stages.** The RBQ file downloads automatically. The REQ register does
not: the Registraire serves its zip only to a person in a browser (servers get 403, and the page is
down at times). No technical workaround exists on our side; a fresh register requires a user to
download it, about twice a month, matching the Registraire's publication cadence.
- *Stage 1 (now):* manual upload of the REQ zip; the app keeps a reduced index (NEQ, registration year)
  and reuses it for later runs; a counter shows how old the held file is against the latest
  published version. Note: on free hosting the stored index is lost when the app restarts.
- *Stage 2:* publish that reduced index on GitHub (release asset) so the app fetches it automatically
  and it survives restarts; one 5-minute manual refresh per fortnight.
- *Stage 3:* a scheduled job that tries the refresh itself twice a month; it will work only if the
  Registraire lets it through, otherwise Stage 2 remains.

**Licences.** RBQ data: CC-BY 4.0. REQ data: CC-BY-NC-SA 4.0 (non-commercial, share-alike).

Code: {github}
""",
  },
  "fr": {
    "page_title": "RBQ Radar",
    "title": "RBQ Radar : filtre de cibles d'acquisition (licences RBQ x registre REQ)",
    "caption": "Filtre, jointure et pointage deterministes. N'invente rien. Chaque etape rapporte son nombre de lignes.",
    "switch": "Eng",
    "params": "Parametres",
    "subcat": "SOUS-CATEGORIE (sous-categorie RBQ)",
    "subcat_help": "Saisir le code exactement comme dans le fichier RBQ (16 = electricite, 15.5 = plomberie).",
    "subcat_ref": "Sous-categories RBQ (reference)",
    "annexe1": "Annexe I - Entrepreneur general",
    "annexe2": "Annexe II - Entrepreneur specialise",
    "annexe3": "Annexe III - Entrepreneur specialise",
    "muni": "MUNICIPALITES (une par ligne)",
    "muni_help": "Le territoire : seules les entreprises dont la 'Municipalite' RBQ correspond a l'un de ces noms "
                 "sont conservees (accents et majuscules ignores); cette liste definit donc le corridor.",
    "year": "ANNEE_COURANTE",
    "year_help": "L'annee servant a calculer l'age de chaque entreprise (ANNEE_COURANTE moins l'annee d'immatriculation au REQ).",
    "age": "SEUIL_AGE",
    "age_help": "L'age, en annees, a partir duquel une entreprise est consideree etablie.",
    "strong_age": "SEUIL_AGE_FORT",
    "strong_age_help": "L'age, en annees, a partir duquel une entreprise est consideree etablie de longue date, "
                       "le signe le plus fort d'un proprietaire proche de la releve.",
    "bond": "CAUTION_ELEVEE (CAD)",
    "bond_help": "Le montant de la caution RBQ qui signale une licence d'entrepreneur general; la RBQ exige "
                 "40 000 CAD des entrepreneurs generaux et 20 000 CAD des entrepreneurs specialises.",
    "subcat_thr": "SEUIL_SOUS_CAT",
    "subcat_thr_help": "Le nombre de sous-categories RBQ autorisees a partir duquel une entreprise est consideree diversifiee.",
    "rows": "LIGNES_SORTIE",
    "rows_help": "Le nombre d'entreprises les mieux classees versees dans la feuille Cibles; le corridor complet "
                 "est tout de meme exporte sur sa propre feuille.",
    "req_join": "Joindre le registre REQ (annee d'immatriculation)",
    "req_join_help": "Cochee, l'application cherche l'annee d'immatriculation de chaque entreprise au REQ pour calculer "
                     "son age; decochee, elle tourne sur les donnees RBQ seules et l'age reste vide "
                     "(donnees REQ sous licence CC-BY-NC-SA 4.0, usage non commercial).",
    "file1": "Fichier 1 : licences RBQ actives",
    "file1_src": "Source : [Donnees Quebec]({url}), licence CC-BY 4.0, mise a jour quotidienne.",
    "rbq_fetch": "Telecharger la derniere version automatiquement",
    "rbq_upload": "Televerser un fichier (.csv ou .zip)",
    "rbq_uploader": "rdl01_ExtractionDonneesOuvertes (.csv ou .zip)",
    "file2": "Fichier 2 : registre REQ (Nom.csv)",
    "file2_src": "Source : [Donnees Quebec]({url}), licence CC-BY-NC-SA 4.0, mise a jour deux fois par mois. "
                 "Seul Nom.csv est utilise; les cinq autres fichiers du zip sont ignores.",
    "req_reuse": "Reutiliser le dernier fichier REQ televerse",
    "req_upload": "Televerser un fichier (Nom.csv ou le .zip du REQ)",
    "req_fetch": "Tenter le telechargement automatique",
    "req_mode_help": "Le telechargement automatique est en dernier a dessein : le Registraire refuse les requetes "
                     "venant de serveurs (403) et sa page est souvent indisponible (503). Telechargez le zip "
                     "depuis Donnees Quebec dans votre navigateur, puis televersez-le ici. Le zip est plus "
                     "leger en memoire que Nom.csv.",
    "req_uploader": "Nom.csv ou le zip complet du REQ",
    "req_none": "Aucun fichier REQ n'a encore ete televerse dans cette application.",
    "run": "Lancer le filtre",
    "dl_rbq": "Telechargement de la liste des licences RBQ depuis Donnees Quebec...",
    "cached_rbq": "Fichier RBQ mis en cache aujourd'hui.",
    "err_rbq": "Televersez le fichier RBQ ou choisissez le telechargement automatique.",
    "dl_req": "Tentative de telechargement du registre REQ aupres du Registraire...",
    "err_req_fetch": "Echec du telechargement automatique du REQ : {err}\n\nLe Registraire ne sert le zip qu'a une "
                     "personne dans un navigateur. Telechargez-le depuis la [page du jeu de donnees]({url}), "
                     "puis passez la source REQ a Televerser et fournissez le zip ou Nom.csv.",
    "err_req": "Televersez le fichier REQ, reutilisez le dernier, ou decochez la jointure REQ.",
    "err_req_reuse": "Aucun fichier REQ conserve a reutiliser. Televersez-en un d'abord.",
    "building_index": "Reduction de Nom.csv en un index NEQ / annee d'immatriculation (conserve pour les prochaines executions)...",
    "index_saved": "Index REQ conserve : {n:,} entreprises, fichier date du {d}. Les prochaines executions pourront le reutiliser.",
    "running": "Execution du pipeline...",
    "done": "Termine. {corridor:,} entreprises dans le corridor, {n} retenues en tete de liste.",
    "download": "Telecharger targets.xlsx",
    "targets": "Cibles",
    "corridor": "Corridor ({n:,} lignes)",
    "err_empty": "Arret : le filtre '{f}' a vide le jeu de donnees. Verifiez le parametre et l'orthographe "
                 "exacte des valeurs dans le fichier source.",
    "fresh_title": "Fichier REQ conserve par l'application",
    "fresh_none": "Aucun pour l'instant. Televersez le zip du REQ (ou Nom.csv) une fois; les prochaines executions "
                  "pourront le reutiliser jusqu'a ce que le Registraire publie une nouvelle version (environ deux fois par mois).",
    "fresh_held": "Fichier conserve : version du {held} (televersee le {up}).",
    "fresh_published": "Derniere version publiee par le Registraire : {pub}.",
    "fresh_unknown_pub": "Derniere version publiee : inconnue (catalogue Donnees Quebec inaccessible); "
                         "estimation selon une cadence de 14 jours.",
    "fresh_uptodate": "A jour. Prochaine publication attendue vers le {next} (T-{days} jours).",
    "fresh_stale": "Une version plus recente est disponible depuis {days} jours (T+{days}). "
                   "Telechargez-la et televersez-la ici pour rafraichir.",
    "fresh_due_today": "Une nouvelle publication est attendue aujourd'hui (T-0).",
    "fresh_caption": "Fichier REQ : {short}",
    "short_none": "aucun conserve",
    "short_ok": "version du {held}, a jour, prochaine attendue vers le {next} (T-{days})",
    "short_stale": "version du {held}, version plus recente disponible depuis {days} jours (T+{days})",
    "short_due": "version du {held}, nouvelle publication attendue aujourd'hui (T-0)",
    "about": """
### A propos de RBQ Radar (travail en cours)

**But.** Un outil de terrain pour dresser une premiere liste de candidats a l'acquisition dans un metier
de la construction, avant toute approche. Il filtre la liste publique des licences actives de la RBQ par
metier et par territoire, joint le registre des entreprises du Quebec (REQ) pour connaitre l'age de chaque
entreprise, puis classe le resultat. Il n'invente rien : chaque etape rapporte son nombre de lignes et
s'arrete si un filtre vide les donnees.

**Essai.** Il s'agit d'un prototype a l'essai. Les resultats sont un point de depart pour une verification
humaine (RBQ, REQ, site web, telephone), pas une liste qualifiee.

**Classement.** Chaque entreprise recoit des points; la liste est triee par points, puis par immatriculation
la plus ancienne en cas d'egalite :
- age egal ou superieur a SEUIL_AGE_FORT : 3 points; sinon age egal ou superieur a SEUIL_AGE : 2 points
- caution RBQ egale a CAUTION_ELEVEE : 2 points
- sous-categories autorisees egales ou superieures a SEUIL_SOUS_CAT : 1 point

Maximum 6. L'age exige la jointure REQ; sans elle, seuls la caution et l'etendue comptent.

**Limite des donnees et les trois etapes.** Le fichier RBQ se telecharge automatiquement. Le registre REQ,
non : le Registraire ne sert son zip qu'a une personne dans un navigateur (les serveurs recoivent un 403,
et la page est parfois hors service). Aucun contournement technique n'existe de notre cote; un registre
frais exige qu'un utilisateur le telecharge, environ deux fois par mois, au rythme des publications du
Registraire.
- *Etape 1 (maintenant) :* televersement manuel du zip REQ; l'application conserve un index reduit
  (NEQ, annee d'immatriculation) et le reutilise aux executions suivantes; un compteur indique l'age du
  fichier conserve par rapport a la derniere version publiee. Note : sur l'hebergement gratuit, l'index
  conserve est perdu au redemarrage de l'application.
- *Etape 2 :* publier cet index reduit sur GitHub (fichier de version) pour que l'application le recupere
  automatiquement et qu'il survive aux redemarrages; un rafraichissement manuel de 5 minutes par quinzaine.
- *Etape 3 :* une tache planifiee qui tente le rafraichissement elle-meme deux fois par mois; elle ne
  fonctionnera que si le Registraire la laisse passer, sinon l'etape 2 demeure.

**Licences.** Donnees RBQ : CC-BY 4.0. Donnees REQ : CC-BY-NC-SA 4.0 (usage non commercial, partage
dans les memes conditions).

Code : {github}
""",
  },
}


# ---------------------------------------------------------------------------
# Download helpers (stream to disk, never hold the whole zip in RAM)
# ---------------------------------------------------------------------------

def _cached_path(name: str, ttl: int):
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl:
        return path
    return None


def download_to(path: str, url: str, progress=None, timeout=600) -> str:
    """Stream a URL to disk. Returns path. Raises on HTTP error or non-zip body."""
    tmp = path + ".part"
    with requests.get(url, stream=True, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0 (target-screen prototype)"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress.progress(min(done / total, 1.0))
    if not zipfile.is_zipfile(tmp):
        os.remove(tmp)
        raise ValueError("Downloaded file is not a zip archive (the source served a web page instead).")
    os.replace(tmp, path)
    return path


def zip_member(path: str, hint: str):
    """Open one member of a zip on disk as a streaming file object, plus its date."""
    zf = zipfile.ZipFile(path)
    names = [n for n in zf.namelist() if hint.lower() in n.lower() and n.lower().endswith(".csv")]
    if not names:
        raise FileNotFoundError(f"No CSV matching '{hint}' inside the zip. Members: {zf.namelist()}")
    info = zf.getinfo(names[0])
    return zf.open(names[0]), dt.date(*info.date_time[:3])


def source_from_upload(uploaded, hint: str):
    """Uploaded file may be a CSV or a zip containing the CSV. Returns
    (file object, file date, temp path to delete afterwards or None)."""
    if uploaded.name.lower().endswith(".zip"):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=CACHE_DIR)
        tmp.write(uploaded.getbuffer())
        tmp.close()
        src, fdate = zip_member(tmp.name, hint)
        return src, fdate, tmp.name
    return io.BytesIO(uploaded.getvalue()), dt.date.today(), None


# ---------------------------------------------------------------------------
# REQ index persistence and freshness
# ---------------------------------------------------------------------------

def load_req_meta():
    if os.path.exists(REQ_INDEX_PATH) and os.path.exists(REQ_META_PATH):
        try:
            with open(REQ_META_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def save_req_index(index: pd.DataFrame, file_date: dt.date, source_name: str):
    index.to_csv(REQ_INDEX_PATH, index=False)
    meta = {"file_date": file_date.isoformat(), "uploaded": dt.date.today().isoformat(),
            "source": source_name, "rows": int(len(index))}
    with open(REQ_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


def load_req_index() -> pd.DataFrame:
    return pd.read_csv(REQ_INDEX_PATH, dtype={"NEQ": str})


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def req_published_date():
    """Date the Registraire last published the REQ zip, per Donnees Quebec's catalogue. None if unreachable."""
    try:
        r = requests.get(REQ_CKAN_API, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (target-screen prototype)"})
        r.raise_for_status()
        for res in r.json()["result"]["resources"]:
            if (res.get("format") or "").lower() == "zip" and res.get("last_modified"):
                return dt.date.fromisoformat(res["last_modified"][:10])
    except Exception:
        return None
    return None


def freshness(meta, lang):
    """Return (long text for the tooltip, short text for the caption)."""
    s = STRINGS[lang]
    if not meta:
        return s["fresh_none"], s["short_none"]
    held = dt.date.fromisoformat(meta["file_date"])
    today = dt.date.today()
    pub = req_published_date()
    lines = [s["fresh_held"].format(held=held, up=meta["uploaded"])]
    if pub is None:
        lines.append(s["fresh_unknown_pub"])
        anchor = held
    else:
        lines.append(s["fresh_published"].format(pub=pub))
        anchor = pub
    if pub is not None and pub > held:
        days = max((today - pub).days, 0)
        lines.append(s["fresh_stale"].format(days=days))
        return "\n\n".join(lines), s["short_stale"].format(held=held, days=days)
    # up to date (or unknown): count down to the next expected publication
    nxt = anchor + dt.timedelta(days=REQ_CADENCE_DAYS)
    while nxt < today:
        nxt += dt.timedelta(days=REQ_CADENCE_DAYS)
    days = (nxt - today).days
    if days == 0:
        lines.append(s["fresh_due_today"])
        return "\n\n".join(lines), s["short_due"].format(held=held)
    lines.append(s["fresh_uptodate"].format(next=nxt, days=days))
    return "\n\n".join(lines), s["short_ok"].format(held=held, next=nxt, days=days)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render(lang: str):
    s = STRINGS[lang]
    other = "fr" if lang == "en" else "en"
    t = s.__getitem__

    # Language switch, pinned in the header row, left of Streamlit's toolbar
    # (Share, star, edit, GitHub, menu). The toolbar is not ours, so the link is
    # positioned over the header rather than inserted into it.
    st.markdown(
        "<style>.lang-switch{position:fixed;top:0.95rem;right:15.5rem;z-index:1000001;"
        "font-size:0.85rem;font-weight:600;text-decoration:none;color:inherit;opacity:0.85}"
        ".lang-switch:hover{opacity:1;text-decoration:underline}</style>"
        f'<a class="lang-switch" href="/{PAGES[other].url_path}" target="_self">{t("switch")}</a>',
        unsafe_allow_html=True)
    st.title(t("title"))
    st.caption(t("caption"))

    defaults = pl.Params()
    title_col = 1 if lang == "fr" else 2

    with st.sidebar:
        st.header(t("params"))
        SUBCATEGORY = st.text_input(t("subcat"), defaults.SUBCATEGORY,
                                    help=t("subcat_help") + "\n\n" + "\n\n".join(
                                        f"**{t(annexe)}**  \n" + "  \n".join(f"{c[0]} : {c[title_col]}" for c in items)
                                        for annexe, items in SUBCATEGORIES))
        with st.expander(t("subcat_ref")):
            for annexe, items in SUBCATEGORIES:
                st.markdown(f"**{t(annexe)}**")
                st.markdown("  \n".join(f"`{c[0]}` {c[title_col]}" for c in items))
        muni_text = st.text_area(t("muni"), "\n".join(defaults.MUNICIPALITIES), height=260, help=t("muni_help"))
        CURRENT_YEAR = st.number_input(t("year"), 2000, 2100, defaults.CURRENT_YEAR, help=t("year_help"))
        AGE_THRESHOLD = st.number_input(t("age"), 0, 100, defaults.AGE_THRESHOLD, help=t("age_help"))
        STRONG_AGE_THRESHOLD = st.number_input(t("strong_age"), 0, 100, defaults.STRONG_AGE_THRESHOLD,
                                               help=t("strong_age_help"))
        HIGH_BOND = st.number_input(t("bond"), 0, 1_000_000, defaults.HIGH_BOND, step=1000, help=t("bond_help"))
        SUBCAT_THRESHOLD = st.number_input(t("subcat_thr"), 0, 50, defaults.SUBCAT_THRESHOLD, help=t("subcat_thr_help"))
        OUTPUT_ROWS = st.number_input(t("rows"), 1, 1000, defaults.OUTPUT_ROWS, help=t("rows_help"))
        USE_REQ_JOIN = st.checkbox(t("req_join"), defaults.USE_REQ_JOIN, help=t("req_join_help"))

    params = pl.Params(
        SUBCATEGORY=SUBCATEGORY.strip(),
        MUNICIPALITIES=[m.strip() for m in muni_text.splitlines() if m.strip()],
        CURRENT_YEAR=int(CURRENT_YEAR), AGE_THRESHOLD=int(AGE_THRESHOLD),
        STRONG_AGE_THRESHOLD=int(STRONG_AGE_THRESHOLD), HIGH_BOND=int(HIGH_BOND),
        SUBCAT_THRESHOLD=int(SUBCAT_THRESHOLD), OUTPUT_ROWS=int(OUTPUT_ROWS),
        USE_REQ_JOIN=USE_REQ_JOIN,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.subheader(t("file1"))
        st.markdown(t("file1_src").format(url=RBQ_PAGE))
        rbq_mode = st.radio("RBQ source", [t("rbq_fetch"), t("rbq_upload")],
                            horizontal=True, label_visibility="collapsed")
        rbq_upload = None
        if rbq_mode == t("rbq_upload"):
            rbq_upload = st.file_uploader(t("rbq_uploader"), type=["csv", "zip"])

    meta = load_req_meta()
    fresh_long, fresh_short = freshness(meta, lang)

    with col2:
        st.subheader(t("file2"))
        st.markdown(t("file2_src").format(url=REQ_PAGE))
        options = ([t("req_reuse")] if meta else []) + [t("req_upload"), t("req_fetch")]
        req_mode = st.radio("REQ source", options, horizontal=True, label_visibility="collapsed",
                            disabled=not USE_REQ_JOIN, help=t("req_mode_help"))
        req_upload = None
        if req_mode == t("req_upload") and USE_REQ_JOIN:
            # The tooltip on this uploader is the freshness counter (T-x / T+y).
            req_upload = st.file_uploader(t("req_uploader"), type=["csv", "zip"], help=fresh_long)
        st.caption(t("fresh_caption").format(short=fresh_short), help=fresh_long)

    run = st.button(t("run"), type="primary", use_container_width=True)

    if run:
        log_box = st.empty()
        lines = []

        def log_fn(msg):
            lines.append(msg)
            log_box.code("\n".join(lines))

        temp_files = []
        rbq_src = req_src = None
        req_index = None
        try:
            # ---- RBQ ----
            if rbq_upload is not None:
                rbq_src, _, tmp = source_from_upload(rbq_upload, "rdl01")
                if tmp:
                    temp_files.append(tmp)
            elif rbq_mode == t("rbq_fetch"):
                path = _cached_path("rbq.zip", RBQ_CACHE_SECONDS)
                if path is None:
                    st.info(t("dl_rbq"))
                    path = download_to(os.path.join(CACHE_DIR, "rbq.zip"), RBQ_ZIP_URL, st.progress(0.0))
                else:
                    st.info(t("cached_rbq"))
                rbq_src, _ = zip_member(path, "rdl01")
            else:
                st.error(t("err_rbq"))
                st.stop()

            # ---- REQ ----
            if params.USE_REQ_JOIN:
                nom_date, nom_name = None, None
                if req_mode == t("req_reuse"):
                    if not meta:
                        st.error(t("err_req_reuse"))
                        st.stop()
                    req_index = load_req_index()
                elif req_upload is not None:
                    req_src, nom_date, tmp = source_from_upload(req_upload, "Nom")
                    nom_name = req_upload.name
                    if tmp:
                        temp_files.append(tmp)
                elif req_mode == t("req_fetch"):
                    st.info(t("dl_req"))
                    try:
                        path = download_to(os.path.join(CACHE_DIR, "req.zip"), REQ_SOURCE_URL, st.progress(0.0))
                    except Exception as e:
                        st.error(t("err_req_fetch").format(err=e, url=REQ_PAGE))
                        st.stop()
                    req_src, nom_date = zip_member(path, "Nom")
                    nom_name = "req.zip"
                    temp_files.append(path)
                else:
                    st.error(t("err_req"))
                    st.stop()

                if req_src is not None:
                    # Reduce once, keep for later runs (Stage 1 reuse), then join from the index.
                    with st.spinner(t("building_index")):
                        req_index = pl.build_req_index(req_src)
                    meta = save_req_index(req_index, nom_date, nom_name)
                    st.info(t("index_saved").format(n=meta["rows"], d=meta["file_date"]))

            # ---- Run ----
            out = io.BytesIO()
            with st.spinner(t("running")):
                shortlist, corridor, counts = pl.run(rbq_src, None, params, out, log_fn=log_fn,
                                                     req_index=req_index)
            out.seek(0)

            st.success(t("done").format(corridor=len(corridor), n=len(shortlist)))
            st.download_button(t("download"), out, file_name="targets.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)

            st.subheader(t("targets"))
            st.dataframe(pl.to_output(shortlist), use_container_width=True, hide_index=True)
            with st.expander(t("corridor").format(n=len(corridor))):
                st.dataframe(pl.to_output(corridor), use_container_width=True, hide_index=True)

        except pl.EmptyResult as e:
            st.error(t("err_empty").format(f=e))
        except Exception as e:
            st.exception(e)
        finally:
            for src in (rbq_src, req_src):
                try:
                    if src is not None:
                        src.close()
                except Exception:
                    pass
            for f in temp_files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            del req_index
            gc.collect()


# ---------------------------------------------------------------------------
# Two URLs, one page function. /eng is the default.
# ---------------------------------------------------------------------------

PAGES = {
    "en": st.Page(lambda: render("en"), title=STRINGS["en"]["page_title"], url_path="eng", default=True),
    "fr": st.Page(lambda: render("fr"), title=STRINGS["fr"]["page_title"], url_path="fr"),
}

pg = st.navigation(list(PAGES.values()), position="hidden")
LANG = "fr" if pg.url_path == "fr" else "en"
st.set_page_config(
    page_title=STRINGS[LANG]["page_title"], layout="wide",
    menu_items={"About": STRINGS[LANG]["about"].format(github=GITHUB_URL)},
)
pg.run()
