"""Centro de alertas: datos ficticios, SQLite y notificaciones prohibidas."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from Tiendas import test_suscripciones as fixtures
from Tiendas.models import AlertaOperativa, Cierre_Caja, MovimientoCaja, Tienda, Tienda_Administrador
from Tiendas.alertas_operativas import revisar_riesgo_venta, revisar_cierres_ausentes
from Tiendas.centro_alertas import controles_caja
from Ventas.models import Venta
from Recaudos.models import Recaudo


@override_settings(ROOT_URLCONF='Tiendas.test_suscripciones', TELEGRAM_ALERTAS_USERNAME='admin-pruebas')
class AlertasTests(TestCase):
    def setUp(self):
        fixtures.SuscripcionesTests.setUp(self)
        self.api.force_authenticate(self.admin)

    def alerta(self, **extra):
        fields = dict(tipo='RIESGO_CARTERA', severidad='critica', estado='nueva',
            clave_dedupe=f'test:{AlertaOperativa.objects.count()}', titulo='Alerta ficticia',
            detalle='Detalle ficticio', tienda_id_ref=self.ruta.id,
            cliente_id_ref=self.cliente.id, venta_id_ref=self.venta.id)
        fields.update(extra)
        return AlertaOperativa.objects.create(**fields)

    def pendientes(self, **params):
        return self.api.get('/tiendas/alertas/pendientes/', params)

    def test_pagado_y_perdida_resuelven_sin_reactivar_ni_cambiar_saldos(self):
        for estado in ('Pagado', 'Perdida'):
            with self.subTest(estado=estado):
                self.venta.estado_venta = estado
                self.venta.saldo_actual = 0 if estado == 'Pagado' else 1100
                self.venta.save()
                alerta = self.alerta(datos={'revisiones': [{'nota': 'Nota anterior'}]})
                result = revisar_riesgo_venta(self.venta, notificar=False)
                alerta.refresh_from_db(); self.venta.refresh_from_db()
                self.assertEqual(alerta.estado, 'resuelta')
                self.assertEqual(alerta.datos['resolucion']['motivo'], 'Crédito cerrado')
                self.assertEqual(alerta.datos['revisiones'][0]['nota'], 'Nota anterior')
                self.assertEqual(result['nivel'], 0)
                self.assertFalse(revisar_riesgo_venta(self.venta, notificar=False)['creada'])
                self.assertEqual(self.venta.estado_venta, estado)
        self.ruta.refresh_from_db(); self.assertEqual(self.ruta.caja_inicial, 10000)
        self.assertEqual(Recaudo.objects.count(), 1)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_cerrado_sin_alerta_no_crea_ninguna_para_todas_las_frecuencias(self):
        for plazo in ('Diario', 'Semanal', 'Mensual'):
            for estado in ('Pagado', 'Perdida'):
                self.venta.plazo = plazo; self.venta.estado_venta = estado; self.venta.save()
                revisar_riesgo_venta(self.venta, notificar=False)
        self.assertFalse(AlertaOperativa.objects.exists())

    def test_monitor_conserva_notas_al_actualizar_credito_activo(self):
        alerta = self.alerta(datos={'revisiones': [{'nota': 'Llamar al cliente'}]})
        revisar_riesgo_venta(self.venta, notificar=False)
        alerta.refresh_from_db()
        self.assertEqual(alerta.datos['revisiones'][0]['nota'], 'Llamar al cliente')

    def test_cierre_posterior_resuelve_omision_en_la_operacion_autorizada(self):
        alerta = self.alerta(tipo='CIERRE_AUSENTE', venta_id_ref=None,
            datos={'fecha': '2026-10-09'})
        response = self.api.post(f'/tiendas/cierre/post/2026-10-09/t/{self.ruta.id}/')
        self.assertEqual(response.status_code, 200, response.data)
        alerta.refresh_from_db(); self.assertEqual(alerta.estado, 'resuelta')
        self.assertTrue(alerta.datos['resolucion']['automatica'])
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_mantenimiento_reconcilia_omisiones_antiguas_solo_en_alcance(self):
        vieja = self.alerta(tipo='CIERRE_AUSENTE', datos={'fecha': '2026-10-01'})
        ajena = Tienda.objects.create(nombre='Fuera de alcance', administrador=self.root)
        otra = self.alerta(tipo='CIERRE_AUSENTE', tienda_id_ref=ajena.pk,
            datos={'fecha': '2026-10-01'})
        for ruta in (self.ruta, ajena):
            Cierre_Caja.objects.create(tienda=ruta, fecha_cierre=date(2026,10,1), valor=10000)
        revisar_cierres_ausentes(date(2026,10,9), rutas=Tienda.objects.filter(pk=self.ruta.pk))
        vieja.refresh_from_db(); otra.refresh_from_db()
        self.assertEqual(vieja.estado, 'resuelta'); self.assertEqual(otra.estado, 'nueva')

    def test_get_no_depura_estados_ni_escribe(self):
        self.venta.estado_venta='Pagado'; self.venta.saldo_actual=0; self.venta.save()
        riesgo = self.alerta()
        cierre = self.alerta(tipo='CIERRE_AUSENTE', datos={'fecha':'2026-10-09'})
        Cierre_Caja.objects.create(tienda=self.ruta, fecha_cierre=date(2026,10,9), valor=10000)
        before = list(AlertaOperativa.objects.values())
        self.assertEqual(self.pendientes().status_code, 200)
        feed = self.api.get('/tiendas/alertas/', {'estado':'activas'})
        self.assertEqual(feed.data['resumen']['total'], 0)
        self.assertEqual(list(AlertaOperativa.objects.values()), before)
        self.assertFalse(MovimientoCaja.objects.exists())

    def test_cierres_agrupados_por_ruta_no_por_fecha(self):
        for day in (1,2,3):
            self.alerta(tipo='CIERRE_AUSENTE', datos={'fecha':f'2026-10-0{day}'})
        response=self.pendientes(tipo='CIERRE_AUSENTE')
        self.assertEqual(response.data['paginacion']['total'],1)
        self.assertEqual(len(response.data['casos'][0]['fechas_pendientes']),3)

    def test_filtros_y_paginacion_buscan_todo_no_solo_los_200_recientes(self):
        critica=self.alerta(tipo='VENTA_CON_ALERTAS', titulo='Excepción Ántigua importante')
        AlertaOperativa.objects.bulk_create([AlertaOperativa(
            tipo='VENTA_CON_ALERTAS', severidad='media', clave_dedupe=f'bulk:{n}',
            titulo='Revisión ficticia', tienda_id_ref=self.ruta.id) for n in range(205)])
        response=self.pendientes(severidad='critica',q='antigua')
        self.assertEqual(response.data['paginacion']['total'],1)
        self.assertEqual(response.data['casos'][0]['alerta_id'],critica.pk)
        total=self.pendientes(tipo='VENTA_CON_ALERTAS',limit=25)
        self.assertEqual(total.data['paginacion']['total'],206)
        self.assertEqual(len(total.data['casos']),25)
        final=self.pendientes(tipo='VENTA_CON_ALERTAS',limit=25,page=9)
        self.assertEqual(len(final.data['casos']),6)
        self.assertEqual(final.data['resumen']['total'],total.data['resumen']['total'])

    def test_critica_revisada_sigue_contando_como_critica(self):
        self.alerta(tipo='VENTA_CON_ALERTAS', estado='revisada')
        self.assertEqual(self.pendientes(tipo='VENTA_CON_ALERTAS').data['casos'][0]['estado'],'revisada')
        self.assertEqual(self.api.get('/tiendas/alertas/').data['resumen']['criticas'],1)

    def test_trabajador_root_y_otro_admin_no_entran_en_el_centro(self):
        for user in (self.worker,self.root,None):
            self.api.force_authenticate(user)
            self.assertIn(self.pendientes().status_code,(401,403))
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.pendientes(tienda='999999').status_code,403)
        self.assertEqual(self.api.get('/tiendas/alertas/',{'tienda':'incorrecta'}).status_code,403)

    def test_validacion_paginacion_y_filtros(self):
        for params in ({'page':0},{'page':'x'},{'limit':101},{'severidad':'x'},{'estado':'x'}):
            self.assertEqual(self.pendientes(**params).status_code,400,params)

    def test_no_oculta_inconsistencias_que_superan_100_casos(self):
        Venta.objects.bulk_create([Venta(tienda=self.ruta,cliente=self.cliente,
            fecha_venta=date(2026,10,1),valor_venta=1000,interes=20,cuotas=20,
            saldo_actual=500,estado_venta='Vigente') for n in range(105)])
        response=self.pendientes(tipo='INCONSISTENCIA',limit=100)
        self.assertEqual(response.data['paginacion']['total'],105)
        self.assertEqual(len(self.pendientes(tipo='INCONSISTENCIA',page=2,limit=100).data['casos']),5)

    def test_caja_incluye_desembolsos_y_reversas_sin_falsos_faltantes(self):
        saldo=Decimal('1000000')
        for delta in (200000,-50000,-300000,300000,-300000):
            MovimientoCaja.objects.create(tienda=self.ruta,delta=delta,
                saldo_anterior=saldo,saldo_posterior=saldo+delta,tipo='AJUSTE')
            saldo+=delta
        Tienda.objects.filter(pk=self.ruta.pk).update(caja_inicial=saldo)
        casos,sin_historial=controles_caja([self.ruta,self.otra])
        self.assertEqual(casos,[]); self.assertEqual(sin_historial,[self.otra.nombre])

    def test_caja_sin_historial_no_inventa_cuadre(self):
        self.assertEqual(controles_caja([self.ruta]),([],[self.ruta.nombre]))
        self.assertEqual(self.pendientes().data['caja_sin_historial'],[self.ruta.nombre,self.otra.nombre])

    def test_caja_detecta_salto_y_diferencia_sin_cambiar_registros(self):
        MovimientoCaja.objects.create(tienda=self.ruta,delta=100,
            saldo_anterior=9000,saldo_posterior=9100,tipo='APORTE')
        MovimientoCaja.objects.create(tienda=self.ruta,delta=100,
            saldo_anterior=9200,saldo_posterior=9300,tipo='APORTE')
        casos,_=controles_caja([self.ruta])
        self.assertEqual(casos[0]['monto'],700)
        self.assertIn('discontinuidad',casos[0]['detalle'])
        self.ruta.refresh_from_db(); self.assertEqual(self.ruta.caja_inicial,10000)

    def test_revision_guarda_actor_nota_y_evitar_doble_envio(self):
        alerta=self.alerta(tipo='VENTA_CON_ALERTAS')
        path=f'/tiendas/alertas/{alerta.pk}/estado/'
        for _ in range(2):
            response=self.api.patch(path,{'estado':'revisada','nota':'Autorización comprobada'},format='json')
            self.assertEqual(response.status_code,200,response.data)
        alerta.refresh_from_db(); historial=alerta.datos['revisiones']
        self.assertEqual(len(historial),1)
        self.assertEqual(historial[0]['usuario_id'],self.admin.pk)
        self.assertEqual(historial[0]['nota'],'Autorización comprobada')
        response=self.api.patch(path,{'estado':'resuelta','nota':'Crédito autorizado por administración'},format='json')
        self.assertEqual(response.status_code,200)

    def test_no_resuelve_deuda_exigible_por_marcar_revisada(self):
        alerta=self.alerta()
        response=self.api.patch(f'/tiendas/alertas/{alerta.pk}/estado/',
            {'estado':'resuelta','nota':'Ya llamé al cliente'},format='json')
        self.assertEqual(response.status_code,409)
        alerta.refresh_from_db(); self.assertEqual(alerta.estado,'nueva')
        self.venta.refresh_from_db(); self.assertEqual(self.venta.saldo_actual,1100)

    def test_conflicto_otra_sesion_no_sobrescribe(self):
        alerta=self.alerta(tipo='VENTA_CON_ALERTAS')
        response=self.api.patch(f'/tiendas/alertas/{alerta.pk}/estado/',
            {'estado':'revisada','nota':'Revisión ficticia','actualizada':'fecha-antigua'},format='json')
        self.assertEqual(response.status_code,409)
        alerta.refresh_from_db(); self.assertEqual(alerta.estado,'nueva')

    def test_busqueda_del_historial_tolera_acentos_y_busca_cliente_y_ruta(self):
        alerta=self.alerta(tipo='VENTA_CON_ALERTAS',estado='resuelta',titulo='Excepción Ántigua')
        for q in ('antigua','Cliente','Ruta A'):
            response=self.api.get('/tiendas/alertas/',{'estado':'resuelta','q':q})
            self.assertEqual(response.data['resumen']['total'],1)
            self.assertEqual(response.data['alertas'][0]['id'],alerta.pk)

    @override_settings(TELEGRAM_ADMIN_CHAT_ID='chat-ficticio')
    def test_telegram_guarda_revision_y_respeta_alcance(self):
        from Tiendas.telegram_bot import procesar_callback_alerta
        alerta=self.alerta(tipo='VENTA_CON_ALERTAS')
        callback={'data':f'alerta:revisar:{alerta.pk}','id':'callback-ficticio',
            'message':{'chat':{'id':'chat-ficticio'}},'from':{'id':999,'first_name':'Actor ficticio'}}
        with patch('Tiendas.telegram_bot.responder_callback'), patch('Tiendas.telegram_bot._editar_alerta_operativa'):
            self.assertTrue(procesar_callback_alerta(callback))
            self.assertTrue(procesar_callback_alerta(callback))
            alerta.refresh_from_db()
            self.assertEqual(len(alerta.datos['revisiones']),1)
            self.assertEqual(alerta.datos['revisiones'][0]['origen'],'telegram')
            Tienda_Administrador.objects.filter(tienda=self.ruta,administrador=self.admin).delete()
            callback['data']=f'alerta:resolver:{alerta.pk}'
            procesar_callback_alerta(callback)
            alerta.refresh_from_db();self.assertEqual(alerta.estado,'revisada')

    @override_settings(TELEGRAM_ADMIN_CHAT_ID='chat-ficticio')
    def test_telegram_no_resuelve_cobranza_vigente(self):
        from Tiendas.telegram_bot import procesar_callback_alerta
        alerta=self.alerta()
        callback={'data':f'alerta:resolver:{alerta.pk}','id':'callback-ficticio',
            'message':{'chat':{'id':'chat-ficticio'}},'from':{'id':999}}
        with patch('Tiendas.telegram_bot.responder_callback'), patch('Tiendas.telegram_bot._editar_alerta_operativa'):
            procesar_callback_alerta(callback)
        alerta.refresh_from_db();self.assertEqual(alerta.estado,'nueva')

    def test_resolucion_requiere_motivo_y_no_admite_nota_invalida(self):
        alerta=self.alerta(tipo='VENTA_CON_ALERTAS')
        for nota in ('',1,'x'*1001):
            response=self.api.patch(f'/tiendas/alertas/{alerta.pk}/estado/',
                {'estado':'resuelta','nota':nota},format='json')
            self.assertEqual(response.status_code,400)

    def test_query_count_no_crece_por_credito(self):
        self.alerta()
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as before:
            self.pendientes()
        for n in range(20):
            venta=Venta.objects.create(tienda=self.ruta,cliente=self.cliente,
                fecha_venta=date(2026,10,1),valor_venta=1000,interes=20,cuotas=20,saldo_actual=1200)
            self.alerta(venta_id_ref=venta.pk)
        with CaptureQueriesContext(connection) as after:
            self.pendientes()
        self.assertEqual(len(before),len(after))
