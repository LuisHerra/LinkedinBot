# API de métricas

Un servicio de solo lectura en la VM que expone el estado del bot en JSON para tu web. Está hecho con la biblioteca estándar de Python, así que no añade dependencias y consume muy poca RAM.

| Endpoint | Token | Contenido |
|---|---|---|
| `GET /api/health` | No | `{"ok": true}` |
| `GET /api/summary` | No (salvo `METRICS_PUBLIC=0`) | Estado, contadores, planes, próxima operación, historial y operaciones de hoy. **No incluye datos de personas.** |
| `GET /api/metrics` | **Sí** | Lo mismo, más las invitaciones de hoy y de los últimos 7 días (nombre, perfil, contactos en común, seguidores, puntuación) y el detalle de cada operación |

El token se envía en la cabecera `Authorization: Bearer <METRICS_TOKEN>` o con el parámetro `?token=`. Usa la cabecera siempre que puedas: la URL puede quedar guardada en registros.

## Qué devuelve `/api/summary`

```jsonc
{
  "generated_at": "2026-11-04T12:30:00+01:00",
  "status": "active",                      // "active" | "paused"
  "paused_until": null,
  "last_check": "2026-11-04T12:14:51+01:00",   // última comprobación del temporizador
  "last_run": { "at": "2026-11-04T10:21:07+01:00", "sent": 2, "outcome": "ok", "dry_run": false },
  "today": {
    "date": "2026-11-04", "sent_bot": 2, "sent_manual": 1, "daily_cap": 25,
    "plan": { "quota": 5, "batches": [ { "hour": 10, "quota": 2, "done": true }, { "hour": 17, "quota": 3, "done": false } ] }
  },
  "week": { "rolling_7d": 41, "hard_cap": 100, "plan": { "week": "2026-W45", "target": 95, "days": { "2026-11-02": 18, "...": 0 } } },
  "totals": { "total_bot": 120, "total_manual": 9, "pending": 60, "resolved": 64, "withdrawn": 5, "last_7d": 41, "resolved_ratio": 0.516 },
  "next_operation": { "type": "batch", "quota": 3, "earliest": "2026-11-04T17:05:00+01:00", "latest": "2026-11-04T17:30:00+01:00",
                      "description": "Tanda de 3 invitaciones entre las 17:05 y las 17:30." },
  "history_30d": [ { "date": "2026-11-03", "bot": 14, "manual": 0 } ],
  "operations_today": [
    { "at": "2026-11-04T10:21:07+01:00", "kind": "run", "wanted": 2, "sent": 2, "outcome": "ok", "dry_run": false },
    { "at": "2026-11-04T10:20:58+01:00", "kind": "batch" },
    { "at": "2026-11-04T09:14:02+01:00", "kind": "day_plan" }
  ]
}
```

**Cómo leer algunos campos:**
- **`resolved`**: invitaciones que ya no están pendientes. Junta las aceptadas y las rechazadas, porque LinkedIn no distingue entre ellas en la lista de pendientes. `resolved_ratio` sirve como aproximación de la tasa de aceptación.
- **`last_run.outcome`**: `ok`, `checkpoint` (LinkedIn pide una verificación), `session_expired` o `error`.
- **`next_operation.type`**: `batch` (una tanda hoy), `day` (otro día de esta semana), `new_week` (empieza una semana nueva), `paused` o `none`.

## Activarla en la VM

```bash
cd ~/LinkedinBot && git pull
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # copia el resultado
nano .env
```

Si tu `.env` es anterior a esta función, añade al final:

```
METRICS_HOST=127.0.0.1
METRICS_PORT=8787
METRICS_TOKEN=el_token_generado
METRICS_PUBLIC=1
METRICS_CORS=https://tu-web.com
```

Después instala el servicio y prueba que responde:

```bash
bash deploy/setup.sh            # instala el servicio linkedin-bot-api (no toca lo ya instalado)
sudo systemctl enable --now linkedin-bot-api
curl -s localhost:8787/api/summary | head -30
```

## Publicarla con HTTPS (Cloudflare Tunnel)

La API escucha solo dentro de la VM. Tu web casi seguro usa HTTPS, y un navegador no deja que una página HTTPS lea un endpoint HTTP. Por eso conviene publicarla con **Cloudflare Tunnel**:
- Es gratis.
- Da HTTPS.
- No hace falta abrir ningún puerto en el cortafuegos de Google Cloud.

**Prueba rápida (URL temporal):**
```bash
curl -L -o cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel --url http://localhost:8787     # imprime una URL https://xxxx.trycloudflare.com
```

**Uso permanente (URL fija):** si tienes un dominio en Cloudflare (por ejemplo `kokoworks.es`), crea un túnel con nombre desde Zero Trust → Networks → Tunnels:
1. Apúntalo a `http://localhost:8787` con un subdominio como `bot.kokoworks.es`.
2. Instálalo en la VM con el comando que te da el panel (`sudo cloudflared service install <token>`).

## Consumirla desde tu web

**Directamente desde el navegador** (solo `/api/summary`, que no lleva datos personales):

```js
const res = await fetch("https://bot.kokoworks.es/api/summary");
const m = await res.json();
console.log(`${m.week.rolling_7d}/${m.week.hard_cap} esta semana — ${m.next_operation.description}`);
```

**Desde el backend de tu web** (para `/api/metrics`, con nombres y perfiles). **Nunca pongas el token en JavaScript del navegador**: cualquiera podría verlo y leer los datos de las personas a las que invitas.

```js
// p. ej. una función serverless / ruta de API de tu web
const res = await fetch("https://bot.kokoworks.es/api/metrics", {
  headers: { Authorization: `Bearer ${process.env.METRICS_TOKEN}` },
});
```
