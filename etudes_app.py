import base64
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

load_dotenv()
claude = anthropic.Anthropic()
# La clé doit être passée à la main : ElevenLabs la cherche avant que load_dotenv() ne la charge.
voix = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

MODELE = "claude-opus-5-5"
VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
# Si Claude refuse une demande, l'API relance automatiquement sur un autre modèle.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

# --- Sauvegarde : une base de données avec un compte par élève ---
DOSSIER = Path(__file__).parent / "donnees"
DOSSIER.mkdir(exist_ok=True)
FICHIER_BASE = DOSSIER / "revisia.db"
FICHIER_CONTACTS = DOSSIER / "contacts.csv"
# Case jamais cochée d'avance : l'accord pour la pub doit être un vrai choix de l'élève (RGPD).
CONSENTEMENT = "J'accepte de recevoir les nouveautés de Révis'IA par email (désactivable à tout moment dans mon profil)."
CONSENTEMENT_MOINS_15 = " J'ai moins de 15 ans : un parent est d'accord."

THEMES = ["🌙 Sombre", "☀️ Clair"]
# Le thème de Streamlit est commun à tout le serveur : le mode clair est donc appliqué
# par élève avec du CSS, pour ne pas changer les couleurs des autres élèves connectés.
CSS_CLAIR = """
<style>
.stApp, [data-testid="stHeader"], [data-testid="stBottom"], [data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"] { background-color: #ffffff !important; }
[data-testid="stSidebar"], [data-testid="stSidebarContent"] { background-color: #f4f4f4 !important; }
.stApp p, .stApp li, .stApp label, .stApp span, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp td, .stApp th, .stApp summary, .accueil, div[role="dialog"] * { color: #0d0d0d !important; }
.stApp input, .stApp textarea, [data-baseweb="select"] > div, [data-baseweb="input"],
[data-testid="stChatInput"], [data-testid="stChatInput"] > div, [data-testid="stFileUploaderDropzone"],
[data-baseweb="popover"] > div, div[role="dialog"] { background-color: #f4f4f4 !important; color: #0d0d0d !important; }
[data-testid="stChatMessage"] { background-color: #f7f7f8 !important; }
.stApp button[kind="primary"] *, .stApp button[kind="primaryFormSubmit"] *, .avatar { color: #ffffff !important; }
</style>
"""

CLASSES = ["6e", "5e", "4e", "3e", "Seconde", "Première", "Terminale",
           "BTS / BUT", "Licence", "Master", "Prépa"]
MATIERES = ["Maths", "Français", "Histoire-Géo", "SVT", "Physique-Chimie", "Anglais",
            "Espagnol", "Allemand", "Philosophie", "SES", "NSI / Informatique", "Technologie"]
NIVEAUX = {
    "😣 Je galère": "il a du mal : explique très progressivement, avec des mots simples, "
                   "beaucoup d'exemples concrets, rassure-le et vérifie chaque étape",
    "🙂 Moyen": "niveau correct : explications claires, consolide les bases et propose des exercices",
    "💪 Je suis bon": "il est à l'aise : sois plus concis, va plus loin et propose-lui des défis",
}
TYPES_FICHIERS = ["png", "jpg", "jpeg", "webp", "gif", "pdf", "txt", "md"]

CONSIGNE_SCHEMA = """Quand un schéma aide à comprendre, dessine-le en SVG dans un bloc ```svg ... ```
avec viewBox="0 0 800 500", un fond blanc, des couleurs douces, des textes lisibles
(font-size ≥ 14, font-family sans-serif), des légendes et des flèches claires."""

OUTILS = {
    "💬 Discuter": {"effort": "low", "consigne": ""},
    "📝 Fiche de révision": {"effort": "high", "consigne": """
Pour ce message, crée une fiche de révision de très haute qualité sur le sujet demandé
(ou sur le document envoyé), en Markdown, avec exactement ces parties :
# 📌 <Titre>
## 🎯 L'essentiel en 3 phrases
## 📖 Définitions clés (tableau : Notion | Définition)
## 🧩 Le cours, étape par étape (titres courts, puces, **mots-clés en gras**)
## 🧮 Formules / dates / chiffres à retenir (si pertinent)
## 🖼️ Schéma (un schéma SVG obligatoire)
## ⚠️ Pièges fréquents
## 💡 Astuces mémo (moyens mnémotechniques)
## ✅ Auto-évaluation (5 questions, réponses cachées dans des balises <details>)"""},
    "🎨 Schéma": {"effort": "medium", "consigne": """
Pour ce message, réponds surtout par un schéma SVG soigné (schéma légendé, carte mentale,
frise ou diagramme selon ce qui convient le mieux), suivi de 2 ou 3 phrases d'explication."""},
    "🧠 Quiz": {"effort": "medium", "consigne": """
Pour ce message, crée un QCM de 5 questions (4 choix chacune) sur le sujet demandé,
ou sur ce qui vient d'être étudié dans la conversation. Adapte la difficulté au niveau de l'élève."""},
}

