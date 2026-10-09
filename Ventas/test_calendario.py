from datetime import date, datetime, timezone as utc
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from Clientes.models import Cliente
from Clientes.views import _calcular_score_desde_datos
from Recaudos.models import Recaudo, Visita_Blanco
from Recaudos.views import post_recaudo, post_recaudo_no_pay, delete_recaudo
from Tiendas.models import Tienda, MovimientoCaja
from Ventas.calendario import calcular_calendario, fechas_cuotas, calendario_venta, riesgo_calendario, datos_calendario_score
from Ventas.models import Venta
from Ventas.serializers import VentaSerializer, VentaUpdateSerializer, VentaListaSerializer, VentaDetailSerializer
from Ventas.views import list_ventas_a_liquidar, _anotar_ventas_lista, post_venta, renovar_venta, corregir_venta_administrativamente


class CalendarioPuroTests(SimpleTestCase):
    def test_telegram_no_confunde_antiguedad_con_mora(self):
        from Clientes.telegram_resumen import estado_credito_visible
        datos = {'estado_venta': 'Vigente', 'frecuencia': 'Semanal',
                 'dias_sin_abono': 40, 'cuotas_atrasadas': '0',
                 'calendario_pago': self.perfil(abonado=600000)}
        self.assertEqual(estado_credito_visible(datos), '🟢 Al día')
        datos['calendario_pago'] = self.perfil(consulta=date(2026,10,16))
        self.assertEqual(estado_credito_visible(datos), '🟠 Con atraso')

    def perfil(self, **extra):
        data = dict(fecha_venta=date(2026, 10, 8), cuotas=4, plazo='Semanal',
            total='1200000', abonado=0, consulta=date(2026, 10, 15), version=1)
        data.update(extra)
        return calcular_calendario(**data)

    def test_semanal_cuatro_fechas_exactas(self):
        self.assertEqual(list(fechas_cuotas(date(2026,10,8),4,'Semanal',1)),
            [date(2026,10,15),date(2026,10,22),date(2026,10,29),date(2026,11,5)])

    def test_mensual_anclado_mes_corto_y_bisiesto(self):
        self.assertEqual(list(fechas_cuotas(date(2026,1,31),3,'Mensual',1)),[date(2026,2,28),date(2026,3,31),date(2026,4,30)])
        self.assertEqual(list(fechas_cuotas(date(2028,1,31),1,'Mensual',1)),[date(2028,2,29)])

    def test_mensual_historico_sigue_siendo_treinta_dias(self):
        self.assertEqual(list(fechas_cuotas(date(2026,10,8),1,'Mensual',0)),[date(2026,11,7)])

    def test_hoy_exigible_no_es_mora_hasta_manana(self):
        today = self.perfil()
        self.assertEqual(today['importe_pendiente_hoy'],'300000.00')
        self.assertEqual(today['importe_vencido'],'0.00')
        self.assertEqual(today['estado'],'Vigente')
        tomorrow = self.perfil(consulta=date(2026,10,16))
        self.assertEqual(tomorrow['estado'],'Atrasado')
        self.assertEqual(tomorrow['cuotas_atrasadas'],'1.00')

    def test_antes_de_cuota_no_visita_obligatoria_ni_falla(self):
        p = self.perfil(consulta=date(2026,10,9))
        self.assertFalse(p['visible_liquidar'])
        self.assertFalse(p['permite_falla'])
        self.assertFalse(p['pendiente_operativo'])

    def test_abono_parcial_anticipado_sigue_visible_sin_mora(self):
        p = self.perfil(consulta=date(2026,10,9),abonado=100000)
        self.assertTrue(p['visible_liquidar'])
        self.assertTrue(p['cobro_voluntario'])
        self.assertEqual(p['cuotas_atrasadas'],'0.00')

    def test_doble_anticipo_no_riesgo_por_paso_del_tiempo(self):
        p = self.perfil(consulta=date(2026,10,24),abonado=600000)
        self.assertEqual(p['importe_vencido'],'0.00')
        self.assertFalse(riesgo_calendario(p,'Semanal',16)['en_mora'])

    def test_cuota_cubierta_hoy_sigue_visible_para_cobro_voluntario(self):
        p = self.perfil(abonado=300000,gestionado=True)
        self.assertTrue(p['visible_liquidar'])
        self.assertTrue(p['cobro_voluntario'])
        self.assertFalse(p['permite_falla'])

    def test_registrar_falla_no_borra_deuda(self):
        p = self.perfil(gestionado=True)
        self.assertFalse(p['pendiente_operativo'])
        self.assertEqual(p['importe_pendiente_hoy'],'300000.00')

    def test_cuotas_atrasadas_no_exceden_contrato(self):
        p = self.perfil(consulta=date(2028,1,1))
        self.assertEqual(p['cuotas_atrasadas'],'4.00')

    def test_saldo_cero_o_perdida_no_entran_liquidar(self):
        for extra in ({'abonado':1200000},{'estado':'Perdida'}):
            p = self.perfil(**extra)
            self.assertFalse(p['visible_liquidar'])
            self.assertFalse(p['permite_falla'])

    def test_residuo_centavos_no_crea_mora_ficticia(self):
        p = self.perfil(total='100.01',cuotas=3,abonado='33.34')
        self.assertEqual(p['importe_pendiente_hoy'],'0.00')
        p = self.perfil(total='100.01',cuotas=3,abonado='66.67',consulta=date(2026,10,22))
        self.assertEqual(p['importe_pendiente_hoy'],'0.00')

    def test_legacy_mas_de_120_cuotas_se_puede_consultar(self):
        self.assertEqual(len(list(fechas_cuotas(date(2026,1,1),121,'Semanal',0))),121)
        with self.assertRaises(ValueError):
            list(fechas_cuotas(date(2026,1,1),121,'Semanal',1))


