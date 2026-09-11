"""
Acquisition target screen (RBQ x REQ) - Streamlit front end.

Run locally:   streamlit run app.py
Deploy:        push to GitHub, point Streamlit Community Cloud at app.py
"""

import gc
import io
import os
import tempfile
import time
import zipfile

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

# The REQ zip is not hosted on Donnees Quebec; the portal redirects to this
# page on the Registraire's site. Automatic fetch is attempted, but the page
# may not serve the zip directly, in which case the user uploads Nom.csv.
REQ_SOURCE_URL = ("https://www.registreentreprises.gouv.qc.ca/RQAnonymeGR/GR/GR03/"
                  "GR03A2_22A_PIU_RecupDonnPub_PC/FichierDonneesOuvertes.aspx")
REQ_PAGE = "https://www.donneesquebec.ca/recherche/dataset/registre-des-entreprises"

REQ_CACHE_SECONDS = 14 * 24 * 3600   # register is updated twice a month
RBQ_CACHE_SECONDS = 24 * 3600        # licence list is updated daily

# RBQ licence sub-categories (Regie du batiment du Quebec, annexes I to III).
# Source: rbq.gouv.qc.ca, "Liste des sous-categories", consulted 2026-09-11.
SUBCATEGORIES = [
    ("Annexe I - Entrepreneur general", [
        ("1.1.1", "Batiments residentiels neufs vises a un plan de garantie, classe I"),
        ("1.1.2", "Batiments residentiels neufs vises a un plan de garantie, classe II"),
        ("1.2", "Petits batiments"),
        ("1.3", "Batiments de tout genre"),
        ("1.4", "Routes et canalisation"),
        ("1.5", "Structures d'ouvrages de genie civil"),
        ("1.6", "Ouvrages de genie civil immerges"),
        ("1.7", "Telecommunication, transport, transformation et distribution d'energie electrique"),
        ("1.8", "Installation d'equipements petroliers"),
        ("1.9", "Mecanique du batiment"),
        ("1.10", "Remontees mecaniques"),
    ]),
    ("Annexe II - Entrepreneur specialise", [
        ("2.1", "Puits fores"),
        ("2.2", "Ouvrages de captage d'eau non fores"),
        ("2.3", "Systemes de pompage des eaux souterraines"),
        ("2.4", "Systemes d'assainissement autonome"),
        ("2.6", "Pieux et fondations speciales"),
        ("2.8", "Sautage"),
        ("3.1", "Structures de beton"),
        ("4.1", "Structures de maconnerie"),
        ("5.1", "Structures metalliques et elements prefabriques de beton"),
        ("6.1", "Charpentes de bois"),
        ("10.0", "Systemes de chauffage localise a combustible solide"),
        ("11.1", "Tuyauterie industrielle ou institutionnelle sous pression"),
        ("13.1", "Protection contre la foudre"),
        ("13.2", "Systemes d'alarme incendie"),
        ("13.3", "Systemes d'extinction d'incendie"),
        ("13.4", "Systemes localises d'extinction incendie"),
        ("14.1", "Ascenseurs et monte-charges"),
        ("14.2", "Appareils elevateurs pour personnes a mobilite reduite"),
        ("14.3", "Autres types d'appareils elevateurs"),
        ("15.1", "Systemes de chauffage a air pulse"),
        ("15.2", "Systemes de bruleurs au gaz naturel"),
        ("15.3", "Systemes de bruleurs a l'huile"),
        ("15.4", "Systemes de chauffage hydronique"),
        ("15.5", "Plomberie"),
        ("15.6", "Propane"),
        ("15.7", "Ventilation residentielle"),
        ("15.8", "Ventilation"),
        ("15.9", "Petits systemes de refrigeration"),
        ("15.10", "Refrigeration"),
        ("16.0", "Electricite"),
        ("17.1", "Instrumentation, controle et regulation"),
    ]),
    ("Annexe III - Entrepreneur specialise", [
        ("2.5", "Excavation et terrassement"),
        ("2.7", "Travaux d'emplacement"),
        ("3.2", "Petits ouvrages de beton"),
        ("4.2", "Maconnerie non structurale, marbre et ceramique"),
        ("5.2", "Ouvrages metalliques"),
        ("6.2", "Travaux de bois et plastique"),
        ("7.0", "Isolation, etancheite, couvertures et revetements exterieurs"),
        ("8.0", "Portes et fenetres"),
        ("9.0", "Travaux de finition"),
        ("11.2", "Equipements et produits speciaux"),
        ("12.0", "Armoires et comptoirs usines"),
        ("13.5", "Installations speciales ou prefabriquees"),
        ("17.2", "Intercommunication, telephonie et surveillance"),
    ]),
]