LECTURE = """Tu transformes un texte écrit en texte qui sera lu à voix haute par une synthèse vocale
française, comme un professeur qui parle à son élève.
- Écris chaque formule, symbole et notation en toutes lettres, comme un humain le dirait. Exemples :
  x² → « x au carré », x³ → « x au cube », xⁿ → « x puissance n », = → « égale »,
  ≠ → « est différent de », ≤ → « inférieur ou égal à », √2 → « racine de deux »,
  1/2 ou \\frac{1}{2} → « un demi », a/b → « a sur b », 90° → « quatre-vingt-dix degrés »,
  \\hat{ABC} → « l'angle A B C », \\vec{AB} → « le vecteur A B », f'(x) → « f prime de x »,
  → → « donne », π → « pi », % → « pour cent », km/h → « kilomètres par heure »,
  H₂O → « H deux O », XVIe siècle → « seizième siècle », Louis XIV → « Louis quatorze ».
  Les noms de points ou de segments se lisent lettre par lettre (AB → « A B »).
- Retire le Markdown, les emojis et les balises ; reformule les tableaux en phrases ;
  à la place de « (schéma) », dis « regarde le schéma ».
- Garde exactement le même contenu et le même ton, sans rien ajouter.
Réponds uniquement avec le texte à lire."""

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "choix": {"type": "array", "items": {"type": "string"}},
                    "bonne_reponse": {"type": "integer"},
                    "explication": {"type": "string"},
                },
                "required": ["question", "choix", "bonne_reponse", "explication"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

# --- Apparence façon ChatGPT ---
st.set_page_config(page_title="Révis'IA", page_icon="🎓", layout="centered")
st.markdown(
    """
    <style>
    .block-container { max-width: 820px; }
    .accueil { text-align: center; font-size: 2rem; font-weight: 500; margin: 22vh 0 1.5rem; }
    .titre-connexion { text-align: center; font-size: 2.6rem; font-weight: 600; margin-top: 8vh; }
    [data-testid="stSidebar"] .stButton button { justify-content: flex-start; }
    .avatar { display: inline-flex; align-items: center; justify-content: center;
              width: 34px; height: 34px; border-radius: 50%; background: #8e5bd8;
              color: white; font-weight: 600; }
    .avatar.grand { width: 110px; height: 110px; font-size: 3rem; }
    [data-testid="stSidebar"] [data-testid="stImage"] img,
    [data-testid="stDialog"] [data-testid="stImage"] img { border-radius: 50%; object-fit: cover; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Base de données ---
def requete(sql, params=()):
    connexion = sqlite3.connect(FICHIER_BASE)
    connexion.row_factory = sqlite3.Row
    try:
        with connexion:
            return connexion.execute(sql, params).fetchall()
    finally:
        connexion.close()


requete("""CREATE TABLE IF NOT EXISTS comptes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    mot_de_passe TEXT NOT NULL,
    profil TEXT NOT NULL DEFAULT '{}',
    photo BLOB,
    cree_le TEXT NOT NULL)""")
requete("""CREATE TABLE IF NOT EXISTS sessions (
    jeton TEXT PRIMARY KEY,
    compte_id INTEGER NOT NULL,
    expire TEXT NOT NULL)""")
requete("""CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    compte_id INTEGER NOT NULL,
    titre TEXT NOT NULL,
    date TEXT NOT NULL,
    messages TEXT NOT NULL)""")


# --- Comptes ---
def email_valide(email):
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", email) is not None


def hacher(mot_de_passe, sel=None):
    """Le mot de passe n'est jamais stocké en clair, seulement son empreinte."""
    sel = sel or os.urandom(16)
    empreinte = hashlib.scrypt(mot_de_passe.encode(), salt=sel, n=2**14, r=8, p=1)
    return f"{sel.hex()}${empreinte.hex()}"


def mot_de_passe_correct(mot_de_passe, stocke):
    sel = bytes.fromhex(stocke.split("$")[0])
    return hmac.compare_digest(hacher(mot_de_passe, sel), stocke)


def creer_compte(email, mot_de_passe):
    try:
        requete("INSERT INTO comptes (email, mot_de_passe, cree_le) VALUES (?, ?, ?)",
                (email, hacher(mot_de_passe), datetime.now().isoformat()))
    except sqlite3.IntegrityError:
        return None  # email déjà utilisé
    return requete("SELECT id FROM comptes WHERE email = ?", (email,))[0]["id"]


def verifier_connexion(email, mot_de_passe):
    lignes = requete("SELECT id, mot_de_passe FROM comptes WHERE email = ?", (email,))
    if lignes and mot_de_passe_correct(mot_de_passe, lignes[0]["mot_de_passe"]):
        return lignes[0]["id"]
    return None


