# Tienda Fundas — app de gestión

App web para gestionar la tienda de fundas de libros: stock, reconocimiento de pedidos a partir de capturas de pantalla, CRM de pedidos, clientes y contabilidad. Mismo mecanismo que bot-nevera (Flask + base de datos), con un diseño mobile-first similar.

## Pestañas

- **📦 Stock** — alta/edición/baja de fundas, con aviso de stock bajo.
- **📸 Escanear** — sube una captura del chat donde el cliente pide fundas. Si hay API key de Anthropic configurada, la IA extrae automáticamente qué fundas y cuántas piden y prepara el pedido; si no, puedes rellenarlo a mano mirando la imagen.
- **🗂️ Pedidos (CRM)** — cada pedido tiene un estado: pendiente, preparado, enviado, entregado o cancelado. Al crear un pedido se descuenta el stock automáticamente.
- **👤 Clientes** — ficha de cada cliente con su historial de pedidos.
- **💶 Contabilidad** — ingresos y gastos, con balance y filtro por pedido.

Todo esto son pestañas de partida — puedes pedirme que cambie campos, estados, columnas, colores, etc. cuando quieras.

## 1. Instalar (primera vez)

Necesitas Python 3.10+ instalado. Desde una terminal, dentro de esta carpeta:

```bash
python3 -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configurar credenciales NUEVAS

Copia el archivo de ejemplo:

```bash
cp .env.example .env
```

Abre `.env` y rellena:

- `SECRET_KEY` — genera una nueva (no reutilices ninguna de otro proyecto):
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
  Copia el resultado como valor de `SECRET_KEY`.

- `ANTHROPIC_API_KEY` — opcional, pero necesaria para que la pestaña "Escanear" reconozca las fundas automáticamente. Crea una key **nueva y propia para este proyecto** en https://console.anthropic.com/settings/keys (no la compartas con bot-nevera). Si la dejas vacía, la app sigue funcionando: simplemente tendrás que rellenar el pedido a mano después de ver la captura.

- `DATABASE_URL` — déjalo comentado para usar SQLite local (`instance/store.db`). Se crea solo la primera vez que arrancas la app.

## 3. Arrancar la app

```bash
python3 app.py
```

Abre http://127.0.0.1:5000 en el navegador. La primera vez la base de datos estará vacía: añade tus fundas en Stock y tus clientes en Clientes.

## 4. Desplegarla (como bot-nevera, en Render)

1. Sube esta carpeta a un repositorio de GitHub nuevo.
2. En [Render](https://render.com), crea un **Web Service** nuevo apuntando a ese repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. En "Environment", añade las mismas variables que en `.env` (`SECRET_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`) con valores **nuevos**, generados igual que en el paso 2 — no copies las de bot-nevera.

Nota: SQLite en Render no persiste entre despliegues salvo que uses un disco persistente (Render → Disks). Si vas a usarla en producción de forma seria, dímelo y lo cambiamos a Postgres (Render lo ofrece gratis a pequeña escala).

## Estructura del proyecto

```
fundas-app/
├── app.py              # rutas de Flask (todas las pestañas)
├── models.py            # tablas: Producto, Cliente, Pedido, PedidoItem, Movimiento
├── ai_scan.py            # reconocimiento de capturas con Claude (vision)
├── config.py             # configuración / variables de entorno
├── templates/             # HTML de cada pestaña
├── static/css/style.css   # estilo visual (tipo bot-nevera)
└── static/uploads/        # capturas de pedidos subidas
```

## Próximos pasos / personalización

Estas pestañas son un punto de partida. Dime qué quieres cambiar y lo ajustamos: campos del stock, más estados de pedido, categorías de gastos, exportar a Excel, gráficas de ventas, notificaciones, etc.
