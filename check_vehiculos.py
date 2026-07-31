"""
Comprueba si han aparecido vehículos de ocasión nuevos en The Stellantis Club,
si alguno ha cambiado su estado de reserva, o si Stellantis ha modificado algún
campo relevante de un coche existente (precio, km, matriculación, color,
procedencia, campaña), y envía una notificación por Telegram en cada caso.

También atiende el comando de Telegram "/ultimos [N]", que responde con los N
vehículos cuya "Última actualización en Stellantis" (campo updated_at) sea más
reciente. Como no hay servidor permanente, la respuesta llega con el retraso de
la siguiente ejecución programada (hasta ~20 min), reutilizando el propio cron:
en cada ejecución se consulta Telegram (getUpdates) por si hay comandos nuevos.

Hace login automáticamente en cada ejecución (la sesión solo dura 2 horas,
así que no tiene sentido guardar cookies: se generan nuevas cada vez).

Ejecutado periódicamente por GitHub Actions (ver .github/workflows/check-vehiculos.yml).
Guarda el estado (snapshot de cada coche) en data/estado_vehiculos.json y el
offset de Telegram en data/telegram_offset.json, dentro del propio repo.
"""

import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
STATE_FILE = Path("data/estado_vehiculos.json")
# Nombre del fichero de estado antiguo (versión sin seguimiento de reservas),
# por si el repo lo tiene de una ejecución anterior y hay que migrarlo.
STATE_FILE_ANTIGUO = Path("data/ids_conocidos.json")
TELEGRAM_OFFSET_FILE = Path("data/telegram_offset.json")