def changer_mot_de_passe(compte_id, mot_de_passe):
    requete("UPDATE comptes SET mot_de_passe = ? WHERE id = ?", (hacher(mot_de_passe), compte_id))
    # Déconnecte les autres appareils, sauf celui-ci.
    requete("DELETE FROM sessions WHERE compte_id = ? AND jeton != ?",
            (compte_id, empreinte_jeton(st.session_state.get("jeton", ""))))


# --- « Se souvenir de moi » : un jeton secret dans un cookie du navigateur ---
COOKIE = "revisia_session"
DUREE_SESSION = timedelta(days=30)


def empreinte_jeton(jeton):
    """La base ne garde que l'empreinte du jeton : une fuite de la base ne permet pas de se connecter."""
    return hashlib.sha256(jeton.encode()).hexdigest()


def ouvrir_session(compte_id, se_souvenir):
    st.session_state.compte_id = compte_id
    if se_souvenir:
        jeton = secrets.token_urlsafe(32)
        requete("INSERT INTO sessions (jeton, compte_id, expire) VALUES (?, ?, ?)",
                (empreinte_jeton(jeton), compte_id, (datetime.now() + DUREE_SESSION).isoformat()))
        st.session_state.jeton = jeton
        st.session_state.cookie_a_poser = jeton  # posé au prochain affichage, une fois la page redessinée


def compte_depuis_jeton(jeton):
    requete("DELETE FROM sessions WHERE expire < ?", (datetime.now().isoformat(),))
    lignes = requete("SELECT compte_id FROM sessions WHERE jeton = ?", (empreinte_jeton(jeton),))
    return lignes[0]["compte_id"] if lignes else None


def fermer_session():
    if jeton := st.session_state.get("jeton"):
        requete("DELETE FROM sessions WHERE jeton = ?", (empreinte_jeton(jeton),))
    st.session_state.clear()
    st.session_state.effacer_cookie = True


def ecrire_cookie(valeur, duree_secondes):
    st.html(f"<script>document.cookie = '{COOKIE}={valeur}; max-age={duree_secondes}; path=/; SameSite=Lax';"
            "</script>", unsafe_allow_javascript=True)


def charger_profil(compte_id):
    lignes = requete("SELECT * FROM comptes WHERE id = ?", (compte_id,))
    if not lignes:
        return None
    ligne = lignes[0]
    return {**json.loads(ligne["profil"]), "id": ligne["id"], "email": ligne["email"], "photo": ligne["photo"]}


