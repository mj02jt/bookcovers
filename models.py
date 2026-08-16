from datetime import datetime
from extensions import db


class Producto(db.Model):
    """Stock de fundas de libros."""
    __tablename__ = 'productos'

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    modelo = db.Column(db.String(100))          # ej: "A5", "Tapa dura 20x14"
    color = db.Column(db.String(60))
    icono = db.Column(db.String(10), default='📖')
    foto = db.Column(db.String(250))  # ruta antigua (ya no se usa, se deja por compatibilidad)
    foto_data = db.Column(db.LargeBinary)     # foto guardada dentro de la base de datos
    foto_mimetype = db.Column(db.String(50))
    precio = db.Column(db.Float, default=0.0)
    cantidad = db.Column(db.Integer, default=0)
    stock_minimo = db.Column(db.Integer, default=3)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def stock_bajo(self):
        return self.cantidad <= self.stock_minimo


class Cliente(db.Model):
    __tablename__ = 'clientes'

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    telefono = db.Column(db.String(40))
    email = db.Column(db.String(150))
    direccion = db.Column(db.String(250))
    instagram = db.Column(db.String(100))
    notas = db.Column(db.Text)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

    pedidos = db.relationship('Pedido', backref='cliente', lazy=True)


ESTADOS_PEDIDO = ['pendiente', 'preparado', 'enviado', 'entregado', 'cancelado']


class Pedido(db.Model):
    __tablename__ = 'pedidos'

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=True)
    estado = db.Column(db.String(20), default='pendiente')
    total = db.Column(db.Float, default=0.0)
    notas = db.Column(db.Text)
    captura_imagen = db.Column(db.String(250))  # ruta antigua (ya no se usa, se deja por compatibilidad)
    captura_data = db.Column(db.LargeBinary)     # captura guardada dentro de la base de datos
    captura_mimetype = db.Column(db.String(50))
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship('PedidoItem', backref='pedido', lazy=True, cascade='all, delete-orphan')


class PedidoItem(db.Model):
    __tablename__ = 'pedido_items'

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedidos.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id'), nullable=True)
    descripcion = db.Column(db.String(250))  # texto libre si no coincide con un producto del stock
    cantidad = db.Column(db.Integer, default=1)
    precio_unitario = db.Column(db.Float, default=0.0)

    producto = db.relationship('Producto')


TIPOS_MOVIMIENTO = ['ingreso', 'gasto']


class Movimiento(db.Model):
    """Contabilidad: ingresos y gastos."""
    __tablename__ = 'movimientos'

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)  # ingreso / gasto
    concepto = db.Column(db.String(200), nullable=False)
    importe = db.Column(db.Float, nullable=False)
    categoria = db.Column(db.String(80))  # ej: material, envio, venta, marketing
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedidos.id'), nullable=True)
    fecha = db.Column(db.Date, default=datetime.utcnow)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

    pedido = db.relationship('Pedido')