SUBCATEGORY_HELP = "\n\n".join(
    f"**{annexe}**  \n" + "  \n".join(f"{code} : {title}" for code, title in items)
    for annexe, items in SUBCATEGORIES
)

CACHE_DIR = os.path.join(tempfile.gettempdir(), "rbq_req_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


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
    """Open one member of a zip on disk as a streaming file object."""
    zf = zipfile.ZipFile(path)
    names = [n for n in zf.namelist() if hint.lower() in n.lower() and n.lower().endswith(".csv")]
    if not names:
        raise FileNotFoundError(f"No CSV matching '{hint}' inside the zip. Members: {zf.namelist()}")
    return zf.open(names[0])


def source_from_upload(uploaded, hint: str):
    """Uploaded file may be a CSV or a zip containing the CSV. Returns a file object
    plus a temp path to delete afterwards (or None)."""
    if uploaded.name.lower().endswith(".zip"):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=CACHE_DIR)
        tmp.write(uploaded.getbuffer())
        tmp.close()
        return zip_member(tmp.name, hint), tmp.name
    return io.BytesIO(uploaded.getvalue()), None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Acquisition target screen (RBQ x REQ)", layout="wide")
st.title("Acquisition target screen: RBQ licences x REQ register")
st.caption("Deterministic filter, join, and score. Invents nothing. Every step reports its row count.")

defaults = pl.Params()

with st.sidebar:
    st.header("Parameters")
    SUBCATEGORY = st.text_input("SUBCATEGORY (RBQ sous-categorie)", defaults.SUBCATEGORY,
                                help="Type the code exactly as it appears in the RBQ file "
                                     "(16 = electricite, 15.5 = plomberie).\n\n" + SUBCATEGORY_HELP)
    with st.expander("Sous-categories RBQ (reference)"):
        for annexe, items in SUBCATEGORIES:
            st.markdown(f"**{annexe}**")
            st.markdown("  \n".join(f"`{code}` {title}" for code, title in items))
    muni_text = st.text_area("MUNICIPALITIES (one per line)", "\n".join(defaults.MUNICIPALITIES),
                             height=260, help="Accent- and case-insensitive match on the RBQ 'Municipalite' column.")
    CURRENT_YEAR = st.number_input("CURRENT_YEAR", 2000, 2100, defaults.CURRENT_YEAR)
    AGE_THRESHOLD = st.number_input("AGE_THRESHOLD", 0, 100, defaults.AGE_THRESHOLD)
    STRONG_AGE_THRESHOLD = st.number_input("STRONG_AGE_THRESHOLD", 0, 100, defaults.STRONG_AGE_THRESHOLD)
    HIGH_BOND = st.number_input("HIGH_BOND (CAD)", 0, 1_000_000, defaults.HIGH_BOND, step=1000)
    SUBCAT_THRESHOLD = st.number_input("SUBCAT_THRESHOLD", 0, 50, defaults.SUBCAT_THRESHOLD)
    OUTPUT_ROWS = st.number_input("OUTPUT_ROWS", 1, 1000, defaults.OUTPUT_ROWS)
    USE_REQ_JOIN = st.checkbox("Join REQ register (registration year)", defaults.USE_REQ_JOIN,
                               help="REQ open data is licensed CC-BY-NC-SA 4.0 (non-commercial). "
                                    "Untick to run RBQ-only; Age and age-based score will be blank.")

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
    st.subheader("File 1: RBQ active licences")
    st.markdown(f"Source: [Donnees Quebec]({RBQ_PAGE}), licence CC-BY 4.0, updated daily.")
    rbq_mode = st.radio("RBQ source", ["Fetch latest automatically", "Upload file (.csv or .zip)"],
                        horizontal=True, label_visibility="collapsed")
    rbq_upload = None
    if rbq_mode.startswith("Upload"):
        rbq_upload = st.file_uploader("rdl01_ExtractionDonneesOuvertes (.csv or .zip)", type=["csv", "zip"])

