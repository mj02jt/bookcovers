import io
import os
import uuid
import zipfile
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, send_file
from werkzeug.utils import secure_filename
from sqlalchemy import text
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.utils import ImageReader

from config import Config
from extensions import db
from models import Producto, Cliente, Pedido, PedidoItem, Movimiento, MetodoEnvio, ESTADOS_PEDIDO, TIPOS_MOVIMIENTO
import ai_scan

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def leer_imagen_subida(file):
    """Lee una imagen subida y devuelve (bytes, mimetype) o (None, None).
    Se guarda dentro de la base de datos para que no se pierda entre despliegues."""
    if not file or file.filename == '' or not allowed_file(file.filename):
        return None, None
    data = file.read()
    if not data:
        return None, None
    mimetype = file.mimetype or 'image/jpeg'
    return data, mimetype


def resolver_cliente_id(form):
    """Si el usuario eligió '+ Crear cliente nuevo', lo crea y devuelve su id."""
    cliente_id = form.get('cliente_id') or None
    if cliente_id == 'nuevo':
        nombre_nuevo = (form.get('nuevo_cliente_nombre') or '').strip()
        if nombre_nuevo:
            nuevo = Cliente(
                nombre=nombre_nuevo,
                telefono=form.get('nuevo_cliente_telefono'),
                instagram=form.get('nuevo_cliente_instagram'),
            )
            db.session.add(nuevo)
            db.session.flush()  # asigna el id sin cerrar la transacción
            return nuevo.id
        return None
    return int(cliente_id) if cliente_id else None


def _cargar_fuente(tam, negrita=True):
    rutas = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if negrita else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    ]
    for ruta in rutas:
        try:
            return ImageFont.truetype(ruta, tam)
        except Exception:
            continue
    return ImageFont.load_default()


