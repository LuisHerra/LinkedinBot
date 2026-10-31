# linkedin-growth

Envía invitaciones de LinkedIn a contactos de 2º grado. Prioriza a quienes tienen muchos contactos en común contigo y pocos seguidores. Se ejecuta desde GitHub Actions a través de un proxy residencial fijo.

> ⚠️ Las Condiciones de LinkedIn prohíben la automatización. El riesgo es que restrinjan tu cuenta. Los topes y pausas de este proyecto lo reducen, pero no lo eliminan.

## Cómo funciona

1. **Plan semanal**: un objetivo aleatorio entre 90 y 100 (50 y 75 en las dos primeras semanas de calentamiento). Se reparte entre los días con pesos aleatorios, así hay días fuertes, días flojos y 0–2 días de descanso. Ningún día pasa de 25.
2. **Plan diario**: la cuota del día se divide en 2–5 tandas, a horas aleatorias entre las 9 y las 20 h (hora de Madrid).
3. **Cada hora**, el cron comprueba sin abrir navegador si toca tanda. Si toca:
   - Abre Chromium con tu cookie `li_at` a través del proxy principal.
   - **Sincroniza las invitaciones pendientes.** Las que no mandó el bot son tuyas, se guardan como `manual` y cuentan para el tope.
   - Una vez al día, **retira las pendientes de más de 21 días**.
   - Calcula cuántas puede enviar: `min(cuota de la tanda, 100 − enviadas en los últimos 7 días, 25 − enviadas hoy)`.
   - Busca contactos de 2º grado con una palabra clave al azar, se queda con los que tienen más contactos en común, visita sus perfiles para leer seguidores y modo creador, los puntúa y envía a los mejores, con pausas de 45–150 s entre invitaciones.
4. El estado (`data/state.db`, en SQLite) se guarda en el repositorio después de cada ejecución.

**Segmentos**: en cada tanda se elige un segmento según su peso: CayoTV y alumni de la UAH y Paris-Saclay ×3; Telefónica / Ellas Hablan Código, AWS/cloud, IA/datos y desarrollo web ×2; recruiters tech ×1. Dentro del segmento se toma una palabra clave al azar. Todo está en `config.yaml`.

**Puntuación**: `3·ln(1+comunes) − 1.5·log10(seguidores) + 1 si el titular encaja + 2 si el perfil comparte universidad/programa/AWS − 1.5 si es muy senior`. Se descartan los perfiles con menos de 3 contactos en común, más de 10k seguidores o modo creador.

**Pausas automáticas**:
- Si LinkedIn pide una verificación (captcha o código), 48 h. El bot nunca intenta resolverla: entras tú desde tu navegador.
- Si avisa del límite semanal, 72 h.
- Si la cookie caduca, hasta que la renueves.

## Despliegue recomendado: VM en Google Cloud

Sigue **[deploy/GCP.md](deploy/GCP.md)**: una e2-micro gratuita con un perfil de navegador persistente, en la que inicias sesión en LinkedIn una sola vez. Es la opción estable.

Con GitHub Actions, LinkedIn invalidó la sesión en las pruebas. Cada ejecución arranca un navegador nuevo con cookies copiadas, y para LinkedIn eso es un dispositivo distinto cada hora. La sección siguiente se mantiene como referencia, pero el cron solo se activa con `BOT_ENABLED=true`.

## Puesta en marcha (GitHub Actions, no recomendado)

1. Crea un repositorio **privado**: el estado contiene nombres y perfiles.
2. Añade estos secretos en Settings → Secrets and variables → Actions:
   - `LI_AT`: el valor de la cookie `li_at`. Para obtenerlo, en Chrome con LinkedIn abierto: F12 → Application → Cookies → linkedin.com.
   - `PROXY_PRIMARY`: `http://usuario:contraseña@host:puerto`. Que sea una IP residencial **en tu país y, si puede ser, en tu ciudad**.
   - `PROXY_SECONDARY`: otra igual. Solo se usa como reserva.
3. **Antes de activar el bot, entra una vez en LinkedIn a mano desde ese mismo proxy** (por ejemplo, con un perfil de Chrome configurado con él). Así LinkedIn ya conoce esa IP asociada a tu sesión. Después copia el `li_at` de esa sesión.
4. Actions → linkedin-invites → Run workflow → `dry-run` con quota 3. Hace todo menos pulsar "Enviar". Revisa el log y, si falla, las capturas del artefacto `screenshots`.
5. Cuando el dry-run vaya bien, prueba `force` con quota 1–2 y comprueba en LinkedIn que la invitación ha salido. A partir de ahí el cron funciona solo.

## Elegir los proxies

Para ver dónde está cada proxy y elegir los dos más cercanos a Madrid, mete tus proxies en `proxies.txt`, uno por línea (el archivo está en `.gitignore`), y ejecuta:

```bash
python tools/check_proxies.py proxies.txt
```

El script descarta los que salen como datacenter y recomienda un principal y una reserva del mismo país.

## Proxy: por qué `sticky` por defecto

Cada cambio de IP en una sesión es una señal más para LinkedIn, sobre todo si las dos IPs están en ciudades distintas. Por eso, en modo `sticky` se usa siempre el principal y el secundario solo entra si el principal no responde. El modo `weekly` alterna de IP una vez por semana ISO. Si lo activas, que las dos IPs estén en la misma zona. Cada cambio de IP queda registrado como evento `proxy_ip_changed`.

## Comandos locales

```bash
pip install -r requirements.txt
python -m bot plan      # plan de la semana y del día
python -m bot stats     # enviadas, pendientes, resueltas, últimos 7 días
python -m bot unpause
python -m pytest tests
```

## Limitaciones conocidas

- **Selectores**: LinkedIn cambia su HTML a menudo. Están basados en roles y aria-labels, en español e inglés. Si algo se rompe, la captura muestra en qué pantalla.
- **Invitaciones manuales**: se detectan en la lista de pendientes. Si mandas una a mano y la aceptan antes de que el bot vuelva a mirar, no se cuenta. El objetivo de 90–100 deja margen para eso.
- **"Resueltas"** junta aceptadas y rechazadas: la lista de pendientes no distingue entre ellas.
- **Coste en minutos de Actions**: unas 14 ejecuciones cortas al día más 2–5 tandas de 10–20 min salen a unos 1.500–2.000 min/mes. El plan gratuito para repos privados incluye 2.000.
