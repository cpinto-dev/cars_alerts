"""
Comprueba si han aparecido vehículos de ocasión nuevos en The Stellantis Club
y envía una notificación por Telegram cuando detecta alguno.

Hace login automáticamente en cada ejecución (la sesión solo dura 2 horas,
así que no tiene sentido guardar cookies: se generan nuevas cada vez).

Ejecutado periódicamente por GitHub Actions (ver .github/workflows/check-vehiculos.yml).
Guarda el estado (IDs de coches ya vistos) en data/ids_conocidos.json dentro del propio repo.
"""

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuración: todo esto viene de variables de entorno (GitHub Secrets)
# ---------------------------------------------------------------------------
EMPLOYEE_NUMBER = os.environ.get("EMPLOYEE_NUMBER")
SOCIETY = os.environ.get("SOCIETY", "I")  # "I" = Iveco, según tu caso
# Ruta del segundo paso de login: "r" = Jubilado, "e" = Empleado.
LOGIN_ROUTE = os.environ.get("LOGIN_ROUTE", "r")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

AUTH_BASE = "https://auth.thestellantisclub.com"
SITE_BASE = "https://thestellantisclub.com"
URL_BACK = "http://thestellantisclub.com"

LOGIN_PAGE_URL = f"{AUTH_BASE}/login/tsc/tsc-app-pro?urlBack={urllib.parse.quote(URL_BACK, safe='')}"
LOGIN_SUBMIT_URL = f"{AUTH_BASE}/login/tsc/{LOGIN_ROUTE}/tsc-app-pro"
FINAL_LOGIN_URL = f"{SITE_BASE}/login"

API_URL = f"{SITE_BASE}/ajax/filter/CarsVoPrice"
STATE_FILE = Path("data/ids_conocidos.json")

