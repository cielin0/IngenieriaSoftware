# Centro de incidencias — Chile (por comunidades)

Aplicación web colaborativa para reportar y dar seguimiento a incidencias geolocalizadas **dentro de Chile**. La interfaz usa JavaScript y Leaflet; el backend está implementado en Django y utiliza MySQL.

## Estructura del proyecto

- `incidents/`: modelos, API, reglas de negocio, migraciones y comandos de administración.
- `los_lagos/`: configuración y rutas del proyecto Django.
- `static/`: JavaScript y estilos de la interfaz y del panel de administración.
- `index.html` y `admin_panel.html`: vistas principales de la aplicación.
- `data/`: datos JSON heredados que se pueden importar a la base de datos.
- `requirements.txt`: dependencias de Python.

Los entornos virtuales locales (`.venv/` y `entorno/`) y los archivos con variables de entorno no forman parte del repositorio. Instala las dependencias en tu propio entorno y configura las credenciales mediante variables de entorno.

## Requisitos

- Python 3.10 o posterior.
- MySQL Server 8.0 o posterior.
- MySQL Workbench para administrar el servidor y la base de datos.
- Dependencias Python de `requirements.txt`.

> Workbench es el cliente visual; instala e inicia también MySQL Server. Django se conecta directamente al servidor MySQL.

## Preparar MySQL Workbench

Conecta Workbench a tu servidor MySQL (normalmente `localhost`, puerto `3306`) y ejecuta:

```sql
CREATE DATABASE los_lagos
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'los_lagos_app'@'localhost' IDENTIFIED BY 'cambia-esta-clave';
GRANT ALL PRIVILEGES ON los_lagos.* TO 'los_lagos_app'@'localhost';
```

Usa una contraseña propia para el usuario de la aplicación. No guardes contraseñas reales en el código ni en el repositorio.

## Instalación y ejecución

