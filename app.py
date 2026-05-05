"""
Fonctions métier de traitement des leads Meta.
Utilisé par app.py (Streamlit) et traitement_leads_meta.py (tkinter).
"""

import io
import json
import pandas as pd
from datetime import datetime

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

COLONNES_SORTIE = ["Date de création", "Nom Prénom", "Email", "Téléphone", "Code Postal"]


def lire_csv_depuis_bytes(file_bytes):
    """Lit un CSV depuis des bytes (upload Streamlit) avec détection auto encodage/séparateur."""
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


def normaliser_lead(row, mapping):
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
    return {champ: get(champ) for champ in COLONNES_SORTIE}


def detecter_zones_partagees(clients):
    """Retourne un dict {prefix: [liste clients]} pour les zones partagées entre plusieurs clients."""
    prefix_clients = {}
    for client in clients:
        for prefix in client["prefixes"]:
            prefix_clients.setdefault(prefix, []).append(client["nom"])
    return {p: noms for p, noms in prefix_clients.items() if len(noms) > 1}


def classifier_leads_avec_repartition(leads_bruts, clients):
    """
    Classifie les leads en gérant la répartition équitable sur les zones partagées.
    - Zones exclusives : lead attribué au seul client concerné
    - Zones partagées : leads répartis en alternance entre les clients concernés
    """
    zones_partagees = detecter_zones_partagees(clients)
    
    # Compteurs pour la répartition équitable par zone partagée
    compteurs_zones = {prefix: 0 for prefix in zones_partagees}

    resultats = {c["nom"]: [] for c in clients}
    resultats["Hors_Zone"] = []

    for lead in leads_bruts:
        cp = str(lead.get("Code Postal", "")).strip()
        if cp.startswith("z:"):
            cp = cp[2:].strip()

        prefix_match = None
        for client in clients:
            for prefix in client["prefixes"]:
                if cp.startswith(str(prefix)):
                    prefix_match = prefix
                    break
            if prefix_match:
                break

        if prefix_match is None:
            resultats["Hors_Zone"].append(lead)
            continue

        if prefix_match in zones_partagees:
            # Zone partagée → répartition en tourniquet
            clients_concernes = zones_partagees[prefix_match]
            idx = compteurs_zones[prefix_match] % len(clients_concernes)
            client_choisi = clients_concernes[idx]
            compteurs_zones[prefix_match] += 1
            resultats[client_choisi].append(lead)
        else:
            # Zone exclusive → client unique
            for client in clients:
                for prefix in client["prefixes"]:
                    if cp.startswith(str(prefix)):
                        resultats[client["nom"]].append(lead)
                        break
                else:
                    continue
                break

    return resultats


def dedoublonner(leads):
    vus_email, vus_tel = {}, {}
    doublons, propres = [], {cle: [] for cle in leads}

    for cle, data in leads.items():
        for lead in data:
            email = lead.get("Email", "").lower().strip()
            tel   = lead.get("Téléphone", "").strip()
            doublon, raison = False, ""

            if email and email in vus_email:
                doublon = True
                raison  = f"Email en double : {email} (déjà vu dans {vus_email[email]})"
            elif tel and tel in vus_tel:
                doublon = True
                raison  = f"Téléphone en double : {tel} (déjà vu dans {vus_tel[tel]})"

            if doublon:
                lead_d = dict(lead)
                lead_d["Raison"] = raison
                lead_d["Client origine"] = cle
                doublons.append(lead_d)
            else:
                propres[cle].append(lead)
                if email: vus_email[email] = cle
                if tel:   vus_tel[tel] = cle

    return propres, doublons


def valider_config(config_json_str):
    """Valide et retourne la liste des clients depuis un JSON string."""
    config = json.loads(config_json_str)
    clients = config.get("clients", [])
    if not clients:
        raise ValueError("Aucun client trouvé.")
    for c in clients:
        if "nom" not in c or "prefixes" not in c:
            raise ValueError(f"Client mal configuré : {c}")
    return clients


def traiter_fichiers(fichiers_bytes, clients, log_callback=None):
    """
    Traite une liste de fichiers (bytes) et retourne les DataFrames résultants.
    fichiers_bytes : list of (nom, bytes)
    Retourne : dict {nom_client: DataFrame}, doublons_df, global_df, logs[]
    """
    tous_leads_bruts = []
    logs = []

    def log(msg, level="INFO"):
        logs.append((level, msg))
        if log_callback:
            log_callback(level, msg)

    # Détecter et logger les zones partagées
    zones_partagees = detecter_zones_partagees(clients)
    if zones_partagees:
        for prefix, noms in zones_partagees.items():
            log(f"  Zone partagée détectée : {prefix} → {', '.join(noms)} (répartition équitable)", "WARNING")

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
                lead = normaliser_lead(row, mapping)
                tous_leads_bruts.append(lead)

            log(f"  ✓ {len(df)} leads traités")
        except Exception as e:
            log(f"  ✗ Erreur : {e}", "ERROR")

    # Dédoublonnage sur tous les leads bruts avant classification
    leads_dict_brut = {"_tous": tous_leads_bruts}
    leads_dict_brut, doublons = dedoublonner(leads_dict_brut)
    leads_propres = leads_dict_brut["_tous"]
    log(f"Dédoublonnage : {len(doublons)} doublon(s) détecté(s)", "WARNING" if doublons else "INFO")

    # Classification avec répartition équitable sur zones partagées
    leads = classifier_leads_avec_repartition(leads_propres, clients)

    # Logger la répartition finale
    for cle, data in leads.items():
        if len(data) > 0:
            log(f"  → {cle} : {len(data)} leads", "DEBUG")

    # Dédoublonnage déjà fait — on réinitialise pour compatibilité
    doublons_list = doublons
    # Construction des DataFrames
    resultats = {}
    for cle, data in leads.items():
        resultats[cle] = pd.DataFrame(data, columns=COLONNES_SORTIE)

    doublons_df = pd.DataFrame(doublons_list, columns=COLONNES_SORTIE + ["Client origine", "Raison"]) if doublons_list else pd.DataFrame()

    # Global
    tous = []
    for cle, data in leads.items():
        for lead in data:
            l = dict(lead)
            l["Client"] = cle
            tous.append(l)
    global_df = pd.DataFrame(tous, columns=["Client"] + COLONNES_SORTIE)

    return resultats, doublons_df, global_df, logs


def df_to_csv_bytes(df):
    """Convertit un DataFrame en bytes CSV téléchargeable."""
    return df.to_csv(index=False, encoding="utf-8-sig", sep=";").encode("utf-8-sig")
