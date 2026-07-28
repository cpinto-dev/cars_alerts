# Alertas de vehículos de ocasión — The Stellantis Club

Revisa cada 20 minutos si hay coches nuevos en
https://thestellantisclub.com/vehiculos-ocasion, o si alguno pasa a estar
**reservado** (o se libera de nuevo), y te avisa por Telegram en cada caso.
Se ejecuta en GitHub Actions, gratis, sin que tengas que tener tu PC encendido.

Notificaciones que puedes recibir:
- 🆕 Vehículo nuevo disponible
- 🔒 Vehículo reservado (ya no disponible)
- 🔓 Vehículo disponible de nuevo (por si se cancela una reserva)

El script hace **login automático en cada ejecución** (la sesión de la web solo dura
2 horas, así que en vez de guardar cookies que caducan, el script inicia sesión él
solo cada vez usando tu número de empleado).

## Paso 1 — Crear el bot de Telegram (2 minutos)

1. En Telegram, busca el usuario **@BotFather** y ábrele un chat.
2. Escríbele `/newbot`, ponle un nombre y un usuario (debe terminar en "bot", ej. `stellantis_alertas_bot`).
3. Te dará un **token** tipo `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Guárdalo.
4. Busca tu bot por su usuario y envíale cualquier mensaje (ej. "hola") para iniciar el chat.
5. Tu `chat_id` ya lo tienes: **6771568734**.

## Paso 2 — Crear el repositorio en GitHub

1. Entra en github.com (crea una cuenta gratuita si no tienes) y crea un **repositorio nuevo**, puede ser privado.
2. Sube estos archivos/carpetas manteniendo la estructura:
   - `check_vehiculos.py`
   - `requirements.txt`
   - `.github/workflows/check-vehiculos.yml`

   Puedes hacerlo arrastrando los archivos desde la interfaz web de GitHub ("Add file" → "Upload files"), sin necesidad de usar git por terminal.

## Paso 3 — Guardar los secrets en GitHub

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**.
Crea estos 6 secrets:

| Nombre del secret     | Valor |
|------------------------|-------|
| `EMPLOYEE_NUMBER`      | tu número de empleado (el que pusiste: `04003961`) |
| `SOCIETY`              | `I` (el código que usaste, Iveco) |
| `LOGIN_ROUTE`          | `r` (la ruta que corresponde a "Jubilado" — si algún día usaras la opción "Empleado" en vez de "Jubilado", sería `e`) |
| `TELEGRAM_BOT_TOKEN`   | el token de tu bot (Paso 1) |
| `TELEGRAM_CHAT_ID`     | `6771568734` |

Ya no hace falta copiar ninguna cookie a mano — el script hace login él solo cada vez.

## Paso 4 — Probarlo

1. Ve a la pestaña **Actions** de tu repositorio.
2. Verás el workflow "Comprobar vehículos de ocasión". Haz clic en él.
3. Pulsa **Run workflow** (botón desplegable arriba a la derecha) para lanzarlo a mano.
4. Espera ~30 segundos y revisa que termine en verde ✅. Si falla, entra en el detalle
   de la ejecución y mira el mensaje de error — casi siempre será que el número de
   empleado, la sociedad o la ruta de login no coinciden.
   - La primera ejecución **no envía notificaciones** (guarda el estado actual de
     todos los coches, disponibles y reservados, como punto de partida en
     `data/estado_vehiculos.json`). A partir de la segunda ejecución, cualquier
     cambio (coche nuevo, reservado, o liberado) te llegará por Telegram.
5. A partir de aquí, se ejecutará solo cada 20 minutos, sin que tengas que hacer nada.

**Nota si ya tenías la versión anterior del proyecto funcionando:** el fichero de
estado ha cambiado de nombre (antes `data/ids_conocidos.json`, ahora
`data/estado_vehiculos.json`, porque ahora guarda también si cada coche está
reservado o no). El script migra automáticamente el fichero antiguo la primera vez
que lo ejecutes con esta versión nueva, así que no necesitas borrar ni tocar nada:
simplemente sustituye `check_vehiculos.py` y el workflow por los nuevos.

## Si algún día deja de funcionar

Como el login es automático, no debería haber mantenimiento. Si un día ves que el
workflow falla de forma persistente en la pestaña Actions, seguramente es porque
Stellantis ha cambiado algo en su web (por ejemplo, el nombre de algún campo del
formulario). En ese caso, habría que repetir el proceso de capturar las peticiones
con las DevTools para ver qué ha cambiado.

## Cambiar la frecuencia

En `.github/workflows/check-vehiculos.yml`, la línea `cron: "*/20 * * * *"` controla
la frecuencia. Por ejemplo, `*/10 * * * *` sería cada 10 minutos. Con una ejecución de
este tipo (que dura unos segundos) cada 20 min no hay ningún problema con el límite
gratuito de minutos de GitHub Actions.