# Payload "sin filtrar": trae todos los vehículos disponibles
PAYLOAD_COCHES = {
    "fuels": [],
    "carlines": [],
    "reserved": ["0"],
    "colours": [],
    "engines": [],
    "carsKm": 999999,
    "displacements": [],
    "platesDate": [],
    "pricesFinal": "999999.00",
    "ranges": [],
    "versions": [],
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def check_config():
    faltan = [
        name
        for name, val in [
            ("EMPLOYEE_NUMBER", EMPLOYEE_NUMBER),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ]
        if not val
    ]
    if faltan:
        print(f"Faltan variables de entorno: {', '.join(faltan)}", file=sys.stderr)
        sys.exit(1)


def enviar_telegram(mensaje: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if not resp.ok:
        print(f"Error enviando Telegram: {resp.status_code} {resp.text}", file=sys.stderr)


def extraer_token_formulario(html: str, route: str) -> str:
    """
    Extrae el _token del formulario cuya action corresponde a la ruta de login
    elegida (ej. /login/tsc/r/tsc-app-pro).
    """
    patron_form = rf'action="/login/tsc/{route}/tsc-app-pro".*?name="_token" value="([^"]+)"'
    match = re.search(patron_form, html, re.DOTALL)
    if not match:
        # Fallback: coge el primer _token que encuentre en la página
        match = re.search(r'name="_token" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("No se encontró el _token en la página de login inicial.")
    return match.group(1)


def extraer_jwt_y_urlback(html: str):
    token_match = re.search(r'name="token" value="([^"]+)"', html)
    urlback_match = re.search(r'name="url_back" value="([^"]+)"', html)
    if not token_match:
        raise RuntimeError(
            "No se encontró el JWT de sesión en la respuesta de login. "
            "Revisa que EMPLOYEE_NUMBER, SOCIETY y LOGIN_ROUTE sean correctos."
        )
    jwt = token_match.group(1)
    url_back = urlback_match.group(1) if urlback_match else URL_BACK
    return jwt, url_back


def hacer_login() -> requests.Session:
    session = requests.Session()
    session.headers.update({"user-agent": USER_AGENT})

    # Paso 1: cargar la página de login para conseguir el _token y las cookies iniciales
    resp1 = session.get(LOGIN_PAGE_URL, timeout=30)
    resp1.raise_for_status()
    token_inicial = extraer_token_formulario(resp1.text, LOGIN_ROUTE)

    # Paso 2: enviar número de empleado + sociedad -> devuelve un HTML con el JWT
    resp2 = session.post(
        LOGIN_SUBMIT_URL,
        data={
            "_token": token_inicial,
            "urlBack": URL_BACK,
            "society": SOCIETY,
            "employeenumber": EMPLOYEE_NUMBER,
        },
        headers={"referer": LOGIN_PAGE_URL},
        timeout=30,
    )
    resp2.raise_for_status()
    jwt, url_back = extraer_jwt_y_urlback(resp2.text)

    # Paso 3: canjear el JWT por las cookies de sesión definitivas (tsc_session / XSRF-TOKEN)
    resp3 = session.post(
        FINAL_LOGIN_URL,
        data={"token": jwt, "url_back": url_back},
        headers={"referer": AUTH_BASE + "/"},
        timeout=30,
        allow_redirects=True,
    )
    resp3.raise_for_status()

    if not session.cookies.get("tsc_session", domain="thestellantisclub.com"):
        raise RuntimeError(
            "El login no ha generado la cookie tsc_session. "
            "Puede que el número de empleado, la sociedad o la ruta de login (r/e) no sean correctos."
        )

    return session


def obtener_vehiculos(session: requests.Session):
    xsrf_cookie = session.cookies.get("XSRF-TOKEN", domain="thestellantisclub.com")
    xsrf_header = urllib.parse.unquote(xsrf_cookie)

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": SITE_BASE,
        "referer": f"{SITE_BASE}/vehiculos-ocasion",
        "x-requested-with": "XMLHttpRequest",
        "x-xsrf-token": xsrf_header,
    }

    resp = session.post(API_URL, headers=headers, json=PAYLOAD_COCHES, timeout=30)

    if resp.status_code in (401, 403, 419):
        enviar_telegram(
            "⚠️ <b>Fallo de autenticación</b> al consultar The Stellantis Club.\n"
            "El login automático no ha funcionado en esta ejecución. Revisa los secrets "
            "EMPLOYEE_NUMBER / SOCIETY / LOGIN_ROUTE en GitHub."
        )
        sys.exit(1)

    resp.raise_for_status()
    return resp.json()


def cargar_ids_conocidos():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_ids_conocidos(ids):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f)


def formatear_coche(coche: dict) -> str:
    marca = coche.get("brand", "?")
    modelo = coche.get("name", "?")
    precio = coche.get("price_final", "?")
    km = coche.get("car_km", "?")
    combustible = coche.get("fuel", "?")
    color = coche.get("colour", "?")
    anio = coche.get("year", "?")
    matricula = coche.get("plate", "?")

    return (
        f"🚗 <b>{marca} - {modelo}</b>\n"
        f"💰 Precio: {precio} €\n"
        f"📅 Año: {anio}   🛣️ Km: {km}\n"
        f"⛽ Combustible: {combustible}   🎨 Color: {color}\n"
        f"🔖 Matrícula: {matricula}\n"
        f"🔗 {SITE_BASE}/vehiculos-ocasion"
    )


def main():
    check_config()

    session = hacer_login()
    vehiculos = obtener_vehiculos(session)
    ids_conocidos = cargar_ids_conocidos()

    es_primera_ejecucion = len(ids_conocidos) == 0

    vehiculos_por_id = {str(v["id"]): v for v in vehiculos}
    ids_actuales = set(vehiculos_por_id.keys())

    nuevos = ids_actuales - ids_conocidos

    if es_primera_ejecucion:
        # No notificamos en la primera ejecución (serían ~200 mensajes de golpe);
        # solo guardamos el estado inicial.
        print(f"Primera ejecución: guardando {len(ids_actuales)} vehículos como estado inicial.")
    elif nuevos:
        print(f"Detectados {len(nuevos)} vehículos nuevos.")
        for vid in nuevos:
            mensaje = "🆕 <b>Nuevo vehículo de ocasión disponible</b>\n\n" + formatear_coche(
                vehiculos_por_id[vid]
            )
            enviar_telegram(mensaje)
    else:
        print("Sin novedades.")

    guardar_ids_conocidos(ids_actuales)


if __name__ == "__main__":
    main()
