"""Conversión de modalidad: SQLite ficticia, permisos y Telegram bloqueado."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from Ventas import test_calendario as fixtures
from Ventas.views import put_venta, corregir_venta_administrativamente
from Ventas.serializers import VentaUpdateSerializer
from Recaudos.models import Recaudo, Visita_Blanco
from Tiendas.models import Tienda, MovimientoCaja


class EditarModalidadTests(TestCase):
    setUp = fixtures.CalendarioAPITests.setUp
    crear = fixtures.CalendarioAPITests.crear
    request = fixtures.CalendarioAPITests.request

    def editar(self, venta, usuario=None, ruta=None, **extra):
        datos = dict(fecha_venta=str(venta.fecha_venta), valor_venta=str(venta.valor_venta),
            interes=venta.interes, cuotas=venta.cuotas, plazo=venta.plazo, comentario='Prueba local')
        datos.update(extra)
        return self.request('put', put_venta, datos, user=usuario,
            pk=venta.pk, tienda_id=str(ruta or venta.tienda_id))

    def test_seis_conversiones_y_calendario_controlado_por_servidor(self):
        for origen in ('Diario','Semanal','Mensual'):
            for destino,cuotas,version,vencimiento in (
                    ('Diario',20,0,date(2026,11,1)),
                    ('Semanal',4,1,date(2026,11,5)),
                    ('Mensual',1,1,date(2026,11,8))):
                if origen == destino: continue
                with self.subTest(origen=origen,destino=destino):
                    venta=self.crear(plazo=origen,calendario_version=0)
                    respuesta=self.editar(venta,plazo=destino,cuotas=cuotas,
                        calendario_version=99,fecha_vencimiento='2099-01-01',saldo_actual='1')
                    self.assertEqual(respuesta.status_code,200,respuesta.data)
                    venta.refresh_from_db()
                    self.assertEqual(venta.plazo,destino)
                    self.assertEqual(venta.calendario_version,version)
                    self.assertEqual(venta.fecha_vencimiento,vencimiento)
                    self.assertEqual(venta.saldo_actual,1200000)
                    self.assertEqual(venta.valor_cuota(),Decimal(1200000)/cuotas)
        self.ruta.refresh_from_db();self.assertEqual(self.ruta.caja_inicial,10000000)
        self.assertEqual(Recaudo.objects.count(),0)
        self.assertEqual(MovimientoCaja.objects.count(),0)

    def test_conversion_mensual_fin_de_mes_y_bisiesto(self):
        for fecha,vence in ((date(2026,1,31),date(2026,2,28)),(date(2024,1,31),date(2024,2,29))):
            venta=self.crear(fecha_venta=fecha,plazo='Diario',cuotas=20,calendario_version=0)
            self.assertEqual(self.editar(venta,plazo='Mensual',cuotas=1).status_code,200)
            venta.refresh_from_db();self.assertEqual(venta.fecha_vencimiento,vence)

    def test_misma_modalidad_no_migra_calendario_historico(self):
        venta=self.crear(fecha_venta=date(2026,1,31),plazo='Mensual',cuotas=1,calendario_version=0)
        self.assertEqual(self.editar(venta).status_code,200)
        venta.refresh_from_db()
        self.assertEqual(venta.calendario_version,0)
        self.assertEqual(venta.fecha_vencimiento,date(2026,3,6))

    def test_cliente_anterior_sin_campo_plazo_conserva_modalidad(self):
        venta=self.crear()
        respuesta=self.request('put',put_venta,{'fecha_venta':str(venta.fecha_venta),
            'valor_venta':1000000,'interes':20,'cuotas':4},pk=venta.pk,tienda_id=str(self.ruta.pk))
        self.assertEqual(respuesta.status_code,200,respuesta.data)
        venta.refresh_from_db();self.assertEqual(venta.plazo,'Semanal')
        self.assertEqual(venta.calendario_version,1)

    def test_abono_visita_y_renovacion_impiden_conversion_sin_escrituras(self):
        for monto,renovacion in ((100,False),(0,False),(100,True)):
            venta=self.crear()
            Recaudo.objects.create(venta=venta,tienda=self.ruta,fecha_recaudo=date(2026,10,9),
                valor_recaudo=monto,es_renovacion=renovacion)
            antes=(venta.plazo,venta.cuotas,venta.saldo_actual,venta.fecha_vencimiento)
            respuesta=self.editar(venta,plazo='Mensual',cuotas=1)
            self.assertEqual(respuesta.status_code,409,respuesta.data)
            venta.refresh_from_db()
            self.assertEqual((venta.plazo,venta.cuotas,venta.saldo_actual,venta.fecha_vencimiento),antes)

    def test_abono_concurrente_se_detecta_dentro_del_bloqueo(self):
        venta=self.crear()
        original=Tienda.objects.select_for_update
        class Bloqueo:
            def get(inner,**kwargs):
                Recaudo.objects.create(venta=venta,tienda=self.ruta,
                    fecha_recaudo=date(2026,10,9),valor_recaudo=0)
                return original().get(**kwargs)
        with patch('Ventas.views.Tienda.objects.select_for_update',return_value=Bloqueo()):
            respuesta=self.editar(venta,plazo='Mensual',cuotas=1)
        self.assertEqual(respuesta.status_code,409)
        venta.refresh_from_db();self.assertEqual(venta.plazo,'Semanal')

    def test_api_correccion_no_ignora_intento_de_cambiar_modalidad(self):
        venta=self.crear()
        Recaudo.objects.create(venta=venta,tienda=self.ruta,fecha_recaudo=date(2026,10,9),valor_recaudo=100)
        respuesta=self.request('patch',corregir_venta_administrativamente,
            {'fecha_venta':str(venta.fecha_venta),'cuotas':1,'motivo':'Intento ficticio','plazo':'Mensual'},
            pk=venta.pk,tienda_id=str(self.ruta.pk))
        self.assertEqual(respuesta.status_code,400)
        self.assertIn('plazo',respuesta.data)
        venta.refresh_from_db();self.assertEqual(venta.plazo,'Semanal')

    def test_trabajador_y_otro_administrador_no_pueden_editar(self):
        venta=self.crear()
        trabajador=User.objects.create_user('trabajador-ficticio')
        from Trabajadores.models import Perfil
        Perfil.objects.create(trabajador=trabajador,tienda=self.ruta,identificacion='w',telefono='000')
        for usuario in (trabajador,self.other):
            self.assertEqual(self.editar(venta,usuario=usuario,plazo='Mensual',cuotas=1).status_code,403)
        venta.refresh_from_db();self.assertEqual(venta.plazo,'Semanal')

    def test_no_se_puede_usar_una_ruta_distinta_a_la_del_credito(self):
        venta=self.crear()
        otra=Tienda.objects.create(nombre='Otra ruta propia',administrador=self.admin)
        self.assertEqual(self.editar(venta,ruta=otra.pk,plazo='Mensual',cuotas=1).status_code,404)

    def test_modalidad_cuotas_y_fecha_invalidas_no_modifican_nada(self):
        venta=self.crear()
        for datos in ({'plazo':'Quincenal'},{'plazo':''},{'plazo':None},{'plazo':[]},{'plazo':{}},{'plazo':True},
                {'cuotas':0},{'cuotas':121},{'cuotas':1.5},{'fecha_venta':'2099-01-01'}):
            with self.subTest(datos=datos):
                self.assertEqual(self.editar(venta,**datos).status_code,400)
        venta.refresh_from_db();self.assertEqual(venta.plazo,'Semanal')

    def test_cerrados_y_renovados_no_pueden_cambiar_modalidad(self):
        for estado in ('Pagado','Perdida'):
            venta=self.crear(estado_venta=estado)
            self.assertEqual(self.editar(venta,plazo='Mensual',cuotas=1).status_code,400)
        origen=self.crear()
        nueva=self.crear(origen_renovacion=origen)
        for venta in (origen,nueva):
            self.assertEqual(self.editar(venta,plazo='Mensual',cuotas=1).status_code,400)

    def test_serializer_no_deja_saltar_bloqueo_de_movimientos(self):
        venta=self.crear()
        Recaudo.objects.create(venta=venta,tienda=self.ruta,fecha_recaudo=date(2026,10,9),valor_recaudo=0)
        serializer=VentaUpdateSerializer(venta,data={'plazo':'Mensual'},partial=True)
        self.assertFalse(serializer.is_valid());self.assertIn('plazo',serializer.errors)

    def test_liquidar_y_meta_respetan_modalidad_convertida(self):
        from Ventas.views import list_ventas_a_liquidar
        venta=self.crear(plazo='Diario',cuotas=20,calendario_version=0,estado_venta='Atrasado')
        respuesta=self.editar(venta,plazo='Mensual',cuotas=1)
        self.assertEqual(respuesta.status_code,200,respuesta.data)
        venta.refresh_from_db();self.assertEqual(venta.estado_venta,'Vigente')
        respuesta=self.request('get',list_ventas_a_liquidar,{'resumen':'1'},
            date='2026-10-15',tienda_id=str(self.ruta.pk))
        self.assertEqual(respuesta.status_code,200)
        self.assertEqual(respuesta.data['creditos'],[])
        self.assertEqual(Decimal(respuesta.data['resumen_cobranza']['total_a_cobrar']),0)
