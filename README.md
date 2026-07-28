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

## Paso 5 — Configurar el disparador cada 20 minutos (cron externo)

**Importante:** el `schedule` interno de GitHub Actions no es fiable en repos
poco activos (puede retrasarse horas). Por eso este workflow ya NO tiene un
`schedule` propio — se lanza desde un servicio externo gratuito que llama a la
API de GitHub con precisión real.

### 5.1 — Crear un token de acceso personal (PAT) en GitHub

1. Ve a tu perfil de GitHub → **Settings** (el de tu cuenta, no el del repo) →
   en el menú de la izquierda, baja hasta **Developer settings**.
2. **Personal access tokens → Fine-grained tokens → Generate new token**.
3. Ponle un nombre, por ejemplo `cron-vehiculos`.
4. En **Repository access**, elige **"Only select repositories"** y selecciona
   tu repo (`cars_alerts` o como lo hayas llamado).
5. En **Permissions → Repository permissions**, busca **"Actions"** y ponlo en
   **"Read and write"**.
6. Genera el token y **cópialo** — solo se muestra una vez (empieza por `github_pat_...`).

Este token es una credencial sensible: no lo compartas ni lo pegues en ningún
sitio salvo en el paso siguiente (cron-job.org, en un campo que queda oculto).

### 5.2 — Crear la tarea programada en cron-job.org

1. Entra en **https://cron-job.org** y crea una cuenta gratuita.
2. **Create cronjob**.
3. **Title**: algo como "Comprobar vehículos Stellantis".
4. **URL**:
   ```
   https://api.github.com/repos/TU_USUARIO/TU_REPO/actions/workflows/check-vehiculos.yml/dispatches
   ```
   Sustituye `TU_USUARIO/TU_REPO` por los tuyos (ej. `cpinto-dev/cars_alerts`).
5. **Request method**: `POST`.
6. Baja a **"Advanced"** (o "Headers/Body" según la versión de la web) y añade:
   - **Header** `Authorization` con valor `Bearer TU_TOKEN` (sustituye por el PAT del paso 5.1)
   - **Header** `Accept` con valor `application/vnd.github+json`
   - **Header** `Content-Type` con valor `application/json`
   - **Body** (raw JSON):
     ```json
     {"ref":"main"}
     ```
     (si tu rama principal se llama `master` en vez de `main`, pon `master`)
7. En **Schedule**, elige que se ejecute **cada 20 minutos**.
8. Guarda y activa la tarea.
9. cron-job.org te deja ver el historial de ejecuciones y si la petición devolvió éxito (código 204) o error — puedes usarlo para comprobar que está disparando bien el workflow.

A partir de aquí, es cron-job.org quien despierta al workflow de GitHub cada 20
minutos con precisión, en vez de depender del scheduler interno de GitHub.



Como el login es automático, no debería haber mantenimiento. Si un día ves que el
workflow falla de forma persistente en la pestaña Actions, seguramente es porque
Stellantis ha cambiado algo en su web (por ejemplo, el nombre de algún campo del
formulario). En ese caso, habría que repetir el proceso de capturar las peticiones
con las DevTools para ver qué ha cambiado.

## Cambiar la frecuencia

Ahora la frecuencia se controla desde **cron-job.org** (Paso 5), no desde el
archivo del workflow. Entra en tu tarea en cron-job.org y cambia el intervalo del
**Schedule**. Con una ejecución de este tipo (que dura unos segundos) incluso
cada 5-10 minutos no hay ningún problema con el límite gratuito de minutos de
GitHub Actions.
