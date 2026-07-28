# Alertas de vehículos de ocasión — The Stellantis Club

Revisa cada 20 minutos si hay coches nuevos en
https://thestellantisclub.com/vehiculos-ocasion y te avisa por Telegram.
Se ejecuta en GitHub Actions, gratis, sin que tengas que tener tu PC encendido.

## Paso 1 — Crear el bot de Telegram (2 minutos)

1. En Telegram, busca el usuario **@BotFather** y ábrele un chat.
2. Escríbele `/newbot`, ponle un nombre y un usuario (debe terminar en "bot", ej. `stellantis_alertas_bot`).
3. Te dará un **token** tipo `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Guárdalo.
4. Ahora busca tu bot por su usuario y envíale cualquier mensaje (ej. "hola") para iniciar el chat.
5. Para saber tu **chat_id**, abre esta URL en el navegador (sustituyendo el token):
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   Verás un JSON; busca `"chat":{"id":123456789,...}` — ese número es tu `chat_id`.

## Paso 2 — Crear el repositorio en GitHub

1. Entra en github.com (crea una cuenta gratuita si no tienes) y crea un **repositorio nuevo**, puede ser privado.
2. Sube estos tres archivos/carpetas manteniendo la estructura:
   - `check_vehiculos.py`
   - `requirements.txt`
   - `.github/workflows/check-vehiculos.yml`

   Puedes hacerlo arrastrando los archivos desde la interfaz web de GitHub ("Add file" → "Upload files"), sin necesidad de usar git por terminal.

## Paso 3 — Conseguir la cookie y el token de sesión

Esto lo repetirás cada vez que la sesión caduque (normalmente dura semanas):

1. Entra en https://thestellantisclub.com e inicia sesión con tu código.
2. Ve a la sección **Vehículos de Ocasión**.
3. Abre las DevTools (F12) → pestaña **Network** → filtra por `Fetch/XHR`.
4. Aplica cualquier filtro en la página (o simplemente recarga) para que se dispare la petición a `CarsVoPrice`.
5. Haz clic en esa petición → pestaña **Headers**:
   - En **Request Headers**, busca la línea `cookie:`. Dentro de ese texto largo verás dos trozos que te interesan:
     - `XSRF-TOKEN=....` (todo el valor hasta el `;`) → este es `XSRF_TOKEN_COOKIE`
     - `tsc_session=....` (todo el valor hasta el `;`) → este es `TSC_SESSION`
   - Busca también la línea `x-xsrf-token:` → su valor completo es `XSRF_TOKEN_HEADER`

## Paso 4 — Guardar los secrets en GitHub

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**.
Crea estos 5 secrets (nombre exacto a la izquierda, valor pegado a la derecha):

| Nombre del secret       | Valor |
|--------------------------|-------|
| `TSC_SESSION`             | el valor de `tsc_session` que copiaste |
| `XSRF_TOKEN_COOKIE`       | el valor de `XSRF-TOKEN` (cookie) que copiaste |
| `XSRF_TOKEN_HEADER`       | el valor del header `x-xsrf-token` que copiaste |
| `TELEGRAM_BOT_TOKEN`      | el token de tu bot (Paso 1) |
| `TELEGRAM_CHAT_ID`        | tu chat_id (Paso 1) |

## Paso 5 — Probarlo

1. Ve a la pestaña **Actions** de tu repositorio.
2. Verás el workflow "Comprobar vehículos de ocasión". Haz clic en él.
3. Pulsa **Run workflow** (botón desplegable arriba a la derecha) para lanzarlo a mano.
4. Espera ~30 segundos y revisa que termine en verde ✅.
   - La primera ejecución **no envía notificaciones** (guarda los ~200 coches actuales como punto de partida). A partir de la segunda ejecución, cualquier coche nuevo te llegará por Telegram.
5. A partir de aquí, se ejecutará solo cada 20 minutos, sin que tengas que hacer nada.

## Cuando la sesión caduque

Si un día ves en la pestaña Actions que el workflow falla, o te llega un Telegram de aviso de "sesión caducada",
repite el **Paso 3** y actualiza los 3 secrets relacionados (`TSC_SESSION`, `XSRF_TOKEN_COOKIE`, `XSRF_TOKEN_HEADER`)
en **Settings → Secrets and variables → Actions** (edítalos, no hace falta borrarlos y crearlos de nuevo).

## Cambiar la frecuencia

En `.github/workflows/check-vehiculos.yml`, la línea `cron: "*/20 * * * *"` controla la frecuencia.
Por ejemplo, `*/10 * * * *` sería cada 10 minutos. Ten en cuenta que GitHub Actions tiene un límite de minutos
gratis al mes; con una ejecución de este tipo (que dura unos segundos) cada 20 min no hay ningún problema.