with col2:
    st.subheader("File 2: REQ register (Nom.csv)")
    st.markdown(f"Source: [Donnees Quebec]({REQ_PAGE}), licence CC-BY-NC-SA 4.0, updated twice monthly. "
                "Only Nom.csv is used; the other five files in the zip are ignored.")
    req_mode = st.radio("REQ source", ["Try automatic fetch", "Upload file (Nom.csv or the REQ .zip)"],
                        horizontal=True, label_visibility="collapsed", disabled=not USE_REQ_JOIN)
    req_upload = None
    if req_mode.startswith("Upload") and USE_REQ_JOIN:
        req_upload = st.file_uploader("Nom.csv or the full REQ zip", type=["csv", "zip"])

run = st.button("Run screen", type="primary", use_container_width=True)

if run:
    log_box = st.empty()
    lines = []

    def log_fn(s):
        lines.append(s)
        log_box.code("\n".join(lines))

    temp_files = []
    rbq_src = req_src = None
    try:
        # ---- RBQ ----
        if rbq_upload is not None:
            rbq_src, tmp = source_from_upload(rbq_upload, "rdl01")
            if tmp:
                temp_files.append(tmp)
        elif rbq_mode.startswith("Fetch"):
            path = _cached_path("rbq.zip", RBQ_CACHE_SECONDS)
            if path is None:
                st.info("Downloading RBQ licence list from Donnees Quebec...")
                path = download_to(os.path.join(CACHE_DIR, "rbq.zip"), RBQ_ZIP_URL, st.progress(0.0))
            else:
                st.info("Using RBQ file cached today.")
            rbq_src = zip_member(path, "rdl01")
        else:
            st.error("Upload the RBQ file or choose automatic fetch.")
            st.stop()

        # ---- REQ ----
        if params.USE_REQ_JOIN:
            if req_upload is not None:
                req_src, tmp = source_from_upload(req_upload, "Nom")
                if tmp:
                    temp_files.append(tmp)
            elif req_mode.startswith("Try"):
                path = _cached_path("req.zip", REQ_CACHE_SECONDS)
                if path is None:
                    st.info("Attempting to download the REQ register from the Registraire...")
                    try:
                        path = download_to(os.path.join(CACHE_DIR, "req.zip"), REQ_SOURCE_URL, st.progress(0.0))
                    except Exception as e:
                        st.error(
                            "Automatic REQ download failed: "
                            f"{e}\n\nThe Registraire does not serve the zip from a stable link. "
                            f"Download it manually from the [dataset page]({REQ_PAGE}), then switch "
                            "the REQ source to Upload and provide Nom.csv or the zip."
                        )
                        st.stop()
                else:
                    st.info("Using REQ file cached within the last 14 days.")
                req_src = zip_member(path, "Nom")
            else:
                st.error("Upload Nom.csv or choose automatic fetch, or untick the REQ join.")
                st.stop()

        # ---- Run ----
        out = io.BytesIO()
        with st.spinner("Running pipeline..."):
            shortlist, corridor, counts = pl.run(rbq_src, req_src, params, out, log_fn=log_fn)
        out.seek(0)

        st.success(f"Done. {len(corridor):,} companies in corridor, top {len(shortlist)} shortlisted.")
        st.download_button("Download targets.xlsx", out, file_name="targets.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)

        st.subheader("Targets")
        st.dataframe(pl.to_output(shortlist), use_container_width=True, hide_index=True)
        with st.expander(f"Corridor ({len(corridor):,} rows)"):
            st.dataframe(pl.to_output(corridor), use_container_width=True, hide_index=True)

    except pl.EmptyResult as e:
        st.error(f"Stopped: the filter '{e}' emptied the dataset. Check the parameter and the exact "
                 "spelling of the values in the source file.")
    except Exception as e:
        st.exception(e)
    finally:
        # Release memory: close streams, delete per-run temp files, collect.
        for s in (rbq_src, req_src):
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass
        for f in temp_files:
            try:
                os.remove(f)
            except OSError:
                pass
        gc.collect()