Desde PowerShell, en la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:MYSQL_DATABASE = "los_lagos"
$env:MYSQL_USER = "los_lagos_app"
$env:MYSQL_PASSWORD = "cambia-esta-clave"
$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_PORT = "3306"
python manage.py migrate
python manage.py import_legacy_data --renew-expiry
python manage.py runserver
```

Abre <http://127.0.0.1:8000>. Django crea las tablas de la aplicación con `migrate`; podrás verlas en Workbench tras actualizar el esquema. La importación lee los JSON que ya existían en `data/` y se puede repetir sin duplicar registros. Si no quieres importar datos, omite ese paso. `--renew-expiry` calcula la caducidad desde hoy: sin él, los datos de ejemplo (de septiembre) aparecerían ya caducados.

Para crear la cuenta administradora inicial:

```powershell
$env:ADMIN_EMAIL = "admin@ejemplo.cl"
$env:ADMIN_PASSWORD = "una-clave-larga-y-unica"
python manage.py bootstrap_admin
```

Por ahora basta con que la contraseña no esté vacía. El registro público siempre crea cuentas de ciudadano. Un administrador puede promover ciudadanos a operador o administrador desde **Gestión de usuarios**.

## Funciones

- Registro e inicio de sesión; roles de ciudadano, operador y administrador.
- API JSON autenticada con token Bearer, permisos de rol y propiedad de cada reporte.
- Ciudadanos: crear reportes, **editar o eliminar solo los suyos**, votar y denunciar abusos.
- Operadores: panel estadístico, cambio de estado, prioridad, **veracidad**, asignación, comentarios, renovar vigencia y moderación (ocultar/restaurar).
- Administradores: funciones de operador, gestión de roles y eliminación de cualquier reporte.
- La lista se refresca sola cada minuto sin pisar lo que estés haciendo (comentarios a medio escribir, detalles abiertos) y solo si hubo cambios.
- Los marcadores del mapa agrupan incidencias a menos de 20 m para que ninguna quede tapada por otra.
- Historial de cambios, fechas de resolución y notificaciones a quien creó el reporte (comentarios, cambios de estado **y cambios de veracidad**).
- Búsqueda de incidencias y ubicación, filtros por tipo, estado, **veracidad** y **comunidad**, y orden (más recientes / más antiguos / más confirmados).
- Ubicación GPS, selección de punto en el mapa y geocodificación directa/inversa. Mapa Leaflet con OpenStreetMap y capas GeoJSON locales.
- Persistencia relacional MySQL con modelos Django y migraciones.

### Veracidad, votos y reputación

Cada incidencia tiene un estado de veracidad: **sin verificar** (inicial), **confirmado por la comunidad**, **verificado por un operador**, **probablemente falso** y **falso (descartado)**.

- Cualquier usuario con sesión puede **confirmar** o **negar** un reporte ajeno (un voto por persona, modificable o retirable).
- Cambio automático (valores en `incidents/rules.py`): con puntaje neto ≥ 5 y al menos 3 personas confirmando pasa a *confirmado por la comunidad*; con puntaje neto ≤ −3 y al menos 3 personas negando pasa a *probablemente falso*. Con pesos 1.0, "5 confirmaciones" activan el primero.
- Un operador puede fijar *verificado* o *falso*; esos estados quedan fijos (los votos ya no los cambian). "Sin verificar" devuelve el control a los votos. Los reportes *falsos* dejan de mostrarse al público (los ve su autor y los operadores).
- **Reputación**: cada persona parte en 0. Cuando su reporte pasa a *confirmado* gana +5 (+8 si lo verifica un operador); *probablemente falso* −5; *falso* −15; ocultado por denuncias −5. Los cambios son reversibles (si el estado vuelve atrás, se devuelve el puntaje) y quedan en la bitácora `ReputationEvent` (visible en el admin).
- **Peso del voto** = `1 + reputación/50`, entre 0.25 y 2.0. Así quien reporta bien pesa más y quien reporta falsedades pesa menos.
- Tras recibir votos de otras personas, el autor ya no puede cambiar el tipo ni la ubicación de su reporte (solo la descripción), para evitar "confirmar y cambiar".

### Denuncias ("reportar incidencia")

El botón **⚑ Reportar abuso** permite denunciar bromas, spam, contenido ofensivo o información falsa. Cuando al menos 3 personas distintas denuncian (suma de pesos ≥ 3), el reporte **desaparece del público** y el autor recibe una notificación y la penalización de reputación. Los operadores lo ven con "Incluir ocultos y caducados" y pueden **restaurarlo** (se devuelve la reputación y se borran las denuncias).

### Caducidad

Cada incidencia caduca según su tipo (p. ej. corte de luz 3 días, accidente vial 2, derrumbe 14, actividad volcánica 30; ver `TYPE_CONFIG` en `rules.py`). Las caducadas **dejan de mostrarse solas**. Cada confirmación renueva la vigencia (hasta 2 veces el plazo original) y los operadores pueden usar "Renovar vigencia". Para limpiar la base de datos de caducadas hace más de 30 días, programa (cron / Programador de tareas):

```powershell
python manage.py purge_expired_incidents --older-than-days 30
```

### Comunidades

El modelo `Community` agrupa incidencias por zona. Se cargan las 16 regiones de Chile y cada incidencia se asigna a la comunidad **cuyo centro está más cerca**. El selector de comunidad filtra mapa, lista y estadísticas (por defecto *Los Lagos* para visitantes; los usuarios guardan la suya). Para comunidades más finas (p. ej. "Chiloé" o "Puerto Montt ciudad") créalas en `/admin/` → Communities con su latitud/longitud central; las incidencias **nuevas** se asignan según los centros vigentes.

### Detección de duplicados

Al crear un reporte, si existe uno vigente, no resuelto y del mismo tipo cerca (400 m por defecto; más amplio en incendios, volcanes, clima, etc.), la API responde `409` con los candidatos y la interfaz pregunta *"¿Es el mismo incidente?"*: **confirmar el existente** o **crear el propio** (`confirmDistinct: true`).

### Límite a Chile

El servidor rechaza coordenadas fuera de Chile (polígono simplificado del territorio continental, con ~10 km de holgura, más Juan Fernández, Rapa Nui, Salas y Gómez y Desventuradas; **no incluye el Territorio Chileno Antártico**). La interfaz valida lo mismo al elegir el punto. Si necesitas precisión de frontera, reemplaza `CHILE_POLYGON` en `incidents/geo.py` por el GeoJSON oficial.

### Registro de usuarios

`POST /api/auth/register` crea inmediatamente una cuenta de ciudadano y devuelve un token de sesión (`201`), por lo que el usuario queda ingresado al terminar el registro. No se envían correos de activación. Si el correo ya existe, responde `409`; por ahora basta con que la contraseña no esté vacía.
- Con `DJANGO_DEBUG=false` es obligatorio definir `DJANGO_SECRET_KEY`; la clave de desarrollo se rechaza al arrancar.

### Recuperación de contraseña

"¿Olvidaste tu contraseña?" envía un enlace de un solo uso válido 30 minutos (`/#reset=<token>`, en el fragmento de la URL para que no viaje al servidor ni en cabeceras Referer). La respuesta es **idéntica** exista o no el correo. Al cambiar la contraseña se cierran todas las sesiones. Login, registro y recuperación tienen límite de intentos. En desarrollo el correo se imprime en la consola del servidor; para correo real define `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` y `SITE_URL` (la URL pública usada en el enlace).