def sauver_profil(profil, ancien=None):
    if profil.get("accepte_nouveautes") and not (ancien or {}).get("accepte_nouveautes"):
        profil["date_accord"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    donnees = {k: v for k, v in profil.items() if k not in ("id", "email", "photo")}
    requete("UPDATE comptes SET profil = ?, photo = ? WHERE id = ?",
            (json.dumps(donnees, ensure_ascii=False), profil.get("photo"), profil["id"]))
    exporter_contacts()


def supprimer_compte(compte_id):
    requete("DELETE FROM sessions WHERE compte_id = ?", (compte_id,))
    requete("DELETE FROM conversations WHERE compte_id = ?", (compte_id,))
    requete("DELETE FROM comptes WHERE id = ?", (compte_id,))
    exporter_contacts()


def exporter_contacts():
    """Tient à jour contacts.csv avec les élèves qui acceptent de recevoir les nouveautés."""
    lignes = []
    for compte in requete("SELECT email, profil FROM comptes"):
        p = json.loads(compte["profil"])
        if p.get("accepte_nouveautes"):
            lignes.append({"prenom": p.get("prenom", ""), "nom": p.get("nom", ""), "age": p.get("age", ""),
                           "classe": p.get("classe", ""), "email": compte["email"],
                           "date_accord": p.get("date_accord", "")})
    with FICHIER_CONTACTS.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.DictWriter(f, ["prenom", "nom", "age", "classe", "email", "date_accord"])
        ecrivain.writeheader()
        ecrivain.writerows(lignes)


def texte_consentement(age):
    return CONSENTEMENT + (CONSENTEMENT_MOINS_15 if age and age < 15 else "")


def afficher_photo(profil, grande=False):
    if profil.get("photo"):
        st.image(profil["photo"], width=110 if grande else 34)
    else:
        classe = "avatar grand" if grande else "avatar"
        st.markdown(f"<span class='{classe}'>{profil.get('prenom', '?')[:1].upper()}</span>",
                    unsafe_allow_html=True)


# --- Conversations (chaque élève ne voit que les siennes) ---
def nouvelle_conversation():
    return {"id": uuid.uuid4().hex[:12], "titre": "Nouvelle discussion",
            "date": datetime.now().isoformat(), "messages": []}


def lister_conversations(compte_id):
    return requete("SELECT id, titre FROM conversations WHERE compte_id = ? ORDER BY date DESC", (compte_id,))


def charger_conversation(conv_id, compte_id):
    ligne = requete("SELECT * FROM conversations WHERE id = ? AND compte_id = ?", (conv_id, compte_id))[0]
    return {"id": ligne["id"], "titre": ligne["titre"], "date": ligne["date"],
            "messages": json.loads(ligne["messages"])}


def sauver_conversation(conv, compte_id, remonter=True):
    if remonter:
        conv["date"] = datetime.now().isoformat()
    requete("INSERT OR REPLACE INTO conversations (id, compte_id, titre, date, messages) VALUES (?, ?, ?, ?, ?)",
            (conv["id"], compte_id, conv["titre"], conv["date"], json.dumps(conv["messages"], ensure_ascii=False)))


def renommer_conversation(conv_id, compte_id, titre):
    requete("UPDATE conversations SET titre = ? WHERE id = ? AND compte_id = ?", (titre, conv_id, compte_id))


def supprimer_conversation(conv_id, compte_id):
    requete("DELETE FROM conversations WHERE id = ? AND compte_id = ?", (conv_id, compte_id))
    if st.session_state.conv["id"] == conv_id:
        st.session_state.conv = nouvelle_conversation()


# --- Fichiers envoyés (photos, PDF, discussions copiées…) ---
def encoder_fichier(fichier):
    return {"nom": fichier.name, "type": fichier.type,
            "data": base64.standard_b64encode(fichier.getvalue()).decode()}


def bloc_fichier(f):
    if f["type"] == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": f["type"], "data": f["data"]}}
    if f["type"].startswith("image/"):
        return {"type": "image", "source": {"type": "base64", "media_type": f["type"], "data": f["data"]}}
    texte = base64.b64decode(f["data"]).decode("utf-8", errors="ignore")
    return {"type": "text", "text": f"Contenu du fichier {f['nom']} :\n{texte}"}


def vers_api(messages):
    """Convertit l'historique sauvegardé au format attendu par Claude."""
    resultat = []
    for m in messages:
        if m["role"] == "user":
            blocs = [bloc_fichier(f) for f in m.get("fichiers", [])]
            if m["text"]:
                blocs.append({"type": "text", "text": m["text"]})
            resultat.append({"role": "user", "content": blocs})
        else:
            resultat.append({"role": "assistant", "content": m["text"] or "…"})
    return resultat


# --- Claude ---
def consignes(profil, outil):
    matieres = "\n".join(f"- {m} : {NIVEAUX[n]}" for m, n in profil["matieres"].items())
    return f"""Tu es Révis'IA, le prof particulier de {profil['prenom']}, {profil['age']} ans, élève en {profil['classe']}.
Ses matières et son niveau dans chacune :
{matieres}
Repère la matière de chaque question et adapte ton explication à son niveau dans cette matière
(pour une matière non listée, adapte-toi à sa classe et à son âge).
Tu es bienveillant et motivant, tu le tutoies et tu l'appelles par son prénom de temps en temps.
Tu expliques avec des exemples concrets et des analogies, et tu vérifies sa compréhension.
Quand l'élève envoie une photo ou un document (cours, exercice, conversation), lis-le attentivement
et appuie-toi dessus. Pour un exercice, guide-le pas à pas au lieu de donner directement la réponse,
sauf s'il demande la correction.
{CONSIGNE_SCHEMA}
Formules en LaTeX entre $...$. Tu réponds en français.{OUTILS[outil]['consigne']}"""


def masquer_svg(texte):
    return re.sub(r"```svg.*?(```|$)", "\n\n🎨 *Je dessine le schéma…*\n\n", texte, flags=re.DOTALL)


def repondre_en_direct(profil, conv, outil):
    """Affiche la réponse de Claude au fur et à mesure qu'elle s'écrit."""
    zone = st.empty()
    texte = ""
    with claude.beta.messages.stream(
        model=MODELE,
        max_tokens=32000,
        system=consignes(profil, outil),
        messages=vers_api(conv["messages"]),
        output_config={"effort": OUTILS[outil]["effort"]},
        **FALLBACK,
    ) as flux:
        for morceau in flux.text_stream:
            texte += morceau
            zone.markdown(masquer_svg(texte) + " ▌")
        if flux.get_final_message().stop_reason == "refusal":
            texte = "Désolé, je ne peux pas t'aider sur cette demande."
    with zone.container():
        afficher_texte(texte)
    return texte


def creer_quiz(profil, conv):
    with st.spinner("Je prépare ton quiz…"):
        reponse = claude.beta.messages.create(
            model=MODELE,
            max_tokens=16000,
            system=consignes(profil, "🧠 Quiz"),
            messages=vers_api(conv["messages"]),
            output_config={"effort": OUTILS["🧠 Quiz"]["effort"],
                           "format": {"type": "json_schema", "schema": QUIZ_SCHEMA}},
            **FALLBACK,
        )
    texte = "".join(b.text for b in reponse.content if b.type == "text")
    try:
        return json.loads(texte)["questions"]
    except (json.JSONDecodeError, KeyError):
        return None


# --- Voix ---
def erreur_voix(e):
    detail = e.body.get("detail", {}) if isinstance(e.body, dict) else {}
    if detail.get("code") == "quota_exceeded":
        return "plus assez de crédits ElevenLabs."
    return detail.get("message", str(e.status_code))


def texte_a_lire(texte):
    """Réécrit la réponse comme un prof la dirait à l'oral (formules, symboles, tableaux…)."""
    texte = re.sub(r"```svg.*?```", "\n(schéma)\n", texte, flags=re.DOTALL)
    reponse = claude.beta.messages.create(
        model=MODELE,
        max_tokens=16000,
        system=LECTURE,
        messages=[{"role": "user", "content": texte}],
        output_config={"effort": "low"},
        **FALLBACK,
    )
    oral = "".join(b.text for b in reponse.content if b.type == "text")
    if len(oral) > 2500:  # limite les crédits ElevenLabs, en coupant à la fin d'une phrase
        oral = oral[:2500].rsplit(".", 1)[0] + "."
    return oral


def lire_a_voix_haute(texte):
    texte = texte_a_lire(texte)
    try:
        audio = voix.text_to_speech.convert(
            text=texte,
            voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        st.audio(b"".join(audio), format="audio/mp3", autoplay=True)
    except ApiError as e:
        st.warning(f"🔇 Lecture vocale impossible : {erreur_voix(e)}")


def transcrire(enregistrement):
    try:
        return voix.speech_to_text.convert(file=enregistrement, model_id="scribe_v1", language_code="fra").text
    except ApiError as e:
        st.warning(f"🎙️ Micro indisponible : {erreur_voix(e)} Écris ta question à la place.")
        return ""


# --- Affichage des messages ---
def afficher_texte(texte):
    """Affiche le Markdown et remplace chaque bloc ```svg par le vrai dessin."""
    morceaux = re.split(r"```svg\s*(.*?)```", texte, flags=re.DOTALL)
    for i, morceau in enumerate(morceaux):
        if i % 2 == 1:
            st.image(morceau.strip(), width="stretch")
            st.download_button("⬇️ Télécharger le schéma", morceau.strip(), "schema.svg",
                               key=f"svg_{uuid.uuid4().hex}")
        elif morceau.strip():
            st.markdown(morceau, unsafe_allow_html=True)


def afficher_fichiers(fichiers):
    images = [base64.b64decode(f["data"]) for f in fichiers if f["type"].startswith("image/")]
    if images:
        st.image(images, width=180)
    for f in fichiers:
        if not f["type"].startswith("image/"):
            st.markdown(f"📎 {f['nom']}")


def afficher_quiz(quiz, n):
    reponses = [st.radio(f"**{i + 1}. {q['question']}**", q["choix"], index=None, key=f"quiz{n}_{i}")
                for i, q in enumerate(quiz)]
    if st.button("✅ Corriger", key=f"corriger{n}"):
        st.session_state[f"corrige{n}"] = True
    if st.session_state.get(f"corrige{n}"):
        score = 0
        for q, rep in zip(quiz, reponses):
            bonne = q["choix"][q["bonne_reponse"]]
            if rep == bonne:
                score += 1
                st.success(f"✔️ {q['question']}\n\n{q['explication']}")
            else:
                st.error(f"❌ {q['question']} — bonne réponse : **{bonne}**\n\n{q['explication']}")
        st.metric("Ton score", f"{score} / {len(quiz)}")
        if score == len(quiz):
            st.balloons()


def afficher_message(profil, msg, n):
    if msg["role"] == "user":
        avatar, nom = profil.get("photo"), profil["prenom"]
    else:
        avatar, nom = "🎓", "Révis'IA"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(f"**{nom}**")
        afficher_fichiers(msg.get("fichiers", []))
        if msg.get("quiz"):
            st.markdown("🧠 **À toi de jouer !**")
            afficher_quiz(msg["quiz"], n)
        else:
            afficher_texte(msg["text"])


# --- Connexion / création de compte ---
def ecran_connexion():
    st.markdown("<div class='titre-connexion'>🎓 Révis'IA</div>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center'>Ton prof particulier, disponible à toute heure.</p>",
                unsafe_allow_html=True)
    connexion, inscription = st.tabs(["Se connecter", "Créer un compte"])

    with connexion:
        with st.form("connexion"):
            email = st.text_input("Email")
            mot_de_passe = st.text_input("Mot de passe", type="password")
            se_souvenir = st.checkbox("Se souvenir de moi", value=True)
            if st.form_submit_button("Se connecter", type="primary", width="stretch"):
                compte_id = verifier_connexion(email.strip().lower(), mot_de_passe)
                if compte_id:
                    ouvrir_session(compte_id, se_souvenir)
                    st.rerun()
                st.error("Email ou mot de passe incorrect.")

    with inscription:
        with st.form("inscription"):
            email = st.text_input("Email", key="email_inscription")
            mot_de_passe = st.text_input("Mot de passe (8 caractères minimum)", type="password",
                                         key="mdp_inscription")
            confirmation = st.text_input("Confirme ton mot de passe", type="password")
            se_souvenir = st.checkbox("Se souvenir de moi", value=True, key="souvenir_inscription")
            if st.form_submit_button("Créer mon compte", type="primary", width="stretch"):
                email = email.strip().lower()
                if not email_valide(email):
                    st.error("Cet email n'est pas valide.")
                elif len(mot_de_passe) < 8:
                    st.error("Le mot de passe doit faire au moins 8 caractères.")
                elif mot_de_passe != confirmation:
                    st.error("Les deux mots de passe ne sont pas identiques.")
                elif (compte_id := creer_compte(email, mot_de_passe)) is None:
                    st.error("Un compte existe déjà avec cet email. Connecte-toi !")
                else:
                    ouvrir_session(compte_id, se_souvenir)
                    st.rerun()


# --- Accueil : l'IA fait connaissance avec l'élève ---
def choisir_niveaux(matieres, prefixe, actuels=None):
    actuels = actuels or {}
    return {m: st.segmented_control(m, list(NIVEAUX), default=actuels.get(m), key=f"{prefixe}_{m}")
            for m in matieres}


def etape(p, cle, question, formulaire, resume):
    """Une question de l'IA : affiche le formulaire tant qu'on n'a pas répondu, puis la réponse."""
    with st.chat_message("assistant", avatar="🎓"):
        st.markdown(question)
        if cle not in p:
            valeur = formulaire()
            if valeur is not None:
                p[cle] = valeur
                st.rerun()
            return False
    with st.chat_message("user"):
        st.markdown(resume(p[cle]))
    return True


def formulaire_identite():
    prenom, nom = st.columns(2)
    prenom = prenom.text_input("Prénom")
    nom = nom.text_input("Nom de famille")
    if st.button("Valider", type="primary", key="ok_identite", disabled=not (prenom.strip() and nom.strip())):
        return {"prenom": prenom.strip().title(), "nom": nom.strip().title()}


def formulaire_age():
    age = st.number_input("Âge", min_value=6, max_value=99, value=15, label_visibility="collapsed")
    if st.button("Valider", type="primary", key="ok_age"):
        return int(age)


def formulaire_matieres():
    choix = st.multiselect("Matières", MATIERES, label_visibility="collapsed",
                           placeholder="Choisis ou écris tes matières…", accept_new_options=True)
    if st.button("Valider", type="primary", key="ok_matieres", disabled=not choix):
        return choix


def formulaire_photo():
    photo = st.file_uploader("Photo de profil", type=["png", "jpg", "jpeg", "webp"], label_visibility="collapsed")
    if photo:
        st.image(photo, width=110)
    valider, passer = st.columns(2)
    if valider.button("Valider", type="primary", key="ok_photo", disabled=not photo, width="stretch"):
        return photo.getvalue()
    if passer.button("Plus tard", key="passer_photo", width="stretch"):
        return b""


def accueil(profil):
    p = st.session_state.setdefault("brouillon", {})
    st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)

    if not etape(p, "identite", "Salut ! 👋 Je suis **Révis'IA**, ton prof particulier. Comment tu t'appelles ?",
                 formulaire_identite, lambda v: f"{v['prenom']} {v['nom']}"):
        return
    prenom = p["identite"]["prenom"]
    if not etape(p, "age", f"Enchanté **{prenom}** ! 😊 Tu as quel âge ?", formulaire_age, lambda v: f"{v} ans"):
        return
    if not etape(p, "classe", "Tu es en quelle classe ?",
                 lambda: st.pills("Classe", CLASSES, label_visibility="collapsed"), str):
        return
    if not etape(p, "liste", "Quelles matières veux-tu travailler avec moi ?", formulaire_matieres, ", ".join):
        return

    def formulaire_niveaux():
        niveaux = choisir_niveaux(p["liste"], "accueil")
        if st.button("Valider", type="primary", key="ok_niveaux", disabled=None in niveaux.values()):
            return niveaux

    if not etape(p, "niveaux", "Tu te sens comment dans chacune ? Je vais m'adapter à toi. 💡", formulaire_niveaux,
                 lambda v: "\n".join(f"- {m} : {n}" for m, n in v.items())):
        return
    if not etape(p, "photo", "Tu veux ajouter une photo de profil ? 📸", formulaire_photo,
                 lambda v: "📷 Photo ajoutée" if v else "Pas de photo pour l'instant"):
        return

    with st.chat_message("assistant", avatar="🎓"):
        st.markdown("Dernière chose : tu veux que je te prévienne par email quand il y a des nouveautés ? 📬")
        accepte = st.checkbox(texte_consentement(p["age"]))
        if st.button("🚀 C'est parti !", type="primary"):
            nouveau = {**profil, **p["identite"], "age": p["age"], "classe": p["classe"],
                       "matieres": p["niveaux"], "photo": p["photo"] or None,
                       "accepte_nouveautes": accepte, "termine": True}
            sauver_profil(nouveau)
            del st.session_state.brouillon
            st.rerun()


# --- Barre latérale : historique et profil ---
def barre_laterale(profil, conv):
    with st.sidebar:
        st.markdown("## 🎓 Révis'IA")
        if st.button("✏️ Nouvelle discussion", width="stretch"):
            st.session_state.conv = nouvelle_conversation()
            st.rerun()

        st.caption("Récents")
        for c in lister_conversations(profil["id"]):
            titre, menu = st.columns([6, 1], gap="small", vertical_alignment="center")
            actif = c["id"] == conv["id"]
            if titre.button(c["titre"], key=f"conv_{c['id']}", width="stretch",
                            type="secondary" if actif else "tertiary"):
                st.session_state.conv = charger_conversation(c["id"], profil["id"])
                st.rerun()
            with menu.popover("⋮", type="tertiary", key=f"menu_{c['id']}"):
                nouveau = st.text_input("Renommer", c["titre"], key=f"nom_{c['id']}")
                if st.button("✏️ Renommer", key=f"renommer_{c['id']}", width="stretch",
                             disabled=not nouveau.strip() or nouveau == c["titre"]):
                    renommer_conversation(c["id"], profil["id"], nouveau.strip())
                    if actif:
                        st.session_state.conv["titre"] = nouveau.strip()
                    st.rerun()
                if st.button("🗑️ Supprimer", key=f"supprimer_{c['id']}", type="primary", width="stretch"):
                    supprimer_conversation(c["id"], profil["id"])
                    st.rerun()

        st.divider()
        photo, nom = st.columns([1, 5], vertical_alignment="center")
        with photo:
            afficher_photo(profil)
        if nom.button(f"**{profil['prenom']} {profil['nom']}** · {profil['classe']}",
                      type="tertiary", key="ouvrir_profil"):
            page_profil(profil)


# --- Page profil (en cliquant sur son nom en bas à gauche) ---
@st.dialog("👤 Mon profil", width="medium")
def page_profil(profil):
    gauche, droite = st.columns([1, 2])
    with gauche:
        nouvelle_photo = st.file_uploader("Photo de profil", type=["png", "jpg", "jpeg", "webp"])
        if nouvelle_photo:
            st.image(nouvelle_photo, width=110)
        else:
            afficher_photo(profil, grande=True)
        retirer = bool(profil.get("photo")) and st.checkbox("Retirer la photo")
    with droite:
        st.text_input("Email", profil["email"], disabled=True)
        prenom_col, nom_col = st.columns(2)
        prenom = prenom_col.text_input("Prénom", profil["prenom"])
        nom = nom_col.text_input("Nom", profil["nom"])
        age_col, classe_col = st.columns(2)
        age = age_col.number_input("Âge", min_value=6, max_value=99, value=profil["age"])
        classe = classe_col.selectbox("Classe", CLASSES, index=CLASSES.index(profil["classe"])
                                      if profil["classe"] in CLASSES else 0)

    theme = st.segmented_control("Luminosité", THEMES, default=profil.get("theme", THEMES[0])) \
        or profil.get("theme", THEMES[0])
    voix_active = st.toggle("🔊 Lire les réponses à voix haute", profil.get("voix", False))
    accepte = st.checkbox(texte_consentement(age), profil.get("accepte_nouveautes", False))

    liste = st.multiselect("Matières", sorted(set(MATIERES) | set(profil["matieres"])),
                           default=list(profil["matieres"]), accept_new_options=True)
    niveaux = choisir_niveaux(liste, "profil", profil["matieres"])

    if st.button("💾 Enregistrer", type="primary", width="stretch", disabled=None in niveaux.values()):
        nouveau = {**profil, "prenom": prenom.strip() or profil["prenom"], "nom": nom.strip() or profil["nom"],
                   "age": int(age), "classe": classe, "matieres": niveaux, "theme": theme,
                   "voix": voix_active, "accepte_nouveautes": accepte}
        if retirer:
            nouveau["photo"] = None
        if nouvelle_photo:
            nouveau["photo"] = nouvelle_photo.getvalue()
        sauver_profil(nouveau, ancien=profil)
        st.rerun()

    with st.expander("🔑 Changer le mot de passe"):
        with st.form("mot_de_passe"):
            actuel = st.text_input("Mot de passe actuel", type="password")
            nouveau_mdp = st.text_input("Nouveau mot de passe (8 caractères minimum)", type="password")
            confirmation = st.text_input("Confirme le nouveau mot de passe", type="password")
            if st.form_submit_button("Changer"):
                if not verifier_connexion(profil["email"], actuel):
                    st.error("Le mot de passe actuel est incorrect.")
                elif len(nouveau_mdp) < 8:
                    st.error("Le nouveau mot de passe doit faire au moins 8 caractères.")
                elif nouveau_mdp != confirmation:
                    st.error("Les deux mots de passe ne sont pas identiques.")
                else:
                    changer_mot_de_passe(profil["id"], nouveau_mdp)
                    st.success("Mot de passe changé ✅")

    if st.button("🚪 Se déconnecter", width="stretch"):
        fermer_session()
        st.rerun()

    with st.expander("🗑️ Effacer tout l'historique"):
        st.warning("Toutes tes discussions seront supprimées définitivement.")
        if st.button("Oui, tout effacer", type="primary"):
            requete("DELETE FROM conversations WHERE compte_id = ?", (profil["id"],))
            st.session_state.conv = nouvelle_conversation()
            st.rerun()

    with st.expander("⚠️ Supprimer mon compte"):
        st.warning("Ton compte, ton profil et toutes tes discussions seront supprimés définitivement.")
        if st.checkbox("Je comprends, supprimer mon compte") and st.button("Supprimer mon compte", type="primary"):
            supprimer_compte(profil["id"])
            fermer_session()
            st.rerun()


# --- Programme principal ---
if st.session_state.pop("effacer_cookie", False):
    ecrire_cookie("", 0)
if jeton := st.session_state.pop("cookie_a_poser", None):
    ecrire_cookie(jeton, int(DUREE_SESSION.total_seconds()))

# Page rafraîchie : on reconnecte l'élève grâce au cookie « se souvenir de moi ».
if "compte_id" not in st.session_state and (jeton := st.context.cookies.get(COOKIE)):
    if compte_id := compte_depuis_jeton(jeton):
        st.session_state.compte_id = compte_id
        st.session_state.jeton = jeton

if "compte_id" not in st.session_state:
    ecran_connexion()
    st.stop()

profil = charger_profil(st.session_state.compte_id)
if profil is None:  # compte supprimé entre-temps
    fermer_session()
    st.rerun()
if not profil.get("termine"):
    accueil(profil)
    st.stop()
if profil.get("theme") == "☀️ Clair":
    st.markdown(CSS_CLAIR, unsafe_allow_html=True)

if "conv" not in st.session_state:
    st.session_state.conv = nouvelle_conversation()
conv = st.session_state.conv

if not conv["messages"]:
    st.markdown(f"<div class='accueil'>Au programme aujourd'hui, {profil['prenom']} ?</div>",
                unsafe_allow_html=True)
for n, msg in enumerate(conv["messages"]):
    afficher_message(profil, msg, f"{conv['id']}_{n}")

outil = st.pills("Outil", list(OUTILS), default="💬 Discuter", label_visibility="collapsed") or "💬 Discuter"
saisie = st.chat_input("Demander à Révis'IA…", accept_file="multiple", file_type=TYPES_FICHIERS,
                       accept_audio=True)

if saisie:
    texte = saisie.text
    if saisie.audio and not texte:
        with st.spinner("J'écoute…"):
            texte = transcrire(saisie.audio)
    fichiers = [encoder_fichier(f) for f in saisie.files]
    if texte or fichiers:
        if outil != "💬 Discuter" and not texte:
            texte = f"{outil} sur ce document"
        message = {"role": "user", "text": texte, "fichiers": fichiers}
        conv["messages"].append(message)
        if conv["titre"] == "Nouvelle discussion":
            conv["titre"] = (texte or fichiers[0]["nom"])[:40]
        afficher_message(profil, message, f"{conv['id']}_{len(conv['messages']) - 1}")

        if outil == "🧠 Quiz":
            quiz = creer_quiz(profil, conv)
            if quiz:
                conv["messages"].append({"role": "assistant", "quiz": quiz,
                                         "text": "Quiz : " + json.dumps(quiz, ensure_ascii=False)})
            else:
                conv["messages"].append({"role": "assistant", "text": "Je n'ai pas réussi à créer ce quiz, réessaie."})
            sauver_conversation(conv, profil["id"])
            st.rerun()

        with st.chat_message("assistant", avatar="🎓"):
            st.markdown("**Révis'IA**")
            reponse = repondre_en_direct(profil, conv, outil)
            if profil.get("voix"):
                lire_a_voix_haute(reponse)
        conv["messages"].append({"role": "assistant", "text": reponse})
        sauver_conversation(conv, profil["id"])

# Dessinée en dernier pour que l'historique inclue la discussion qui vient d'être sauvegardée.
barre_laterale(profil, conv)
