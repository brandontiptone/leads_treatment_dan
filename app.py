"""
Interface web Streamlit — Traitement Leads Meta
Déployable gratuitement sur https://streamlit.io/cloud
"""

import json
import zipfile
import io
import streamlit as st
from datetime import datetime
from traitement_core import traiter_fichiers, valider_config, df_to_csv_bytes

# ─────────────────────────────────────────────
# CONFIG PAGE
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Traitement Leads Meta",
    page_icon="📊",
    layout="centered"
)

# ─────────────────────────────────────────────
# STYLE
# ─────────────────────────────────────────────

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
        {"nom": "Client_1", "prefixes": ["87", "23", "19"]},
        {"nom": "Client_2", "prefixes": ["08", "10", "51"]},
        {"nom": "Client_3", "prefixes": ["12", "32", "45"]}
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
# ÉTAPE 2 — Upload des fichiers
# ─────────────────────────────────────────────

st.header("② Charger les fichiers CSV Meta")

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

st.header("③ Lancer le traitement")

if st.button("▶  Lancer le traitement", disabled=(not fichiers_uploades or clients is None)):

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Préparation des fichiers
    fichiers_bytes = [(f.name, f.read()) for f in fichiers_uploades]

    # Zone de logs
    log_container = st.expander("📋 Journal d'exécution", expanded=True)
    log_lines = []

    def log_callback(level, msg):
        log_lines.append((level, msg))

    # Barre de progression
    progress = st.progress(0, text="Démarrage...")

    with st.spinner("Traitement en cours..."):
        progress.progress(10, "Lecture des fichiers...")
        resultats, doublons_df, global_df, logs = traiter_fichiers(
            fichiers_bytes, clients, log_callback
        )
        progress.progress(90, "Génération des fichiers de sortie...")

    progress.progress(100, "✅ Terminé !")

    # Affichage des logs
    with log_container:
        for level, msg in logs:
            if level == "ERROR":
                st.error(msg)
            elif level == "WARNING":
                st.warning(msg)
            elif level == "DEBUG":
                st.caption(msg)
            else:
                st.success(msg)

    st.divider()

    # ─────────────────────────────────────────────
    # RÉCAPITULATIF
    # ─────────────────────────────────────────────

    st.header("④ Récapitulatif")

    total_leads = sum(len(df) for df in resultats.values())
    nb_doublons = len(doublons_df) if not doublons_df.empty else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("Total leads valides", total_leads)
    col2.metric("Doublons détectés", nb_doublons)
    col3.metric("Fichiers traités", len(fichiers_uploades))

    # Tableau récap
    recap_data = []
    for cle, df in resultats.items():
        recap_data.append({
            "Client": cle,
            "Leads": len(df),
            "Statut": "✅ OK" if len(df) > 0 else "— Vide"
        })
    if nb_doublons > 0:
        recap_data.append({"Client": "⚠ Doublons", "Leads": nb_doublons, "Statut": "fichier séparé"})
    recap_data.append({"Client": "🌐 Global", "Leads": total_leads, "Statut": "tous les leads valides"})

    st.table(recap_data)

    st.divider()

    # ─────────────────────────────────────────────
    # TÉLÉCHARGEMENTS
    # ─────────────────────────────────────────────

    st.header("⑤ Télécharger les fichiers")

    # Boutons individuels par client
    cols = st.columns(2)
    for i, (cle, df) in enumerate(resultats.items()):
        with cols[i % 2]:
            st.download_button(
                label=f"⬇ {cle} ({len(df)} leads)",
                data=df_to_csv_bytes(df),
                file_name=f"{cle}_{timestamp}.csv",
                mime="text/csv"
            )

    # Doublons
    if not doublons_df.empty:
        st.download_button(
            label=f"⬇ Doublons ({len(doublons_df)} leads)",
            data=df_to_csv_bytes(doublons_df),
            file_name=f"leads_doublons_{timestamp}.csv",
            mime="text/csv"
        )

    # Global
    st.download_button(
        label=f"⬇ Fichier Global ({len(global_df)} leads)",
        data=df_to_csv_bytes(global_df),
        file_name=f"leads_global_{timestamp}.csv",
        mime="text/csv"
    )

    # ZIP tout
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
        mime="application/zip"
    )