def generar_imagen_catalogo(producto):
    """Genera una imagen cuadrada de la funda con el nombre y el precio superpuestos,
    lista para compartir con clientes por WhatsApp/Instagram."""
    ANCHO, ALTO, BANDA = 800, 800, 150
    alto_foto = ALTO - BANDA

    base = None
    if producto.foto_data:
        try:
            base = Image.open(io.BytesIO(producto.foto_data)).convert('RGB')
        except Exception:
            base = None

    lienzo = Image.new('RGB', (ANCHO, ALTO), '#fdf3f5')
    if base:
        ratio = max(ANCHO / base.width, alto_foto / base.height)
        nuevo = base.resize((max(1, int(base.width * ratio)), max(1, int(base.height * ratio))))
        x = max(0, (nuevo.width - ANCHO) // 2)
        y = max(0, (nuevo.height - alto_foto) // 2)
        recorte = nuevo.crop((x, y, x + ANCHO, y + alto_foto))
        lienzo.paste(recorte, (0, 0))
    else:
        draw_vacio = ImageDraw.Draw(lienzo)
        fuente_vacio = _cargar_fuente(46)
        draw_vacio.multiline_text((ANCHO / 2, alto_foto / 2), producto.nombre,
                                   font=fuente_vacio, anchor='mm', fill='#b8576d', align='center')

    draw = ImageDraw.Draw(lienzo)
    draw.rectangle([0, alto_foto, ANCHO, ALTO], fill='#b8576d')
    nombre_txt = producto.nombre
    if producto.modelo or producto.color:
        nombre_txt += f' · {(producto.modelo or "")} {(producto.color or "")}'.rstrip()
    draw.text((28, alto_foto + 18), nombre_txt[:40], font=_cargar_fuente(32), fill='white')
    draw.text((28, alto_foto + 68), f'{producto.precio:.2f} €', font=_cargar_fuente(46), fill='white')

    buf = io.BytesIO()
    lienzo.save(buf, format='JPEG', quality=88)
    buf.seek(0)
    return buf


def generar_pdf_catalogo(productos):
    """Genera un PDF A4 con una rejilla de fundas: foto, nombre, tamaño y precio.
    Pensado para imprimir o enviar como catálogo de stock actualizado."""
    COLS, ROWS = 3, 3
    MARGEN = 12 * mm
    GAP = 6 * mm
    CABECERA = 14 * mm

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    ancho_pag, alto_pag = A4

    cell_w = (ancho_pag - 2 * MARGEN - (COLS - 1) * GAP) / COLS
    cell_h = (alto_pag - 2 * MARGEN - CABECERA - (ROWS - 1) * GAP) / ROWS
    img_box_h = cell_h - 16 * mm

    def dibujar_cabecera():
        c.setFont('Helvetica-Bold', 14)
        c.drawString(MARGEN, alto_pag - MARGEN + 2, 'Catálogo BOOKCOVERS · Stock actual')
        c.setFont('Helvetica', 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawRightString(ancho_pag - MARGEN, alto_pag - MARGEN + 2,
                           datetime.utcnow().strftime('%d/%m/%Y'))
        c.setFillColorRGB(0, 0, 0)

    por_pagina = COLS * ROWS
    for i, p in enumerate(productos):
        idx_en_pagina = i % por_pagina
        if idx_en_pagina == 0:
            if i != 0:
                c.showPage()
            dibujar_cabecera()

        row = idx_en_pagina // COLS
        col = idx_en_pagina % COLS
        x = MARGEN + col * (cell_w + GAP)
        y_top = alto_pag - MARGEN - CABECERA - row * (cell_h + GAP)

        # borde sutil de la tarjeta
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.rect(x, y_top - cell_h, cell_w, cell_h)

        # foto
        img_reader = None
        if p.foto_data:
            try:
                im = Image.open(io.BytesIO(p.foto_data)).convert('RGB')
                imgbuf = io.BytesIO()
                im.save(imgbuf, format='JPEG', quality=85)
                imgbuf.seek(0)
                img_reader = ImageReader(imgbuf)
            except Exception:
                img_reader = None

        if img_reader:
            iw, ih = img_reader.getSize()
            escala = min(cell_w / iw, img_box_h / ih)
            draw_w, draw_h = iw * escala, ih * escala
            img_x = x + (cell_w - draw_w) / 2
            img_y = y_top - img_box_h + (img_box_h - draw_h) / 2
            c.drawImage(img_reader, img_x, img_y, width=draw_w, height=draw_h,
                        preserveAspectRatio=True, mask='auto')
        else:
            c.setFont('Helvetica', 8)
            c.setFillColorRGB(0.6, 0.6, 0.6)
            c.drawCentredString(x + cell_w / 2, y_top - img_box_h / 2, 'Sin foto')
            c.setFillColorRGB(0, 0, 0)

        # textos: nombre, tamaño, precio
        centro_x = x + cell_w / 2
        texto_y = y_top - img_box_h - 5 * mm
        c.setFont('Helvetica-Bold', 9)
        nombre = p.nombre if len(p.nombre) <= 26 else p.nombre[:24] + '…'
        c.drawCentredString(centro_x, texto_y, nombre)

        c.setFont('Helvetica', 8)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        tamano = p.modelo or 'Tamaño único'
        c.drawCentredString(centro_x, texto_y - 4.5 * mm, tamano[:30])
        c.setFillColorRGB(0, 0, 0)

        c.setFont('Helvetica-Bold', 10)
        c.drawCentredString(centro_x, texto_y - 9.5 * mm, f'{p.precio:.2f} €')

    c.save()
    buf.seek(0)
    return buf


CATEGORIA_VENTA_AUTO = 'Venta (pedido entregado)'


def sincronizar_contabilidad_pedido(pedido):
    """Cuando un pedido pasa a 'entregado' se registra el ingreso automáticamente
    (una única vez). Si deja de estar entregado, se retira ese ingreso automático
    para que la contabilidad no se descuadre."""
    existente = Movimiento.query.filter_by(pedido_id=pedido.id, categoria=CATEGORIA_VENTA_AUTO).first()
    if pedido.estado == 'entregado':
        cliente_nombre = pedido.cliente.nombre if pedido.cliente else 'Sin cliente'
        if not existente:
            db.session.add(Movimiento(
                tipo='ingreso',
                concepto=f'Pedido #{pedido.id} · {cliente_nombre}',
                importe=pedido.total,
                categoria=CATEGORIA_VENTA_AUTO,
                pedido_id=pedido.id,
            ))
        else:
            # El pedido ya estaba entregado y se ha editado: mantenemos el importe al día
            existente.importe = pedido.total
            existente.concepto = f'Pedido #{pedido.id} · {cliente_nombre}'
    elif existente:
        db.session.delete(existente)


def resolver_envio(form):
    """Devuelve (metodo_envio_id, precio_envio) a partir del formulario."""
    envio_id = form.get('metodo_envio_id') or None
    if not envio_id:
        return None, 0.0
    metodo = db.session.get(MetodoEnvio, int(envio_id))
    if not metodo:
        return None, 0.0
    return metodo.id, metodo.precio


def procesar_items_pedido(pedido, form):
    """Crea los PedidoItem de un formulario, usando siempre el precio del stock cuando hay producto."""
    descripciones = form.getlist('descripcion')
    cantidades = form.getlist('cantidad')
    producto_ids = form.getlist('producto_id')
    precios = form.getlist('precio')
    total = 0.0
    for desc, cant, pid, precio in zip(descripciones, cantidades, producto_ids, precios):
        if not desc and not pid:
            continue
        cant = int(cant or 1)
        prod = db.session.get(Producto, int(pid)) if pid else None
        precio_final = prod.precio if prod else float(precio or 0)
        desc_final = desc or (prod.nombre if prod else '')
        db.session.add(PedidoItem(
            pedido=pedido, producto_id=prod.id if prod else None,
            descripcion=desc_final, cantidad=cant, precio_unitario=precio_final,
        ))
        total += cant * precio_final
        if prod:
            prod.cantidad = max(0, prod.cantidad - cant)
    return total


MIGRACIONES = [
    "ALTER TABLE productos ADD COLUMN IF NOT EXISTS foto VARCHAR(250)",
    "ALTER TABLE productos ADD COLUMN IF NOT EXISTS foto_data BYTEA",
    "ALTER TABLE productos ADD COLUMN IF NOT EXISTS foto_mimetype VARCHAR(50)",
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS captura_data BYTEA",
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS captura_mimetype VARCHAR(50)",
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS metodo_envio_id INTEGER REFERENCES metodos_envio(id)",
    "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS envio_precio FLOAT DEFAULT 0",
]


def run_migrations(app):
    """Pequeñas migraciones para bases de datos ya creadas antes de añadir columnas nuevas.
    En sqlite (desarrollo local) no hace falta: create_all ya crea las columnas nuevas."""
    with app.app_context():
        if not str(db.engine.url).startswith('postgresql'):
            return
        for sql in MIGRACIONES:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(sql))
            except Exception:
                pass  # ya existe la columna


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(os.path.join(app.instance_path), exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
    run_migrations(app)
    register_routes(app)
    return app


def register_routes(app):

    @app.route('/')
    def index():
        return redirect(url_for('stock_list'))

    # ---------------- IMÁGENES (guardadas en la base de datos) ----------------
    @app.route('/imagen/producto/<int:pid>')
    def producto_foto(pid):
        p = Producto.query.get_or_404(pid)
        if not p.foto_data:
            return '', 404
        return Response(p.foto_data, mimetype=p.foto_mimetype or 'image/jpeg')

    @app.route('/imagen/pedido/<int:oid>')
    def pedido_captura_img(oid):
        pedido = Pedido.query.get_or_404(oid)
        if not pedido.captura_data:
            return '', 404
        return Response(pedido.captura_data, mimetype=pedido.captura_mimetype or 'image/jpeg')

    # ---------------- STOCK ----------------
    @app.route('/stock')
    def stock_list():
        productos = Producto.query.order_by(Producto.nombre).all()
        return render_template('stock.html', productos=productos, active='stock')

    @app.route('/stock/nuevo', methods=['GET', 'POST'])
    def stock_new():
        if request.method == 'POST':
            foto_data, foto_mimetype = leer_imagen_subida(request.files.get('foto'))
            p = Producto(
                nombre=request.form['nombre'],
                modelo=request.form.get('modelo'),
                color=request.form.get('color'),
                icono=request.form.get('icono') or '📖',
                foto_data=foto_data,
                foto_mimetype=foto_mimetype,
                precio=float(request.form.get('precio') or 0),
                cantidad=int(request.form.get('cantidad') or 0),
                stock_minimo=int(request.form.get('stock_minimo') or 3),
            )
            db.session.add(p)
            db.session.commit()
            flash(f'"{p.nombre}" añadido al stock.')
            return redirect(url_for('stock_list'))
        return render_template('stock_form.html', producto=None, active='stock')

    @app.route('/stock/<int:pid>/editar', methods=['GET', 'POST'])
    def stock_edit(pid):
        p = Producto.query.get_or_404(pid)
        if request.method == 'POST':
            p.nombre = request.form['nombre']
            p.modelo = request.form.get('modelo')
            p.color = request.form.get('color')
            p.icono = request.form.get('icono') or '📖'
            foto_data, foto_mimetype = leer_imagen_subida(request.files.get('foto'))
            if foto_data:
                p.foto_data = foto_data
                p.foto_mimetype = foto_mimetype
            elif request.form.get('quitar_foto'):
                p.foto_data = None
                p.foto_mimetype = None
            p.precio = float(request.form.get('precio') or 0)
            p.cantidad = int(request.form.get('cantidad') or 0)
            p.stock_minimo = int(request.form.get('stock_minimo') or 3)
            db.session.commit()
            flash(f'"{p.nombre}" actualizado.')
            return redirect(url_for('stock_list'))
        return render_template('stock_form.html', producto=p, active='stock')

    @app.route('/stock/catalogo')
    def stock_catalogo():
        productos = Producto.query.filter(Producto.cantidad > 0).order_by(Producto.nombre).all()
        if not productos:
            flash('No hay fundas con stock disponible para generar el catálogo.')
            return redirect(url_for('stock_list'))

        buf_zip = io.BytesIO()
        with zipfile.ZipFile(buf_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            usados = set()
            for p in productos:
                img_buf = generar_imagen_catalogo(p)
                base_nombre = secure_filename(p.nombre) or f'funda-{p.id}'
                nombre_archivo = f'{base_nombre}.jpg'
                i = 2
                while nombre_archivo in usados:
                    nombre_archivo = f'{base_nombre}-{i}.jpg'
                    i += 1
                usados.add(nombre_archivo)
                zf.writestr(nombre_archivo, img_buf.getvalue())
        buf_zip.seek(0)

        fecha = datetime.utcnow().strftime('%Y%m%d')
        return send_file(
            buf_zip, mimetype='application/zip', as_attachment=True,
            download_name=f'catalogo_bookcovers_{fecha}.zip',
        )

    @app.route('/stock/catalogo/pdf')
    def stock_catalogo_pdf():
        productos = Producto.query.filter(Producto.cantidad > 0).order_by(Producto.nombre).all()
        if not productos:
            flash('No hay fundas con stock disponible para generar el catálogo.')
            return redirect(url_for('stock_list'))

        buf_pdf = generar_pdf_catalogo(productos)
        fecha = datetime.utcnow().strftime('%Y%m%d')
        return send_file(
            buf_pdf, mimetype='application/pdf', as_attachment=True,
            download_name=f'catalogo_bookcovers_{fecha}.pdf',
        )

    @app.route('/stock/<int:pid>/borrar', methods=['POST'])
    def stock_delete(pid):
        p = Producto.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        flash(f'"{p.nombre}" eliminado del stock.')
        return redirect(url_for('stock_list'))

    # ---------------- MÉTODOS DE ENVÍO (Vinted, Correos, recogida en mano...) ----------------
    @app.route('/envios')
    def envios_list():
        envios = MetodoEnvio.query.order_by(MetodoEnvio.nombre).all()
        return render_template('envios.html', envios=envios, active='stock')

    @app.route('/envios/nuevo', methods=['GET', 'POST'])
    def envio_new():
        if request.method == 'POST':
            e = MetodoEnvio(
                nombre=request.form['nombre'],
                precio=float(request.form.get('precio') or 0),
                activo=True,
            )
            db.session.add(e)
            db.session.commit()
            flash(f'"{e.nombre}" añadido a envíos.')
            return redirect(url_for('envios_list'))
        return render_template('envio_form.html', envio=None, active='stock')

    @app.route('/envios/<int:eid>/editar', methods=['GET', 'POST'])
    def envio_edit(eid):
        e = MetodoEnvio.query.get_or_404(eid)
        if request.method == 'POST':
            e.nombre = request.form['nombre']
            e.precio = float(request.form.get('precio') or 0)
            e.activo = bool(request.form.get('activo'))
            db.session.commit()
            flash(f'"{e.nombre}" actualizado.')
            return redirect(url_for('envios_list'))
        return render_template('envio_form.html', envio=e, active='stock')

    @app.route('/envios/<int:eid>/borrar', methods=['POST'])
    def envio_delete(eid):
        e = MetodoEnvio.query.get_or_404(eid)
        db.session.delete(e)
        db.session.commit()
        flash(f'"{e.nombre}" eliminado de envíos.')
        return redirect(url_for('envios_list'))

    # ---------------- ESCANEAR PEDIDO ----------------
    @app.route('/escanear', methods=['GET'])
    def scan_upload():
        return render_template('scan.html', active='scan', api_disponible=bool(app.config['ANTHROPIC_API_KEY']))

    @app.route('/escanear/procesar', methods=['POST'])
    def scan_process():
        file = request.files.get('captura')
        if not file or file.filename == '':
            flash('Selecciona una imagen primero.')
            return redirect(url_for('scan_upload'))
        if not allowed_file(file.filename):
            flash('Formato de imagen no soportado.')
            return redirect(url_for('scan_upload'))

        fname = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
        fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        file.save(fpath)
        rel_path = f"uploads/{fname}"

        detected_items = []
        cliente_detectado = None
        error = None

        if app.config['ANTHROPIC_API_KEY']:
            try:
                catalogo = [
                    {'id': p.id, 'nombre': p.nombre, 'modelo': p.modelo, 'color': p.color}
                    for p in Producto.query.all()
                ]
                resultado = ai_scan.reconocer_pedido(fpath, catalogo, app.config['ANTHROPIC_API_KEY'])
                detected_items = resultado.get('items', [])
                cliente_detectado = resultado.get('cliente_detectado')
            except Exception as e:
                error = f'No se pudo analizar la imagen automáticamente: {e}'
        else:
            error = 'No hay API key configurada: revisa la imagen y rellena el pedido manualmente.'

        productos = Producto.query.order_by(Producto.nombre).all()
        clientes = Cliente.query.order_by(Cliente.nombre).all()
        envios = MetodoEnvio.query.filter_by(activo=True).order_by(MetodoEnvio.nombre).all()
        return render_template(
            'scan_review.html', active='scan',
            imagen=rel_path, items=detected_items, error=error,
            cliente_detectado=cliente_detectado,
            productos=productos, clientes=clientes, envios=envios,
        )

    @app.route('/escanear/crear-pedido', methods=['POST'])
    def scan_create_order():
        imagen = request.form.get('imagen')  # ruta temporal en disco (uploads/xxx)

        cliente_id = resolver_cliente_id(request.form)
        pedido = Pedido(cliente_id=cliente_id, estado='pendiente')

        # Guardamos la captura dentro de la base de datos para que no se pierda en el próximo despliegue
        if imagen:
            fpath = os.path.join(app.config['UPLOAD_FOLDER'], os.path.basename(imagen))
            if os.path.exists(fpath):
                with open(fpath, 'rb') as f:
                    pedido.captura_data = f.read()
                ext = imagen.rsplit('.', 1)[-1].lower()
                pedido.captura_mimetype = {
                    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'webp': 'image/webp', 'gif': 'image/gif',
                }.get(ext, 'image/jpeg')

        db.session.add(pedido)
        pedido.metodo_envio_id, pedido.envio_precio = resolver_envio(request.form)
        pedido.total = procesar_items_pedido(pedido, request.form) + pedido.envio_precio
        db.session.commit()
        flash(f'Pedido #{pedido.id} creado.')
        return redirect(url_for('order_detail', oid=pedido.id))

    # ---------------- PEDIDOS (CRM) ----------------
    @app.route('/pedidos')
    def orders_list():
        pedidos = Pedido.query.order_by(Pedido.creado.desc()).all()
        columnas = {e: [p for p in pedidos if p.estado == e] for e in ESTADOS_PEDIDO}
        return render_template('orders.html', columnas=columnas, estados=ESTADOS_PEDIDO, active='orders')

    @app.route('/pedidos/nuevo', methods=['GET', 'POST'])
    def order_new():
        productos = Producto.query.order_by(Producto.nombre).all()
        clientes = Cliente.query.order_by(Cliente.nombre).all()
        envios = MetodoEnvio.query.filter_by(activo=True).order_by(MetodoEnvio.nombre).all()
        if request.method == 'POST':
            cliente_id = resolver_cliente_id(request.form)
            pedido = Pedido(cliente_id=cliente_id, estado='pendiente', notas=request.form.get('notas'))
            db.session.add(pedido)

            pedido.metodo_envio_id, pedido.envio_precio = resolver_envio(request.form)
            pedido.total = procesar_items_pedido(pedido, request.form) + pedido.envio_precio
            db.session.commit()
            flash(f'Pedido #{pedido.id} creado.')
            return redirect(url_for('order_detail', oid=pedido.id))
        return render_template('order_form.html', productos=productos, clientes=clientes, envios=envios, active='orders')

    @app.route('/pedidos/<int:oid>')
    def order_detail(oid):
        pedido = Pedido.query.get_or_404(oid)
        return render_template('order_detail.html', pedido=pedido, estados=ESTADOS_PEDIDO, active='orders')

    @app.route('/pedidos/<int:oid>/editar', methods=['GET', 'POST'])
    def order_edit(oid):
        pedido = Pedido.query.get_or_404(oid)
        productos = Producto.query.order_by(Producto.nombre).all()
        clientes = Cliente.query.order_by(Cliente.nombre).all()
        envios = MetodoEnvio.query.filter_by(activo=True).order_by(MetodoEnvio.nombre).all()

        if request.method == 'POST':
            # Devolvemos al stock las cantidades del pedido tal y como estaba antes de editarlo
            for item in pedido.items:
                if item.producto:
                    item.producto.cantidad += item.cantidad
                db.session.delete(item)
            db.session.flush()

            pedido.cliente_id = resolver_cliente_id(request.form)
            pedido.notas = request.form.get('notas')
            pedido.metodo_envio_id, pedido.envio_precio = resolver_envio(request.form)
            pedido.total = procesar_items_pedido(pedido, request.form) + pedido.envio_precio
            pedido.actualizado = datetime.utcnow()
            sincronizar_contabilidad_pedido(pedido)
            db.session.commit()
            flash(f'Pedido #{pedido.id} actualizado.')
            return redirect(url_for('order_detail', oid=pedido.id))

        items_actuales = [
            {'cantidad': it.cantidad, 'producto_id': it.producto_id, 'descripcion': it.descripcion}
            for it in pedido.items
        ]
        return render_template(
            'order_edit.html', pedido=pedido, productos=productos, clientes=clientes, envios=envios,
            items_actuales=items_actuales, active='orders',
        )

    @app.route('/pedidos/<int:oid>/estado', methods=['POST'])
    def order_update_status(oid):
        pedido = Pedido.query.get_or_404(oid)
        nuevo_estado = request.form.get('estado')
        if nuevo_estado in ESTADOS_PEDIDO:
            pedido.estado = nuevo_estado
            pedido.actualizado = datetime.utcnow()
            sincronizar_contabilidad_pedido(pedido)
            db.session.commit()
            flash(f'Pedido #{pedido.id} ahora está "{nuevo_estado}".')
        return redirect(url_for('order_detail', oid=oid))

    @app.route('/pedidos/<int:oid>/mover/<direction>', methods=['POST'])
    def order_move(oid, direction):
        pedido = Pedido.query.get_or_404(oid)
        idx = ESTADOS_PEDIDO.index(pedido.estado) if pedido.estado in ESTADOS_PEDIDO else 0
        if direction == 'next' and idx < len(ESTADOS_PEDIDO) - 1:
            pedido.estado = ESTADOS_PEDIDO[idx + 1]
        elif direction == 'prev' and idx > 0:
            pedido.estado = ESTADOS_PEDIDO[idx - 1]
        else:
            return redirect(url_for('orders_list'))
        pedido.actualizado = datetime.utcnow()
        sincronizar_contabilidad_pedido(pedido)
        db.session.commit()
        flash(f'Pedido #{pedido.id} ahora está "{pedido.estado}".')
        return redirect(url_for('orders_list') + f'#col-{pedido.estado}')

    @app.route('/pedidos/<int:oid>/borrar', methods=['POST'])
    def order_delete(oid):
        pedido = Pedido.query.get_or_404(oid)
        db.session.delete(pedido)
        db.session.commit()
        flash(f'Pedido #{oid} eliminado.')
        return redirect(url_for('orders_list'))

    # ---------------- CLIENTES ----------------
    @app.route('/clientes')
    def clients_list():
        clientes = Cliente.query.order_by(Cliente.nombre).all()
        return render_template('clients.html', clientes=clientes, active='clients')

    @app.route('/clientes/nuevo', methods=['GET', 'POST'])
    def client_new():
        if request.method == 'POST':
            c = Cliente(
                nombre=request.form['nombre'],
                telefono=request.form.get('telefono'),
                email=request.form.get('email'),
                direccion=request.form.get('direccion'),
                instagram=request.form.get('instagram'),
                notas=request.form.get('notas'),
            )
            db.session.add(c)
            db.session.commit()
            flash(f'Cliente "{c.nombre}" añadido.')
            return redirect(url_for('clients_list'))
        return render_template('client_form.html', cliente=None, active='clients')

    @app.route('/clientes/<int:cid>/editar', methods=['GET', 'POST'])
    def client_edit(cid):
        c = Cliente.query.get_or_404(cid)
        if request.method == 'POST':
            c.nombre = request.form['nombre']
            c.telefono = request.form.get('telefono')
            c.email = request.form.get('email')
            c.direccion = request.form.get('direccion')
            c.instagram = request.form.get('instagram')
            c.notas = request.form.get('notas')
            db.session.commit()
            flash(f'Cliente "{c.nombre}" actualizado.')
            return redirect(url_for('clients_list'))
        return render_template('client_form.html', cliente=c, active='clients')

    @app.route('/clientes/<int:cid>')
    def client_detail(cid):
        c = Cliente.query.get_or_404(cid)
        return render_template('client_detail.html', cliente=c, active='clients')

    @app.route('/clientes/<int:cid>/borrar', methods=['POST'])
    def client_delete(cid):
        c = Cliente.query.get_or_404(cid)
        db.session.delete(c)
        db.session.commit()
        flash(f'Cliente "{c.nombre}" eliminado.')
        return redirect(url_for('clients_list'))

    # ---------------- CONTABILIDAD ----------------
    @app.route('/contabilidad')
    def accounting_home():
        movimientos = Movimiento.query.order_by(Movimiento.fecha.desc(), Movimiento.creado.desc()).all()
        ingresos = sum(m.importe for m in movimientos if m.tipo == 'ingreso')
        gastos = sum(m.importe for m in movimientos if m.tipo == 'gasto')
        balance = ingresos - gastos
        return render_template('accounting.html', movimientos=movimientos, ingresos=ingresos,
                                gastos=gastos, balance=balance, active='accounting')

    @app.route('/contabilidad/nuevo', methods=['GET', 'POST'])
    def movement_new():
        pedidos = Pedido.query.order_by(Pedido.creado.desc()).all()
        if request.method == 'POST':
            m = Movimiento(
                tipo=request.form['tipo'],
                concepto=request.form['concepto'],
                importe=float(request.form['importe']),
                categoria=request.form.get('categoria'),
                pedido_id=request.form.get('pedido_id') or None,
                fecha=datetime.strptime(request.form['fecha'], '%Y-%m-%d') if request.form.get('fecha') else datetime.utcnow(),
            )
            db.session.add(m)
            db.session.commit()
            flash('Movimiento registrado.')
            return redirect(url_for('accounting_home'))
        return render_template('movement_form.html', pedidos=pedidos, tipos=TIPOS_MOVIMIENTO, active='accounting')

    @app.route('/contabilidad/<int:mid>/borrar', methods=['POST'])
    def movement_delete(mid):
        m = Movimiento.query.get_or_404(mid)
        db.session.delete(m)
        db.session.commit()
        flash('Movimiento eliminado.')
        return redirect(url_for('accounting_home'))


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
