"""
Interface web Streamlit — Traitement Leads Meta
Déployable gratuitement sur https://streamlit.io/cloud
"""

import json
import zipfile
import io
import pandas as pd
import streamlit as st
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG PAGE
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Traitement Leads Meta",
    page_icon="📊",
    layout="centered"
)

st.markdown("""
<style>
    .main { background-color: #1e1e2e; }
    .stApp { background-color: #1e1e2e; color: #cdd6f4; }
    h1, h2, h3 { color: #89b4fa; }
    .stButton > button {
        background-color: #89b4fa;
        color: #1e1e2e;
        font-weight: bold;
        border-radius: 8px;
        border: none;
        padding: 0.5rem 2rem;
    }
    .stButton > button:hover { background-color: #74c7ec; }
    .stDownloadButton > button {
        background-color: #a6e3a1;
        color: #1e1e2e;
        font-weight: bold;
        border-radius: 8px;
        border: none;
    }
    .stTextArea textarea { background-color: #181825; color: #cdd6f4; }
    .stFileUploader { background-color: #181825; }
    div[data-testid="stMetricValue"] { color: #a6e3a1; font-size: 2rem; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# MAPPING COLONNES
# ─────────────────────────────────────────────

MAPPING_COLONNES = {
    "Date de création": [
        "created_time", "date_created", "creation_date", "date", "timestamp",
        "date de création", "date_soumission", "submit_time"
    ],
    "Nom Prénom": [
        "full_name", "nom complet", "last_name", "nom", "name", "surname", "famille"
    ],
    "Email": [
        "email", "e-mail", "mail", "email_address", "adresse_email", "adresse mail",
        "adresse e-mail"
    ],
    "Code Postal": [
        "code postal", "post_code", "zip_code", "postal_code", "code_postal",
        "cp", "postcode", "zip", "codepostal"
    ],
    "Téléphone": [
        "numero de telephone", "numéro de téléphone", "phone_number", "phone",
        "telephone", "tel", "mobile", "portable"
    ]
}

COLONNES_SORTIE = ["Date de création", "Propriétaire", "Maison", "Gaz", "Code Postal", "Nom Prénom", "Téléphone", "Client"]

# ─────────────────────────────────────────────
# FONCTIONS METIER
# ─────────────────────────────────────────────

def lire_csv_depuis_bytes(file_bytes):
    for encoding in ["utf-16", "utf-16-le", "utf-16-be", "utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        for sep in [",", ";", "\t"]:
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), sep=sep, encoding=encoding, dtype=str)
                if len(df.columns) > 1 and not df.columns[0].startswith("\ufffd"):
                    return df, encoding, sep
            except Exception:
                continue
    raise ValueError("Impossible de lire le fichier.")


def detecter_colonne(colonnes_dispo, candidats):
    colonnes_lower = {c.lower().strip(): c for c in colonnes_dispo}
    for candidat in candidats:
        if candidat.lower() in colonnes_lower:
            return colonnes_lower[candidat.lower()]
    return None


def construire_mapping(colonnes):
    mapping = {}
    for champ, candidats in MAPPING_COLONNES.items():
        col_trouvee = detecter_colonne(colonnes, candidats)
        if col_trouvee:
            mapping[champ] = col_trouvee
    return mapping


def normaliser_lead(row, mapping, proprietaire="Propriétaire", maison="Maison", gaz="Gaz"):
    def get(champ):
        col = mapping.get(champ)
        if col and col in row.index:
            val = row[col]
            if pd.notna(val):
                v = str(val).strip()
                if champ == "Code Postal" and v.startswith("z:"):
                    v = v[2:].strip()
                if champ == "Téléphone" and v.startswith("p:"):
                    v = v[2:].strip()
                return v
        return ""
    return {
        "Date de création": get("Date de création"),
        "Propriétaire":     proprietaire,
        "Maison":           maison,
        "Gaz":              gaz,
        "Code Postal":      get("Code Postal"),
        "Nom Prénom":       get("Nom Prénom"),
        "Téléphone":        get("Téléphone"),
        "Client":           ""  # sera rempli après classification
    }


def valider_config(config_json_str):
    config = json.loads(config_json_str)
    clients = config.get("clients", [])
    if not clients:
        raise ValueError("Aucun client trouvé.")
    for c in clients:
        if "nom" not in c or "prefixes" not in c:
            raise ValueError(f"Client mal configuré : {c}")
    return clients


def detecter_zones_partagees(clients):
    """Retourne {prefix: [liste clients]} pour les zones partagées."""
    prefix_clients = {}
    for client in clients:
        for prefix in client["prefixes"]:
            prefix_clients.setdefault(prefix, []).append(client["nom"])
    return {p: noms for p, noms in prefix_clients.items() if len(noms) > 1}


def dedoublonner(leads_bruts):
    """Supprime les doublons sur email ou téléphone."""
    vus_email, vus_tel = {}, {}
    propres, doublons = [], []
    for lead in leads_bruts:
        email = lead.get("Email", "").lower().strip()
        tel   = lead.get("Téléphone", "").strip()
        doublon, raison = False, ""
        if email and email in vus_email:
            doublon = True
            raison  = f"Email en double : {email}"
        elif tel and tel in vus_tel:
            doublon = True
            raison  = f"Téléphone en double : {tel}"
        if doublon:
            lead_d = dict(lead)
            lead_d["Raison"] = raison
            doublons.append(lead_d)
        else:
            propres.append(lead)
            if email: vus_email[email] = True
            if tel:   vus_tel[tel] = True
    return propres, doublons


def classifier_leads(leads_propres, clients):
    """
    Classifie les leads avec répartition équitable sur les zones partagées.
    - Zone exclusive  → lead attribué au seul client concerné
    - Zone partagée   → leads répartis en tourniquet entre les clients concernés
    """
    zones_partagees = detecter_zones_partagees(clients)
    compteurs = {prefix: 0 for prefix in zones_partagees}

    resultats = {c["nom"]: [] for c in clients}
    resultats["Hors_Zone"] = []

    for lead in leads_propres:
        cp = str(lead.get("Code Postal", "")).strip()
        if cp.startswith("z:"):
            cp = cp[2:].strip()

        prefix_match = None
        clients_match = []
        for client in clients:
            for prefix in client["prefixes"]:
                if cp.startswith(str(prefix)):
                    prefix_match = prefix
                    clients_match = [client["nom"]]
                    break
            if prefix_match:
                break

        if prefix_match is None:
            resultats["Hors_Zone"].append(lead)
            continue

        if prefix_match in zones_partagees:
            # Zone partagée → tourniquet
            clients_concernes = zones_partagees[prefix_match]
            idx = compteurs[prefix_match] % len(clients_concernes)
            client_choisi = clients_concernes[idx]
            compteurs[prefix_match] += 1
            resultats[client_choisi].append(lead)
        else:
            resultats[clients_match[0]].append(lead)

    return resultats


def df_to_csv_bytes(df):
    return df.to_csv(index=False, encoding="utf-8-sig", sep=";").encode("utf-8-sig")


def traiter_fichiers(fichiers_bytes, clients, proprietaire="Propriétaire", maison="Maison", gaz="Gaz"):
    logs = []
    tous_leads_bruts = []

    def log(msg, level="INFO"):
        logs.append((level, msg))

    # Détecter zones partagées
    zones_partagees = detecter_zones_partagees(clients)
    if zones_partagees:
        for prefix, noms in zones_partagees.items():
            log(f"Zone partagée : {prefix} → {', '.join(noms)} (répartition équitable)", "WARNING")

    # Lecture des fichiers
    for nom_fichier, file_bytes in fichiers_bytes:
        log(f"Lecture : {nom_fichier}")
        try:
            df, encoding, sep = lire_csv_depuis_bytes(file_bytes)
            log(f"  Encodage : {encoding} | Séparateur : '{sep}'", "DEBUG")
            mapping = construire_mapping(df.columns.tolist())
            champs_manquants = [c for c in MAPPING_COLONNES if c not in mapping]
            if champs_manquants:
                log(f"  Colonnes non détectées : {champs_manquants}", "WARNING")
            for _, row in df.iterrows():
                tous_leads_bruts.append(normaliser_lead(row, mapping, proprietaire, maison, gaz))
            log(f"  ✓ {len(df)} leads lus")
        except Exception as e:
            log(f"  ✗ Erreur : {e}", "ERROR")

    # Dédoublonnage
    leads_propres, doublons = dedoublonner(tous_leads_bruts)
    log(f"Dédoublonnage : {len(doublons)} doublon(s) détecté(s)", "WARNING" if doublons else "INFO")

    # Classification
    leads = classifier_leads(leads_propres, clients)
    for cle, data in leads.items():
        if data:
            log(f"  → {cle} : {len(data)} leads", "DEBUG")

    # Remplir la colonne Client
    for cle, data in leads.items():
        for lead in data:
            lead["Client"] = cle

    # Construction DataFrames
    resultats = {cle: pd.DataFrame(data, columns=COLONNES_SORTIE) for cle, data in leads.items()}
    doublons_df = pd.DataFrame(doublons, columns=COLONNES_SORTIE + ["Raison"]) if doublons else pd.DataFrame()

    # Global
    tous = []
    for cle, data in leads.items():
        tous.extend(data)
    global_df = pd.DataFrame(tous, columns=COLONNES_SORTIE)

    return resultats, doublons_df, global_df, logs

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────

for key in ["resultats", "doublons_df", "global_df", "logs", "timestamp"]:
    if key not in st.session_state:
        st.session_state[key] = None

# ─────────────────────────────────────────────
# TITRE
# ─────────────────────────────────────────────

st.title("📊 Traitement Leads Meta")
st.markdown("**Facebook Ads — Classement automatique par code postal**")
st.divider()

# ─────────────────────────────────────────────
# ÉTAPE 1 — Configuration clients
# ─────────────────────────────────────────────

st.header("① Configuration des clients")

config_defaut = json.dumps({
  "clients": [
    {"nom": "Client_RN", "prefixes": ["24","47","66","11","09","64","65","87","23","19"]},
    {"nom": "Client_AV1",       "prefixes": ["67","68","88"]},
    {"nom": "Client_AV2",       "prefixes": ["57","54","70","88","90","67","68"]},
    {"nom": "Client_ELIE_PV_1", "prefixes": ["29","22","35","56","44"]},
    {"nom": "Client_ELIE_PV_2", "prefixes": ["85","79","86","17","16","49"]},
    {"nom": "Client_BL", "prefixes": ["16","17","33","64","65","40","47","32"]},
    {"nom": "Client_RR",        "prefixes": ["28","61","53","35","44","49","72","37","86","79","85","36","18","41","45"]},
    {"nom": "Client_AI",        "prefixes": ["22","35","56","72","53"]},
    {"nom": "Client_AV3",       "prefixes": ["28","45","27","76","58","89","60","80","02","57","54","88","53","72"]}
    {"nom": "Client_ZC_GLOBAL", "prefixes": ["81","12","37","41","36","18","85","44","26","07","38","70","25","90","15","63","43","03"]}, 
    {"nom": "Client_DS", "prefixes": ["83","84","04","26","07"]},
    {"nom": "Client_SH",        "prefixes": ["54","55","57","88","51","52","10"]}
  ]
}, indent=2, ensure_ascii=False)

config_json = st.text_area(
    "Colle ou modifie ta configuration JSON :",
    value=config_defaut,
    height=200,
    help="Ajoute ou retire des clients sans toucher au reste."
)

clients = None
try:
    clients = valider_config(config_json)
    st.success(f"✅ {len(clients)} client(s) configuré(s) : {', '.join(c['nom'] for c in clients)}")
except Exception as e:
    st.error(f"❌ Erreur de configuration : {e}")

st.divider()

# ─────────────────────────────────────────────
# VISUALISATION ZONES PARTAGÉES
# ─────────────────────────────────────────────

if clients:
    zones_partagees = detecter_zones_partagees(clients)
    if zones_partagees:
        with st.expander("🔍 Matrice des zones partagées entre clients", expanded=False):
            st.markdown("**✅ = départements en commun** entre deux clients — les leads seront répartis équitablement.")
            st.divider()

            noms_clients = [c["nom"] for c in clients]

            # Construction de la matrice : pour chaque paire de clients, quels deps en commun
            matrice = {}
            for c1 in noms_clients:
                matrice[c1] = {}
                prefixes_c1 = set(next(c["prefixes"] for c in clients if c["nom"] == c1))
                for c2 in noms_clients:
                    if c1 == c2:
                        matrice[c1][c2] = "—"
                    else:
                        prefixes_c2 = set(next(c["prefixes"] for c in clients if c["nom"] == c2))
                        communs = sorted(prefixes_c1 & prefixes_c2)
                        matrice[c1][c2] = ", ".join(communs) if communs else ""

            df_matrice = pd.DataFrame(matrice).T
            df_matrice = df_matrice[noms_clients]

            # Style : colorer les cellules non vides
            def colorier(val):
                if val == "—":
                    return "background-color: #313244; color: #6c7086;"
                elif val == "":
                    return "background-color: #1e1e2e; color: #1e1e2e;"
                else:
                    return "background-color: #f38ba8; color: #1e1e2e; font-weight: bold;"

            try:
                styled = df_matrice.style.map(colorier)
            except AttributeError:
                styled = df_matrice.style.applymap(colorier)

            st.dataframe(styled, use_container_width=True)

            st.divider()
            st.markdown("**Récapitulatif des départements partagés :**")
            rows = []
            for prefix, noms in sorted(zones_partagees.items()):
                rows.append({
                    "Département": prefix,
                    "Clients concernés": " / ".join(noms),
                    "Nb clients": len(noms)
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    else:
        with st.expander("🔍 Zones partagées", expanded=False):
            st.success("✅ Aucune zone partagée — chaque département est exclusif à un seul client.")

st.divider()

# ─────────────────────────────────────────────
# VALEURS FIXES
# ─────────────────────────────────────────────

st.header("② Valeurs fixes des leads")
col_a, col_b, col_c = st.columns(3)
with col_a:
    proprietaire = st.text_input("Propriétaire", value="Propriétaire")
with col_b:
    maison = st.text_input("Maison", value="Maison")
with col_c:
    gaz = st.text_input("Gaz", value="Gaz")

st.divider()

# ─────────────────────────────────────────────
# ÉTAPE 2 — Upload des fichiers
# ─────────────────────────────────────────────

st.header("③ Charger les fichiers CSV Meta")

fichiers_uploades = st.file_uploader(
    "Glisse tes fichiers CSV ici (plusieurs fichiers acceptés)",
    type=["csv"],
    accept_multiple_files=True
)

if fichiers_uploades:
    st.info(f"📁 {len(fichiers_uploades)} fichier(s) chargé(s) : {', '.join(f.name for f in fichiers_uploades)}")

st.divider()

# ─────────────────────────────────────────────
# ÉTAPE 3 — Lancement
# ─────────────────────────────────────────────

st.header("④ Lancer le traitement")

if st.button("▶  Lancer le traitement", disabled=(not fichiers_uploades or clients is None)):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fichiers_bytes = [(f.name, f.read()) for f in fichiers_uploades]

    log_container = st.expander("📋 Journal d'exécution", expanded=True)
    progress = st.progress(0, text="Démarrage...")

    with st.spinner("Traitement en cours..."):
        progress.progress(10, "Lecture des fichiers...")
        resultats, doublons_df, global_df, logs = traiter_fichiers(fichiers_bytes, clients, proprietaire, maison, gaz)
        progress.progress(90, "Génération des fichiers de sortie...")

        st.session_state.resultats  = resultats
        st.session_state.doublons_df = doublons_df
        st.session_state.global_df  = global_df
        st.session_state.logs       = logs
        st.session_state.timestamp  = timestamp

    progress.progress(100, "✅ Terminé !")

    with log_container:
        for level, msg in logs:
            if level == "ERROR":    st.error(msg)
            elif level == "WARNING": st.warning(msg)
            elif level == "DEBUG":  st.caption(msg)
            else:                   st.success(msg)

# ─────────────────────────────────────────────
# RÉSULTATS (après traitement ou refresh)
# ─────────────────────────────────────────────

if st.session_state.resultats is not None:
    resultats   = st.session_state.resultats
    doublons_df = st.session_state.doublons_df
    global_df   = st.session_state.global_df
    timestamp   = st.session_state.timestamp

    st.divider()
    st.header("⑤ Récapitulatif")

    total_leads = sum(len(df) for df in resultats.values())
    nb_doublons = len(doublons_df) if not doublons_df.empty else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("Total leads valides", total_leads)
    col2.metric("Doublons détectés", nb_doublons)
    col3.metric("Fichiers générés", len(resultats))

    recap_data = []
    for cle, df in resultats.items():
        recap_data.append({"Client": cle, "Leads": len(df), "Statut": "✅ OK" if len(df) > 0 else "— Vide"})
    if nb_doublons > 0:
        recap_data.append({"Client": "⚠ Doublons", "Leads": nb_doublons, "Statut": "fichier séparé"})
    recap_data.append({"Client": "🌐 Global", "Leads": total_leads, "Statut": "tous les leads valides"})
    st.table(recap_data)

    st.divider()
    st.header("⑥ Télécharger les fichiers")

    cols = st.columns(2)
    for i, (cle, df) in enumerate(resultats.items()):
        with cols[i % 2]:
            st.download_button(
                label=f"⬇ {cle} ({len(df)} leads)",
                data=df_to_csv_bytes(df),
                file_name=f"{cle}_{timestamp}.csv",
                mime="text/csv",
                key=f"dl_{cle}"
            )

    if not doublons_df.empty:
        st.download_button(
            label=f"⬇ Doublons ({len(doublons_df)} leads)",
            data=df_to_csv_bytes(doublons_df),
            file_name=f"leads_doublons_{timestamp}.csv",
            mime="text/csv",
            key="dl_doublons"
        )

    st.download_button(
        label=f"⬇ Fichier Global ({len(global_df)} leads)",
        data=df_to_csv_bytes(global_df),
        file_name=f"leads_global_{timestamp}.csv",
        mime="text/csv",
        key="dl_global"
    )

    st.divider()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for cle, df in resultats.items():
            zf.writestr(f"{cle}_{timestamp}.csv", df_to_csv_bytes(df).decode("utf-8-sig"))
        if not doublons_df.empty:
            zf.writestr(f"leads_doublons_{timestamp}.csv", df_to_csv_bytes(doublons_df).decode("utf-8-sig"))
        zf.writestr(f"leads_global_{timestamp}.csv", df_to_csv_bytes(global_df).decode("utf-8-sig"))

    st.download_button(
        label="📦 Tout télécharger en ZIP",
        data=zip_buffer.getvalue(),
        file_name=f"leads_meta_{timestamp}.zip",
        mime="application/zip",
        key="dl_zip"
    )
