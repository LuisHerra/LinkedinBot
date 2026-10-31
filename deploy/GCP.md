# Despliegue en Google Cloud (e2-micro gratuita)

El bot se ejecuta en una VM propia con un **perfil de navegador persistente**. Inicias sesión en LinkedIn una sola vez desde la VM, y esa sesión queda guardada en ella. Nunca se copian cookies entre máquinas: eso era lo que provocaba que LinkedIn invalidara la sesión en GitHub Actions.

## 1. Cuenta y presupuesto

1. Entra en [console.cloud.google.com](https://console.cloud.google.com), crea un proyecto (por ejemplo `linkedin-bot`) y activa la facturación. Google pide tarjeta incluso para el nivel gratuito.
2. Ve a Facturación → **Presupuestos y alertas** y crea un presupuesto de **1 €** con alertas al 50 % y al 100 %. Así sabrás enseguida si algo deja de ser gratis.

## 2. Crear la VM

Ve a Compute Engine → Instancias de VM → **Crear instancia** y configura:

| Campo | Valor |
|---|---|
| Nombre | `linkedin-bot` |
| Región | **us-central1**, us-west1 o us-east1 (solo estas son gratis) |
| Tipo de máquina | **e2-micro** |
| Disco de arranque | **Ubuntu 24.04 LTS (x86/64)**, **Disco persistente estándar**, 30 GB |
| Cortafuegos | Deja sin marcar HTTP y HTTPS |

- **El tipo de disco importa.** El disco "equilibrado" que sale por defecto **no** es gratuito.
- **La IP pública.** Desde 2024 Google cobra las IPv4 externas en muchos casos. Mira el coste estimado que muestra el formulario y la factura del primer día. Si aparece, son unos 3 $/mes, y la alerta de presupuesto te avisará.

## 3. Conectarte por SSH desde tu PC

Instala [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) en Windows y luego ejecuta:

```bash
gcloud init
gcloud compute ssh linkedin-bot --zone us-central1-a
```

Usa la zona que elegiste. El primer `ssh` genera la clave automáticamente.

## 4. Instalar el bot (dentro de la VM)

El repositorio es privado. Crea un token de solo lectura en GitHub → Settings → Developer settings → Fine-grained tokens, con acceso a *LinkedinBot* y permiso *Contents: Read*. Cuando `git` pida la contraseña, pega el token.

```bash
git clone https://github.com/LuisHerra/LinkedinBot.git
cd LinkedinBot
bash deploy/setup.sh
nano .env      # PROXY_USER y PROXY_PASS; los proxies ya vienen puestos
```

## 5. Iniciar sesión en LinkedIn (una vez)

Desde **tu PC**, abre un túnel SSH que lleve el escritorio remoto a `localhost:6080`:

```bash
gcloud compute ssh linkedin-bot --zone us-central1-a -- -L 6080:localhost:6080
```

En esa misma sesión, ya dentro de la VM:

```bash
cd LinkedinBot && bash deploy/login.sh
```

1. Abre en tu navegador **http://localhost:6080/vnc.html** y pulsa *Connect*. Verás el Chromium del bot saliendo por el proxy de París.
2. Inicia sesión en LinkedIn. Si te pide un código o una verificación, resuélvelos ahí mismo.
3. Cuando llegues al feed, la terminal mostrará `Sesión iniciada y guardada en el perfil.` y el navegador se cerrará solo.

## 6. Probar y activar

```bash
xvfb-run -a .venv/bin/python -m bot run --force --quota 2 --dry-run   # todo menos pulsar Conectar
sudo systemctl enable --now linkedin-bot.timer                        # activa el funcionamiento diario
```

A partir de aquí, el temporizador lanza el bot cada hora entre las 9 y las 20 h, con un retraso aleatorio de hasta 25 minutos. El plan de 90–100 invitaciones a la semana decide en qué horas toca tanda.

## Métricas para tu web (opcional)

En la VM puedes activar una API JSON con el estado del bot, los envíos, las operaciones y la próxima tanda. Los pasos están en **[METRICS.md](METRICS.md)**.

## Uso diario

```bash
journalctl -u linkedin-bot -n 50          # log de las últimas ejecuciones
.venv/bin/python -m bot stats             # enviadas, pendientes, últimos 7 días
.venv/bin/python -m bot plan              # plan de la semana y de hoy
systemctl list-timers linkedin-bot.timer  # próxima ejecución
git pull                                  # actualizar el código
```

**Si LinkedIn cierra la sesión o pide una verificación**, el bot se pone en pausa solo. Repite el paso 5 y después ejecuta:

```bash
.venv/bin/python -m bot unpause
```

**Ten en cuenta:**
- Las sesiones de tu móvil y tu PC son independientes de la de la VM, así que puedes seguir usando LinkedIn con normalidad.
- **No** ejecutes el bot en tu PC a la vez que en la VM: cada uno lleva su propio estado y se saltarían el tope semanal del otro.