class CalendarioAPITests(TestCase):
    def setUp(self):
        self.red = patch('requests.sessions.Session.request',side_effect=AssertionError('Red externa bloqueada'))
        self.red.start(); self.addCleanup(self.red.stop)
        self.clock = patch('Tiendas.fecha_operativa.timezone.now',return_value=datetime(2026,10,15,16,tzinfo=utc.utc))
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.admin = User.objects.create_user('admin-calendario',is_staff=True)
        self.other = User.objects.create_user('admin-ajeno',is_staff=True)
        self.ruta = Tienda.objects.create(nombre='Calendario local',administrador=self.admin,caja_inicial=10000000,zona_horaria='America/Santiago')
        self.cliente = Cliente.objects.create(tienda=self.ruta,identificacion='calendar-local',nombres='Cliente',apellidos='Prueba',telefono_principal='000',direccion='Local')
        self.factory = APIRequestFactory()

    def crear(self, **extra):
        data = dict(tienda=self.ruta,cliente=self.cliente,fecha_venta=date(2026,10,8),valor_venta=1000000,
            interes=20,cuotas=4,plazo='Semanal',saldo_actual=1200000,calendario_version=1,fecha_vencimiento=date(2026,11,5))
        data.update(extra)
        return Venta.objects.create(**data)

    def request(self, method, view, data=None, user=None, **kwargs):
        req = getattr(self.factory,method)('/solo-pruebas/',data or {},format='json')
        force_authenticate(req,user=user or self.admin)
        return view(req,**kwargs)

    def lista(self, fecha='2026-10-15', user=None):
        return self.request('get',list_ventas_a_liquidar,date=fecha,tienda_id=str(self.ruta.pk),user=user)

    def abonar(self, venta, valor, fecha='2026-10-15'):
        return self.request('post',post_recaudo,{'venta':venta.pk,'valor_recaudo':valor,'fecha_recaudo':fecha},tienda_id=str(self.ruta.pk))

    def falla(self, venta, fecha='2026-10-15'):
        return self.request('post',post_recaudo_no_pay,{'venta':venta.pk,'fecha_recaudo':fecha,'visita_blanco':{'tipo_falla':'Cliente no Tiene Dinero','comentario':'Prueba'}},tienda_id=str(self.ruta.pk))

    def test_creacion_marca_servidor_y_fecha_sin_cuatro_dias(self):
        for plazo, cuotas, fecha in [('Semanal',4,date(2026,11,5)),('Mensual',1,date(2026,11,8)),('Diario',20,date(2026,11,1))]:
            serializer = VentaSerializer(data={'cliente':self.cliente.pk,'tienda':self.ruta.pk,'fecha_venta':'2026-10-08','valor_venta':'1000000','interes':20,'cuotas':cuotas,'plazo':plazo,'calendario_version':99,'fecha_vencimiento':'2099-01-01'},context={'tienda':self.ruta})
            self.assertTrue(serializer.is_valid(),serializer.errors)
            venta = serializer.save()
            self.assertEqual(venta.fecha_vencimiento,fecha)
            self.assertEqual(venta.calendario_version,0 if plazo=='Diario' else 1)

    def test_editar_historico_no_actualiza_version_ni_condiciones(self):
        venta = self.crear(plazo='Mensual',cuotas=1,calendario_version=0,fecha_vencimiento=date(2026,11,11))
        serializer = VentaUpdateSerializer(venta,data={'fecha_venta':'2026-10-08','valor_venta':'1000000','interes':20,'cuotas':1,'plazo':'Mensual','calendario_version':1},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors)
        serializer.save(); venta.refresh_from_db()
        self.assertEqual(venta.calendario_version,0)
        self.assertEqual(venta.fecha_vencimiento,date(2026,11,11))

    def test_renovacion_es_nuevo_calendario_sin_reescribir_anterior(self):
        venta = self.crear(plazo='Mensual',cuotas=1,calendario_version=0,fecha_vencimiento=date(2026,11,11))
        response = self.request('post',renovar_venta,{'fecha_venta':'2026-10-15','interes':20,'cuotas':1},pk=venta.pk,tienda_id=str(self.ruta.pk))
        self.assertEqual(response.status_code,201,response.data)
        nueva = Venta.objects.get(origen_renovacion=venta)
        self.assertEqual(nueva.calendario_version,1)
        self.assertEqual(nueva.fecha_vencimiento,date(2026,11,15))
        venta.refresh_from_db()
        self.assertEqual(venta.calendario_version,0)
        self.assertEqual(venta.fecha_vencimiento,date(2026,11,11))

    def test_renovacion_rechaza_121_cuotas_sin_mutaciones(self):
        venta = self.crear()
        response = self.request('post',renovar_venta,{'fecha_venta':'2026-10-15','interes':20,'cuotas':121},pk=venta.pk,tienda_id=str(self.ruta.pk))
        self.assertEqual(response.status_code,400)
        self.assertEqual(Venta.objects.count(),1)
        self.assertFalse(Recaudo.objects.exists())

    def test_correccion_administrativa_respeta_version(self):
        venta = self.crear(plazo='Mensual',cuotas=1,fecha_vencimiento=date(2026,11,8))
        self.abonar(venta,100000)
        response = self.request('patch',corregir_venta_administrativamente,{'fecha_venta':'2026-10-01','cuotas':2,'motivo':'Corrección de prueba local'},pk=venta.pk,tienda_id=str(self.ruta.pk))
        self.assertEqual(response.status_code,200,response.data)
        venta.refresh_from_db()
        self.assertEqual(venta.calendario_version,1)
        self.assertEqual(venta.fecha_vencimiento,date(2026,12,1))
        self.assertEqual(venta.saldo_actual,Decimal('1100000'))

    def test_liquidar_antes_no_aparece_y_anticipo_si(self):
        venta = self.crear()
        self.assertEqual(self.lista('2026-10-14').data,[])
        self.abonar(venta,600000,'2026-10-14')
        lista = self.lista('2026-10-14').data
        self.assertEqual(len(lista),1)
        self.assertTrue(lista[0]['calendario_pago']['cobro_voluntario'])

    def test_dos_abonos_parciales_completan_una_cuota(self):
        venta = self.crear()
        self.assertEqual(self.abonar(venta,150000).status_code,200)
        self.assertEqual(self.abonar(venta,150000).status_code,200)
        venta.refresh_from_db()
        self.assertEqual(venta.estado_venta,'Vigente')
        self.assertEqual(venta.dias_atrasados(),Decimal('0.00'))
        self.assertEqual(calendario_venta(venta)['importe_pendiente_hoy'],'0.00')

    def test_pago_anticipado_pequeno_no_marca_atraso(self):
        venta = self.crear()
        self.assertEqual(self.abonar(venta,100000,'2026-10-14').status_code,200)
        venta.refresh_from_db()
        # Hoy todavía puede completar la primera cuota; no hay jornada vencida.
        self.assertEqual(venta.estado_venta,'Vigente')

    def test_falla_antes_de_cuota_rechazada_sin_registros(self):
        venta = self.crear()
        self.assertEqual(self.falla(venta,'2026-10-14').status_code,409)
        self.assertEqual(Visita_Blanco.objects.count(),0)
        self.assertEqual(Recaudo.objects.count(),0)
        self.assertEqual(MovimientoCaja.objects.count(),0)

    def test_falla_cubierta_rechazada(self):
        venta = self.crear()
        self.abonar(venta,600000)
        self.assertEqual(self.falla(venta).status_code,409)

    def test_falla_hoy_registra_visita_sin_atraso_ni_caja_y_no_duplica(self):
        venta = self.crear()
        self.assertEqual(self.falla(venta).status_code,200)
        self.assertEqual(self.falla(venta).status_code,409)
        venta.refresh_from_db()
        self.assertEqual(venta.estado_venta,'Vigente')
        self.assertEqual(venta.saldo_actual,Decimal('1200000'))
        self.assertEqual(MovimientoCaja.objects.count(),0)
        self.assertEqual(Recaudo.objects.count(),1)

    def test_api_no_escribe_estado_al_consultar(self):
        venta = self.crear(estado_venta='Vencido')
        lista = self.lista().data
        self.assertEqual(lista[0]['estado_venta'],'Vigente')
        venta.refresh_from_db(); self.assertEqual(venta.estado_venta,'Vencido')

    def test_abono_futuro_no_cubre_calendario_pasado(self):
        venta = self.crear()
        self.abonar(venta,600000)
        self.assertEqual(self.lista('2026-10-14').data,[])

    def test_fecha_futura_invalida_y_ruta_ajena(self):
        self.assertEqual(self.lista('2026-10-16').status_code,400)
        self.assertEqual(self.lista('incorrecta').status_code,400)
        self.assertEqual(self.lista(user=self.other).status_code,403)

    def previa(self, fecha='2026-10-16', user=None):
        return self.request('get', list_ventas_a_liquidar, {'vista': 'previa'},
                            date=fecha, tienda_id=str(self.ruta.pk), user=user)

    def test_previa_futura_calcula_sin_escribir(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        venta = self.crear(fecha_venta=date(2026,10,9))
        self.assertEqual(self.lista().data, [])
        with CaptureQueriesContext(connection) as queries:
            response = self.previa()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['vista_previa'])
        self.assertEqual(response.data['fecha_operativa'], '2026-10-15')
        self.assertEqual(response.data['creditos'][0]['calendario_pago']['importe_pendiente_hoy'], '300000.00')
        self.assertEqual(response.data['creditos'][0]['calendario_pago']['importe_vencido'], '0.00')
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))
        venta.refresh_from_db(); self.ruta.refresh_from_db()
        self.assertEqual(venta.estado_venta, 'Vigente')
        self.assertEqual(venta.saldo_actual, Decimal('1200000'))
        self.assertEqual(self.ruta.caja_inicial, Decimal('10000000'))
        self.assertFalse(Recaudo.objects.exists())
        self.assertFalse(Visita_Blanco.objects.exists())
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_previa_no_exige_cuotas_cubiertas_y_respeta_frecuencia(self):
        pendiente = self.crear(fecha_venta=date(2026,10,9))
        adelantado = self.crear()
        self.abonar(adelantado, 600000)
        mensual = self.crear(plazo='Mensual', cuotas=1, fecha_vencimiento=date(2026,11,8))
        response = self.previa()
        datos = {c['id']: c for c in response.data['creditos']}
        self.assertIn(pendiente.pk, datos)
        self.assertNotIn(mensual.pk, datos)
        self.assertEqual(datos[adelantado.pk]['calendario_pago']['importe_pendiente_hoy'], '0.00')
        self.assertTrue(datos[adelantado.pk]['calendario_pago']['cobro_voluntario'])
        self.assertIn(mensual.pk, [c['id'] for c in self.previa('2026-11-08').data['creditos']])

    def test_previa_vacia_y_acceso_restringido(self):
        self.assertEqual(self.previa().data['creditos'], [])
        self.assertEqual(self.previa(user=self.other).status_code, 403)
        self.admin.is_staff = False
        self.assertEqual(self.previa().status_code, 403)
        self.assertEqual(self.lista().status_code, 200)

    def test_previa_no_autoriza_abonos_ni_fallas_futuros(self):
        for plazo in ('Diario', 'Semanal', 'Mensual'):
            venta = self.crear(plazo=plazo)
            self.assertEqual(self.abonar(venta, 100000, '2026-10-16').status_code, 400)
            self.assertEqual(self.falla(venta, '2026-10-16').status_code, 400)
            venta.refresh_from_db()
            self.assertEqual(venta.saldo_actual, Decimal('1200000'))
        self.assertFalse(Recaudo.objects.exists())
        self.assertFalse(Visita_Blanco.objects.exists())
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_eliminar_pago_reabre_pagado_y_calendario(self):
        venta = self.crear()
        response = self.abonar(venta,1200000)
        self.assertEqual(response.status_code,200)
        venta.refresh_from_db(); self.assertEqual(venta.estado_venta,'Pagado')
        recaudo = Recaudo.objects.get(venta=venta)
        response = self.request('delete',delete_recaudo,pk=recaudo.pk)
        self.assertEqual(response.status_code,200)
        venta.refresh_from_db(); self.assertEqual(venta.estado_venta,'Vigente')
        self.assertEqual(calendario_venta(venta)['importe_pendiente_hoy'],'300000.00')

    def test_diario_sigue_en_lista_y_sin_calendario_nuevo(self):
        venta = self.crear(plazo='Diario',cuotas=20,calendario_version=0)
        lista = self.lista('2026-10-09').data
        self.assertEqual(len(lista),1)
        self.assertIsNone(lista[0]['calendario_pago'])

    def test_serializacion_lista_sin_n_mas_uno(self):
        for _ in range(12): self.crear()
        queryset = _anotar_ventas_lista(Venta.objects.all(),date(2026,10,15))
        with self.assertNumQueries(1):
            self.assertEqual(len(VentaListaSerializer(queryset,many=True).data),12)

    def test_score_no_penaliza_fallas_fuera_de_fecha(self):
        venta = self.crear()
        datos = {'id':venta.pk,'fecha_venta':venta.fecha_venta,'valor_venta':venta.valor_venta,'interes':20,'cuotas':4,'plazo':'Semanal','estado_venta':'Vigente','saldo_actual':venta.saldo_actual,'origen_renovacion_id':None,'calendario_version':1,'fecha_vencimiento':venta.fecha_vencimiento}
        fallas = [{'id':n,'venta_id':venta.pk,'fecha_recaudo':date(2026,10,8+n),'valor_recaudo':0,'visita_blanco_id':n,'es_renovacion':False} for n in range(1,6)]
        base = _calcular_score_desde_datos(self.cliente.pk,self.ruta,[datos],[],set(),hoy=date(2026,10,14))
        con_fallas = _calcular_score_desde_datos(self.cliente.pk,self.ruta,[datos],fallas,set(),hoy=date(2026,10,14))
        self.assertEqual(base['score'],con_fallas['score'])
        self.assertEqual(con_fallas['detalle']['racha_fallas'],0)

    def test_score_una_obligacion_no_dos_recibos_y_no_borra_evidencia(self):
        venta = self.crear()
        datos = {'id':venta.pk,'fecha_venta':venta.fecha_venta,'valor_venta':venta.valor_venta,'interes':20,'cuotas':4,'plazo':'Semanal','estado_venta':'Vigente','saldo_actual':900000,'calendario_version':1,'fecha_vencimiento':venta.fecha_vencimiento}
        registros = [{'id':n,'venta_id':venta.pk,'fecha_recaudo':date(2026,10,15),'valor_recaudo':150000,'visita_blanco_id':None,'es_renovacion':False} for n in (1,2)]
        perfil, observaciones = datos_calendario_score(datos,registros,date(2026,10,16))
        self.assertEqual(len(observaciones),1)
        self.assertIsNone(observaciones[0]['visita_blanco_id'])
        self.assertEqual(perfil['importe_vencido'],'0.00')
        self.assertEqual(len(registros),2)
