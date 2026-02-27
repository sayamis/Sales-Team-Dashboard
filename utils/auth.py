import streamlit as st
import json, time, secrets, hashlib, hmac, base64, urllib.parse, requests

from streamlit_cookies_manager import EncryptedCookieManager
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

STATE_MAX_AGE = 10 * 60
LOGIN_TTL_SECONDS = 7 * 24 * 3600
COOKIE_NAME = "tppl_auth"

def qp_get(name: str):
    try:
        val = st.query_params.get(name)
        if isinstance(val, list):
            return val[0] if val else None
        return val
    except Exception:
        pass
    try:
        d = st.experimental_get_query_params()
        v = d.get(name, [None])
        return v[0] if v else None
    except Exception:
        return None

def qp_clear(keys=None):
    if keys is None:
        try:
            st.query_params.clear()
            return
        except Exception:
            pass
        try:
            st.experimental_set_query_params()
        except Exception:
            pass
        return

    try:
        for k in keys:
            if k in st.query_params:
                del st.query_params[k]
        return
    except Exception:
        pass

    try:
        d = st.experimental_get_query_params()
        for k in keys:
            d.pop(k, None)
        st.experimental_set_query_params(**d)
    except Exception:
        pass

def _get_state_secret() -> bytes:
    s = None
    try:
        s = st.secrets.get("STATE_SECRET", None)
    except Exception:
        s = None
    if not s:
        s = "CHANGE_ME__SET_STATE_SECRET_IN_STREAMLIT_SECRETS"
    return str(s).encode("utf-8")

def get_redirect_uri() -> str:
    try:
        v = st.secrets.get("REDIRECT_URI", None)
        if v:
            return str(v).strip()
    except Exception:
        pass
    return "http://localhost:8501"

def load_oauth_client_cfg() -> dict:
    try:
        oc = st.secrets.get("OAUTH_CLIENT", None)
        if oc:
            web = dict(oc).get("web", dict(oc))
            return {
                "client_id": web["client_id"],
                "client_secret": web["client_secret"],
                "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
            }
    except Exception:
        pass

    with open("oauth_client.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    web = data.get("web", data)
    return {
        "client_id": web["client_id"],
        "client_secret": web["client_secret"],
        "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
    }

def make_signed_login_token(email: str, ttl_seconds: int = LOGIN_TTL_SECONDS) -> str:
    exp = int(time.time()) + int(ttl_seconds)
    rnd = secrets.token_urlsafe(12)
    payload = f"{email}|{exp}|{rnd}".encode("utf-8")
    sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
    p_b64 = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    s_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{p_b64}.{s_b64}"

def verify_login_token(token: str):
    try:
        p_b64, s_b64 = token.split(".", 1)
        def pad(x): return x + "=" * (-len(x) % 4)
        payload = base64.urlsafe_b64decode(pad(p_b64).encode("utf-8"))
        sig = base64.urlsafe_b64decode(pad(s_b64).encode("utf-8"))
        expected_sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        txt = payload.decode("utf-8")
        email, exp_str, _ = txt.split("|", 2)
        if time.time() > int(exp_str):
            return None
        return email.strip().lower()
    except Exception:
        return None

def make_signed_state() -> str:
    ts = int(time.time())
    rnd = secrets.token_urlsafe(16)
    payload = f"{ts}:{rnd}".encode("utf-8")
    sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
    p_b64 = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    s_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{p_b64}.{s_b64}"

def verify_signed_state(state: str, max_age_sec: int = STATE_MAX_AGE) -> bool:
    try:
        p_b64, s_b64 = state.split(".", 1)
        def pad(x): return x + "=" * (-len(x) % 4)
        payload = base64.urlsafe_b64decode(pad(p_b64).encode("utf-8"))
        sig = base64.urlsafe_b64decode(pad(s_b64).encode("utf-8"))
        expected_sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        ts_str = payload.decode("utf-8").split(":", 1)[0]
        ts = int(ts_str)
        return abs(int(time.time()) - ts) <= max_age_sec
    except Exception:
        return False

def build_google_auth_url(client_id: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

def exchange_code_for_tokens(code: str, client_id: str, client_secret: str, token_uri: str) -> dict:
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": get_redirect_uri(),
        "grant_type": "authorization_code",
    }
    resp = requests.post(token_uri, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=25)
    resp.raise_for_status()
    return resp.json()

def verify_google_id_token(id_token_jwt: str, client_id: str) -> dict:
    req = google_requests.Request()
    return google_id_token.verify_oauth2_token(id_token_jwt, req, audience=client_id)

def ensure_google_login(get_user_access_fn):
    # cookie manager
    cookies = EncryptedCookieManager(
        prefix="tppl/",
        password=st.secrets.get("COOKIE_PASSWORD", "CHANGE_ME_COOKIE_PASSWORD_32CHARS_MIN"),
    )
    if not cookies.ready():
        st.stop()

    if "user_email" not in st.session_state:
        st.session_state.user_email = None
    if "did_logout" not in st.session_state:
        st.session_state.did_logout = False

    oauth = load_oauth_client_cfg()

    # restore from cookie
    # restore from cookie (skip if just logged out)
    if not st.session_state.get("did_logout"):
        if st.session_state.user_email is None:
            tok = cookies.get(COOKIE_NAME)
            if tok:
                e = verify_login_token(tok)
                if e:
                    st.session_state.user_email = e

    # OAuth callback
    code = qp_get("code")
    state_qp = qp_get("state")

    if code and st.session_state.user_email is None:
        try:
            if not state_qp or not verify_signed_state(state_qp):
                st.error("OAuth state mismatch / expired. Please login again.")
                qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
            else:
                tokens = exchange_code_for_tokens(code, oauth["client_id"], oauth["client_secret"], oauth["token_uri"])
                idt = tokens.get("id_token")
                if not idt:
                    st.error("No id_token received from Google.")
                    qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                else:
                    claims = verify_google_id_token(idt, oauth["client_id"])
                    email = str(claims.get("email", "")).lower().strip()
                    verified = bool(claims.get("email_verified", False))

                    if not verified:
                        st.error("Your Google email is not verified.")
                        qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                    elif get_user_access_fn(email) is None:
                        st.error("Access denied: your email is not present in Employee List.")
                        qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                    else:
                        st.session_state.user_email = email
                        cookies[COOKIE_NAME] = make_signed_login_token(email)
                        cookies.save()
                        qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                        st.rerun()
        except Exception as e:
            st.error(f"Login failed: {e}")
            qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])

    # Sidebar UI
    with st.sidebar:
        st.subheader("Google Login")
        if st.session_state.user_email:
            st.success(f"Logged in as: {st.session_state.user_email}")
            if st.button("Logout"):
                st.session_state.user_email = None
                st.session_state.did_logout = True  # ✅ prevent instant cookie-restore

                # ✅ more reliable than del
                cookies[COOKIE_NAME] = ""
                cookies.save()

                st.cache_data.clear()
                qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                st.rerun()
        else:
            st.info("Please sign in with Google.")
            if st.session_state.get("did_logout"):
                st.session_state.did_logout = False
            if st.button("Sign in with Google"):
                signed_state = make_signed_state()
                url = build_google_auth_url(oauth["client_id"], signed_state)
                st.link_button("➡️ Continue with Google", url)
                st.stop()

        st.divider()
        st.subheader("Refresh")
        if st.button("🔄 Refresh now"):
            st.cache_data.clear()
            st.rerun()

    return st.session_state.user_email