# Payload SIN filtrar por estado de reserva: trae TODOS los vehículos,
# tanto disponibles (reserved=0) como ya reservados (reserved=1).
PAYLOAD_COCHES = {
    "fuels": [],
    "carlines": [],
    "reserved": ["0", "1"],
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

# Comando de Telegram que devuelve los N coches más recientemente actualizados
# por Stellantis. Por defecto N=5 si no se indica número; máximo 30 (para no
# acercarnos al límite de 4096 caracteres de un mensaje de Telegram).
COMANDO_ULTIMOS = "/ultimos"
ULTIMOS_POR_DEFECTO = 5
ULTIMOS_MAXIMO = 30


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


# ---------------------------------------------------------------------------
# Estado: snapshot por coche (para detectar altas, reservas y cambios de campo)
# ---------------------------------------------------------------------------

# Campos que vigilamos para avisar de cambios cuando varía "updated_at" de un
# coche que ya conocíamos. clave -> (etiqueta para el mensaje, formateador).
CAMPOS_SEGUIMIENTO = {
    "price_final": ("💰 Precio", lambda v: f"{v} €" if v not in (None, "") else "?"),
    "car_km": ("🛣️ Kilómetros", lambda v: f"{v} km" if v not in (None, "") else "?"),
    "plate_date": ("📆 Matriculación", lambda v: v if v not in (None, "") else "?"),
    "colour": ("🎨 Color", lambda v: v if v not in (None, "") else "?"),
    "provenance": ("📦 Procedencia", lambda v: v if v not in (None, "") else "?"),
    "campa": ("🏁 Campaña comercial", lambda v: v if v not in (None, "") else "?"),
}


def snapshot_coche(v: dict) -> dict:
    """Extrae de un vehículo (tal cual viene de la API) los campos que
    queremos guardar en el estado para poder compararlos en la siguiente
    ejecución."""
    return {
        "reserved": bool(int(v.get("reserved", 0))),
        "updated_at": v.get("updated_at"),
        "price_final": v.get("price_final"),
        "car_km": v.get("car_km"),
        "plate_date": v.get("plate_date"),
        "colour": v.get("colour"),
        "provenance": v.get("provenance"),
        "campa": v.get("campa") or v.get("code_campa"),
    }


def cargar_estado():
    """
    Devuelve un dict {id_coche: snapshot}. Si existe el fichero de estado con
    el formato antiguo (solo reservado, o solo lista de ids), lo migra:
    - Formato "lista simple" (ids_conocidos.json): todos como no reservados,
      sin más datos (no se comparan campos hasta la siguiente ejecución).
    - Formato "id -> bool" (versión con solo reservas): se conserva el
      booleano de reservado, sin más datos.
    En ambos casos, al no tener las claves de CAMPOS_SEGUIMIENTO, la primera
    comparación tras la migración no generará avisos de cambio de campo (no
    hay nada fiable con lo que comparar); a partir de esa ejecución sí.
    """
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            estado = json.load(f)
        # Migración interna: si algún valor es un bool suelto (formato
        # intermedio "id -> reservado"), lo convertimos a snapshot parcial.
        migrado = False
        for vid, val in list(estado.items()):
            if isinstance(val, bool):
                estado[vid] = {"reserved": val}
                migrado = True
        if migrado:
            print("Migrando estado del formato 'id -> reservado' al nuevo formato con snapshot.")
        return estado

    if STATE_FILE_ANTIGUO.exists():
        with open(STATE_FILE_ANTIGUO, "r", encoding="utf-8") as f:
            ids_antiguos = json.load(f)
        print(f"Migrando estado antiguo ({len(ids_antiguos)} coches) al nuevo formato.")
        return {str(vid): {"reserved": False} for vid in ids_antiguos}

    return {}


def guardar_estado(estado: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=0, sort_keys=True)


def detectar_cambios_campos(antiguo: dict, nuevo: dict) -> list:
    """
    Compara el snapshot antiguo y el nuevo de un mismo coche y devuelve una
    lista de líneas describiendo qué campos vigilados han cambiado. Si un
    campo no estaba presente en el snapshot antiguo (p.ej. venía de una
    migración), no se compara ese campo (no hay nada fiable con qué comparar).
    """
    cambios = []
    for campo, (etiqueta, formatear) in CAMPOS_SEGUIMIENTO.items():
        if campo not in antiguo:
            continue
        v_antiguo = antiguo.get(campo)
        v_nuevo = nuevo.get(campo)
        if v_antiguo != v_nuevo:
            cambios.append(f"{etiqueta}: {formatear(v_antiguo)} → {formatear(v_nuevo)}")
    return cambios


# ---------------------------------------------------------------------------
# Mensajes
# ---------------------------------------------------------------------------

# Texto que Stellantis pone cuando no hay equipamiento real cargado para ese
# coche en concreto; si equipment_new es justo esto, no aporta nada y no lo
# mostramos en el mensaje.
EQUIPAMIENTO_PLACEHOLDER = "Consulta el equipamiento con tu concesionario"

# Límite de caracteres para el bloque de equipamiento dentro del mensaje,
# para no acercarnos al límite de 4096 caracteres de Telegram en coches con
# listados de extras muy largos.
MAX_LARGO_EQUIPAMIENTO = 500

# Traducción de "provenance". Solo se han visto estos dos valores en el feed;
# "Campo" es claramente flota/uso interno. El valor "5" no viene documentado
# por Stellantis en ningún sitio, así que lo etiquetamos como "sin confirmar"
# en vez de inventarnos con seguridad qué significa.
PROVENANCE_LABELS = {
    "Campo": "Flota (uso interno)",
    "5": "Código 5 (sin confirmar oficialmente — en la muestra coincide con "
         "coches matriculados desde finales de 2024, podría ser dirección/demo)",
}


def _formatear_fecha_actualizacion(iso_timestamp: str) -> str:
    """Convierte el 'updated_at' (UTC, formato ISO) a hora de Madrid legible."""
    try:
        dt_utc = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        dt_madrid = dt_utc.astimezone(ZoneInfo("Europe/Madrid"))
        return dt_madrid.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return iso_timestamp


def _parsear_updated_at(iso_timestamp):
    """Convierte 'updated_at' a datetime para poder ordenar. Si falta o es
    inválido, devuelve la fecha mínima posible para que quede al final al
    ordenar de más reciente a más antiguo."""
    if not iso_timestamp:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def formatear_coche(coche: dict) -> str:
    marca = coche.get("brand", "?")
    modelo = coche.get("name", "?")
    gama = coche.get("carline")
    precio = coche.get("price_final", "?")
    km = coche.get("car_km", "?")
    combustible = coche.get("fuel", "?")
    cambio = coche.get("engine")
    color = coche.get("colour", "?")
    anio = coche.get("year", "?")
    matricula = coche.get("plate", "?")
    matriculacion = coche.get("plate_date")
    vin = coche.get("vin")
    equipamiento = (coche.get("equipment_new") or "").strip()
    procedencia = coche.get("provenance")
    campana = coche.get("campa") or coche.get("code_campa")
    actualizado = coche.get("updated_at")
    codigo_config = coche.get("code")
    codigo_opciones = coche.get("code_options")
    if codigo_opciones in (None, "0", ""):
        codigo_opciones = None

    lineas = [f"🚗 <b>{marca} - {modelo}</b>"]
    if gama and gama.strip().upper() != modelo.strip().upper():
        lineas.append(f"🏷️ Gama: {gama}")
    lineas.append(f"💰 Precio: {precio} €")
    lineas.append(f"📅 Año: {anio}   🛣️ Km: {km}")
    if matriculacion:
        lineas.append(f"📆 Matriculación: {matriculacion}")
    lineas.append(f"⛽ Combustible: {combustible}   🎨 Color: {color}")
    if cambio:
        lineas.append(f"⚙️ Cambio/tracción: {cambio}")
    lineas.append(f"🔖 Matrícula: {matricula}")
    if vin:
        lineas.append(f"🔢 VIN: {vin}")
    if procedencia:
        etiqueta = PROVENANCE_LABELS.get(procedencia, f"Código {procedencia} (sin traducción conocida)")
        lineas.append(f"📦 Procedencia: {etiqueta}")
    # "GENERICO" y "0" no aportan nada (son el valor por defecto cuando no hay
    # campaña comercial asociada), así que solo se muestra si hay un código real.
    if campana and str(campana) not in ("GENERICO", "0"):
        lineas.append(f"🏁 Campaña comercial: {campana}")
    if actualizado:
        lineas.append(f"🕒 Última actualización en Stellantis: {_formatear_fecha_actualizacion(actualizado)}")
    if equipamiento and equipamiento != EQUIPAMIENTO_PLACEHOLDER:
        if len(equipamiento) > MAX_LARGO_EQUIPAMIENTO:
            equipamiento = equipamiento[:MAX_LARGO_EQUIPAMIENTO].rstrip() + "…"
        lineas.append(f"🧰 Equipamiento: {equipamiento}")
    # Códigos internos de fábrica: no son legibles por sí solos (Stellantis no
    # publica el diccionario que los traduce), se incluyen solo como
    # referencia por si algún día hace falta buscarlos o compararlos.
    if codigo_config or codigo_opciones:
        lineas.append("🔧 Códigos de fábrica (referencia interna, no legibles):")
        if codigo_config:
            lineas.append(f"   • Configuración: {codigo_config}")
        if codigo_opciones:
            lineas.append(f"   • Opciones: {codigo_opciones}")
    lineas.append(f"🔗 {SITE_BASE}/vehiculos-ocasion")

    return "\n".join(lineas)


def calcular_resumen_stock(estado: dict) -> str:
    """
    Recibe el estado {id_coche: snapshot} y devuelve una línea de resumen con
    cuántos vehículos hay disponibles y reservados en total, para añadir al
    final de cada notificación.
    """
    total = len(estado)
    reservados = sum(1 for s in estado.values() if s.get("reserved"))
    disponibles = total - reservados
    return f"📊 Disponibles ahora: {disponibles}   🔒 Reservados ahora: {reservados}   (total: {total})"


# ---------------------------------------------------------------------------
# Comando de Telegram: /ultimos [N]
# ---------------------------------------------------------------------------

def cargar_offset_telegram() -> int:
    if TELEGRAM_OFFSET_FILE.exists():
        with open(TELEGRAM_OFFSET_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("offset", 0)
    return 0


def guardar_offset_telegram(offset: int):
    TELEGRAM_OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TELEGRAM_OFFSET_FILE, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)


def obtener_actualizaciones_telegram(offset: int):
    """Consulta corta (no long-polling) a getUpdates: solo recoge los mensajes
    pendientes desde el último offset guardado, no espera a que lleguen nuevos."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        resp = requests.get(
            url,
            params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except requests.RequestException as e:
        print(f"Error consultando getUpdates de Telegram: {e}", file=sys.stderr)
        return []


def construir_resumen_ultimos(vehiculos_por_id: dict, n: int) -> str:
    ordenados = sorted(
        vehiculos_por_id.values(),
        key=lambda v: _parsear_updated_at(v.get("updated_at")),
        reverse=True,
    )
    top = ordenados[:n]

    lineas = [f"🕒 <b>Últimos {len(top)} vehículos actualizados por Stellantis</b>\n"]
    for v in top:
        marca = v.get("brand", "?")
        modelo = v.get("name", "?")
        precio = v.get("price_final", "?")
        km = v.get("car_km", "?")
        fecha = _formatear_fecha_actualizacion(v.get("updated_at")) if v.get("updated_at") else "?"
        reservado = " 🔒" if bool(int(v.get("reserved", 0))) else ""
        lineas.append(f"• <b>{marca} {modelo}</b> — {precio} € — {km} km — {fecha}{reservado}")
    return "\n".join(lineas)


def procesar_comandos_telegram(vehiculos_por_id: dict):
    """
    Revisa si hay mensajes nuevos en el chat de Telegram desde la última
    ejecución y responde a los comandos "/ultimos [N]" que encuentre. Como
    esto se ejecuta cada ~20 minutos (no hay servidor permanente escuchando),
    la respuesta llega con ese retraso.
    """
    offset = cargar_offset_telegram()
    actualizaciones = obtener_actualizaciones_telegram(offset)

    if not actualizaciones:
        return

    max_update_id = offset
    for update in actualizaciones:
        max_update_id = max(max_update_id, update.get("update_id", 0) + 1)

        mensaje = update.get("message") or {}
        chat_id = str(mensaje.get("chat", {}).get("id", ""))
        texto = (mensaje.get("text") or "").strip()

        # Solo atendemos mensajes del chat configurado, y solo si empiezan
        # por el comando (ignorando el "@nombre_del_bot" que Telegram añade
        # a veces en grupos).
        if chat_id != str(TELEGRAM_CHAT_ID) or not texto:
            continue

        partes = texto.split()
        comando = partes[0].split("@")[0].lower()
        if comando != COMANDO_ULTIMOS:
            continue

        n = ULTIMOS_POR_DEFECTO
        if len(partes) > 1 and partes[1].isdigit():
            n = int(partes[1])
        n_solicitado = n
        n = max(1, min(n, ULTIMOS_MAXIMO))

        print(f"Comando recibido: '{texto}' -> respondiendo con los últimos {n} coches.")
        respuesta = construir_resumen_ultimos(vehiculos_por_id, n)
        if n_solicitado > ULTIMOS_MAXIMO:
            respuesta += f"\n\n(Se ha limitado a {ULTIMOS_MAXIMO} para no superar el límite de Telegram.)"
        enviar_telegram(respuesta)

    guardar_offset_telegram(max_update_id)


def main():
    check_config()

    session = hacer_login()
    vehiculos = obtener_vehiculos(session)
    estado_anterior = cargar_estado()

    es_primera_ejecucion = len(estado_anterior) == 0

    vehiculos_por_id = {str(v["id"]): v for v in vehiculos}
    estado_nuevo = {vid: snapshot_coche(v) for vid, v in vehiculos_por_id.items()}

    # El comando /ultimos se atiende siempre (incluso en la primera
    # ejecución), usando los datos recién descargados de la API.
    procesar_comandos_telegram(vehiculos_por_id)

    if es_primera_ejecucion:
        # No notificamos altas/cambios en la primera ejecución (serían ~200
        # mensajes de golpe); solo guardamos el estado inicial.
        print(f"Primera ejecución: guardando {len(estado_nuevo)} vehículos como estado inicial.")
        guardar_estado(estado_nuevo)
        return

    ids_nuevos = set(estado_nuevo) - set(estado_anterior)
    ids_comunes = set(estado_nuevo) & set(estado_anterior)

    recien_reservados = [
        vid for vid in ids_comunes
        if estado_nuevo[vid].get("reserved") and not estado_anterior[vid].get("reserved")
    ]
    recien_liberados = [
        vid for vid in ids_comunes
        if not estado_nuevo[vid].get("reserved") and estado_anterior[vid].get("reserved")
    ]

    # Coches existentes cuya "Última actualización en Stellantis" ha
    # cambiado: comprobamos qué campo concreto ha variado (precio, km, etc.).
    cambios_por_id = {}
    for vid in ids_comunes:
        antiguo = estado_anterior[vid]
        nuevo = estado_nuevo[vid]
        if "updated_at" not in antiguo:
            continue  # snapshot antiguo migrado sin datos fiables, no comparamos
        if antiguo.get("updated_at") == nuevo.get("updated_at"):
            continue  # Stellantis no ha tocado este coche
        cambios = detectar_cambios_campos(antiguo, nuevo)
        cambios_por_id[vid] = cambios

    # Se calcula una sola vez por ejecución: el resumen refleja el estado
    # actual completo (todos los vehículos), no solo los que cambiaron.
    resumen_stock = calcular_resumen_stock(estado_nuevo)

    if ids_nuevos:
        print(f"Detectados {len(ids_nuevos)} vehículos nuevos.")
        for vid in ids_nuevos:
            mensaje = (
                "🆕 <b>Nuevo vehículo de ocasión disponible</b>\n\n"
                + formatear_coche(vehiculos_por_id[vid])
                + f"\n\n{resumen_stock}"
            )
            enviar_telegram(mensaje)

    if recien_reservados:
        print(f"Detectados {len(recien_reservados)} vehículos recién reservados.")
        for vid in recien_reservados:
            mensaje = (
                "🔒 <b>Vehículo reservado (ya no disponible)</b>\n\n"
                + formatear_coche(vehiculos_por_id[vid])
                + f"\n\n{resumen_stock}"
            )
            enviar_telegram(mensaje)

    if recien_liberados:
        print(f"Detectados {len(recien_liberados)} vehículos que han vuelto a estar disponibles.")
        for vid in recien_liberados:
            mensaje = (
                "🔓 <b>Vehículo disponible de nuevo (reserva cancelada)</b>\n\n"
                + formatear_coche(vehiculos_por_id[vid])
                + f"\n\n{resumen_stock}"
            )
            enviar_telegram(mensaje)

    if cambios_por_id:
        print(f"Detectados {len(cambios_por_id)} vehículos con campos actualizados por Stellantis.")
        for vid, cambios in cambios_por_id.items():
            if cambios:
                bloque_cambios = "📝 <b>Cambios:</b>\n" + "\n".join(f"• {c}" for c in cambios)
            else:
                bloque_cambios = (
                    "📝 Stellantis ha actualizado este coche, pero no en los campos "
                    "monitorizados (precio, km, matriculación, color, procedencia, campaña). "
                    "Puede haber cambiado algún otro dato (ej. equipamiento)."
                )
            mensaje = (
                "✏️ <b>Cambio detectado en un vehículo</b>\n\n"
                + formatear_coche(vehiculos_por_id[vid])
                + f"\n\n{bloque_cambios}"
                + f"\n\n{resumen_stock}"
            )
            enviar_telegram(mensaje)

    if not (ids_nuevos or recien_reservados or recien_liberados or cambios_por_id):
        print("Sin novedades.")

    guardar_estado(estado_nuevo)


if __name__ == "__main__":
    main()
