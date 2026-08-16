import os
import uuid
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
from sqlalchemy import text

from config import Config
from extensions import db
from models import Producto, Cliente, Pedido, PedidoItem, Movimiento, ESTADOS_PEDIDO, TIPOS_MOVIMIENTO
import ai_scan

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def save_uploaded_photo(file, upload_folder):
    """Guarda una imagen subida y devuelve la ruta relativa (uploads/xxx) o None."""
    if not file or file.filename == '' or not allowed_file(file.filename):
        return None
    fname = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    file.save(os.path.join(upload_folder, fname))
    return f"uploads/{fname}"


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


def run_migrations(app):
    """Pequeñas migraciones para bases de datos ya creadas antes de añadir columnas nuevas."""
    with app.app_context():
        try:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE productos ADD COLUMN IF NOT EXISTS foto VARCHAR(250)"))
        except Exception:
            pass  # ya existe la columna, o la base es sqlite y ya se creó con create_all


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

    # ---------------- STOCK ----------------
    @app.route('/stock')
    def stock_list():
        productos = Producto.query.order_by(Producto.nombre).all()
        return render_template('stock.html', productos=productos, active='stock')

    @app.route('/stock/nuevo', methods=['GET', 'POST'])
    def stock_new():
        if request.method == 'POST':
            foto = save_uploaded_photo(request.files.get('foto'), app.config['UPLOAD_FOLDER'])
            p = Producto(
                nombre=request.form['nombre'],
                modelo=request.form.get('modelo'),
                color=request.form.get('color'),
                icono=request.form.get('icono') or '📖',
                foto=foto,
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
            nueva_foto = save_uploaded_photo(request.files.get('foto'), app.config['UPLOAD_FOLDER'])
            if nueva_foto:
                p.foto = nueva_foto
            elif request.form.get('quitar_foto'):
                p.foto = None
            p.precio = float(request.form.get('precio') or 0)
            p.cantidad = int(request.form.get('cantidad') or 0)
            p.stock_minimo = int(request.form.get('stock_minimo') or 3)
            db.session.commit()
            flash(f'"{p.nombre}" actualizado.')
            return redirect(url_for('stock_list'))
        return render_template('stock_form.html', producto=p, active='stock')

    @app.route('/stock/<int:pid>/borrar', methods=['POST'])
    def stock_delete(pid):
        p = Producto.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        flash(f'"{p.nombre}" eliminado del stock.')
        return redirect(url_for('stock_list'))

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
        return render_template(
            'scan_review.html', active='scan',
            imagen=rel_path, items=detected_items, error=error,
            cliente_detectado=cliente_detectado,
            productos=productos, clientes=clientes,
        )

    @app.route('/escanear/crear-pedido', methods=['POST'])
    def scan_create_order():
        imagen = request.form.get('imagen')

        cliente_id = resolver_cliente_id(request.form)
        pedido = Pedido(cliente_id=cliente_id, captura_imagen=imagen, estado='pendiente')
        db.session.add(pedido)

        pedido.total = procesar_items_pedido(pedido, request.form)
        db.session.commit()
        flash(f'Pedido #{pedido.id} creado.')
        return redirect(url_for('order_detail', oid=pedido.id))

    # ---------------- PEDIDOS (CRM) ----------------
    @app.route('/pedidos')
    def orders_list():
        estado_filtro = request.args.get('estado')
        q = Pedido.query
        if estado_filtro:
            q = q.filter_by(estado=estado_filtro)
        pedidos = q.order_by(Pedido.creado.desc()).all()
        return render_template('orders.html', pedidos=pedidos, estados=ESTADOS_PEDIDO,
                                estado_filtro=estado_filtro, active='orders')

    @app.route('/pedidos/nuevo', methods=['GET', 'POST'])
    def order_new():
        productos = Producto.query.order_by(Producto.nombre).all()
        clientes = Cliente.query.order_by(Cliente.nombre).all()
        if request.method == 'POST':
            cliente_id = resolver_cliente_id(request.form)
            pedido = Pedido(cliente_id=cliente_id, estado='pendiente', notas=request.form.get('notas'))
            db.session.add(pedido)

            pedido.total = procesar_items_pedido(pedido, request.form)
            db.session.commit()
            flash(f'Pedido #{pedido.id} creado.')
            return redirect(url_for('order_detail', oid=pedido.id))
        return render_template('order_form.html', productos=productos, clientes=clientes, active='orders')

    @app.route('/pedidos/<int:oid>')
    def order_detail(oid):
        pedido = Pedido.query.get_or_404(oid)
        return render_template('order_detail.html', pedido=pedido, estados=ESTADOS_PEDIDO, active='orders')

    @app.route('/pedidos/<int:oid>/estado', methods=['POST'])
    def order_update_status(oid):
        pedido = Pedido.query.get_or_404(oid)
        nuevo_estado = request.form.get('estado')
        if nuevo_estado in ESTADOS_PEDIDO:
            pedido.estado = nuevo_estado
            pedido.actualizado = datetime.utcnow()
            db.session.commit()
            flash(f'Pedido #{pedido.id} ahora está "{nuevo_estado}".')
        return redirect(url_for('order_detail', oid=oid))

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