## API

La documentación de endpoints está disponible en `GET /api/docs`.

| Método | Ruta | Acceso |
| --- | --- | --- |
| POST | `/api/auth/register` | Público; crea ciudadano e inicia sesión |
| POST | `/api/auth/login` | Público |
| GET | `/api/auth/me` | Sesión |
| POST | `/api/auth/logout` | Sesión |
| GET | `/api/meta` | Público; tipos, comunidades, reglas y límites de Chile |
| POST | `/api/auth/password-reset/request` | Público; respuesta siempre `202` |
| POST | `/api/auth/password-reset/confirm` | Público; token de un solo uso |
| PUT | `/api/auth/me` | Sesión; cambia la comunidad |
| GET | `/api/incidents?community=&veracity=&include=all` | Público (`include=all`: operadores) |
| POST | `/api/incidents` | Sesión; `409` si hay posible duplicado |
| PUT / DELETE | `/api/incidents/:id` | Según rol y propiedad (el autor puede eliminar el suyo) |
| POST / DELETE | `/api/incidents/:id/vote` | Sesión; `{"value": "confirmar" \| "negar"}` |
| POST / DELETE | `/api/incidents/:id/flag` | Sesión; `{"reason": "broma" \| "falso" \| "spam" \| "ofensivo" \| "duplicado"}` |
| GET | `/api/admin/stats` | Operador o administrador |
| GET | `/api/users` | Operador o administrador |
| PUT | `/api/users/:id/role` | Administrador |
| GET | `/api/notifications` | Sesión |

Las rutas autenticadas reciben `Authorization: Bearer <token>`. Los tokens expiran a las 12 horas y se almacenan en la base de datos como hashes.

## Configuración

- `MYSQL_DATABASE`: nombre de base de datos (por defecto `los_lagos`).
- `MYSQL_USER` y `MYSQL_PASSWORD`: credenciales MySQL (por defecto `root` y contraseña vacía; define tus propios valores).
- `MYSQL_HOST` y `MYSQL_PORT`: servidor y puerto MySQL (por defecto `127.0.0.1:3306`).
- `DJANGO_DB_ENGINE`: motor de base de datos (`mysql` por defecto; `sqlite` para desarrollo sin servidor MySQL).
- `DJANGO_SECRET_KEY`: clave secreta de Django; define una clave privada antes de desplegar.
- `DJANGO_DEBUG`: activa el modo debug solo en desarrollo (`true`/`false`; por defecto `true` para `runserver`).
- `DJANGO_ALLOWED_HOSTS`: hosts permitidos separados por comas.
- `SITE_URL`, `EMAIL_*`, `DEFAULT_FROM_EMAIL`: correo de recuperación de contraseña. En desarrollo se imprime en la consola del servidor.
- `DATA_DIR`: directorio de los archivos JSON heredados que lee `import_legacy_data`.
- `ADMIN_EMAIL` y `ADMIN_PASSWORD`: credenciales para `bootstrap_admin`.

## Solución de problemas

- **El mapa muestra error 403 en los mosaicos:** OpenStreetMap exige que el navegador envíe la cabecera `Referer`. Django envía por defecto `Referrer-Policy: same-origin`, que la suprime. `SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"` en `settings.py` lo resuelve; el mapa además cambia solo a CARTO o Esri si un proveedor falla. Abre siempre la app desde <http://127.0.0.1:8000> (no abriendo `index.html` como archivo) y fuerza la recarga con `Ctrl+F5`.

## Pruebas y administración

Las pruebas locales no necesitan una instancia MySQL. En PowerShell selecciona SQLite antes de ejecutarlas:

```powershell
$env:DJANGO_DB_ENGINE = "sqlite"
python manage.py test
python manage.py check
```

Para entrar al sitio de administración de Django crea un superusuario con `python manage.py createsuperuser`. La cuenta inicial de rol administrador del API no recibe automáticamente permisos de acceso al admin de Django.
