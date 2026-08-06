"""Reglas compartidas de frecuencia, cobranza y deterioro de créditos."""

from datetime import timedelta


INTERVALOS_COBRO = {
    'Diario': 1,
    'Semanal': 7,
    'Mensual': 30,
}

# (atención temprana, riesgo alto, riesgo crítico), en días sin abono real.
# Estos tramos ya eran usados por el score de clientes; ahora son la fuente
# única para todas las decisiones de riesgo.
UMBRALES_DSA = {
    'Diario': (3, 7, 14),
    'Semanal': (9, 16, 30),
    'Mensual': (35, 45, 75),
}

DIAS_CANDIDATO_CASTIGO = 90


def normalizar_plazo(plazo):
    return plazo if plazo in INTERVALOS_COBRO else None


def intervalo_cobro(plazo):
    return INTERVALOS_COBRO.get(plazo, INTERVALOS_COBRO['Diario'])


def umbrales_dsa(plazo):
    return UMBRALES_DSA.get(plazo, UMBRALES_DSA['Diario'])


def calcular_fecha_vencimiento(fecha_venta, cuotas, plazo):
    """Calcula el final cronológico respetando la frecuencia del crédito."""
    return fecha_venta + timedelta(
        days=(int(cuotas) * intervalo_cobro(plazo)) + 4,
    )


def calcular_riesgo_venta(*, plazo, estado_venta, dias_sin_abono,
                          dias_atrasados, total_abonado):
    """Devuelve prioridad operativa y deterioro sin consultar la base de datos.

    La prioridad sirve para decidir qué gestionar hoy. El deterioro es una
    señal más lenta de pérdida y usa umbrales relativos a Diario/Semanal/
    Mensual. Un crédito adelantado nunca se marca por el solo paso del tiempo.
    """
    intervalo = intervalo_cobro(plazo)
    atencion, alto, critico = umbrales_dsa(plazo)
    dias = max(0, int(round(float(dias_sin_abono or 0))))
    atraso = float(dias_atrasados or 0)
    abonado = float(total_abonado or 0)
    pagos_adelantados = atraso < 0 and estado_venta != 'Vencido'
    sin_primer_abono = abonado <= 0
    # El día del vencimiento todavía es el ciclo esperado. Se considera
    # incumplido después de superar el intervalo; una venta con abono ayer y
    # cero cuotas atrasadas debe seguir apareciendo al día.
    ciclo_incumplido = dias > intervalo
    # El primer abono vence después de completar el primer ciclo. El día
    # esperado todavía pertenece a la jornada de cobro y no debe alertar.
    primer_abono_vencido = sin_primer_abono and dias > intervalo
    cuotas_atrasadas = max(0, atraso)
    if sin_primer_abono:
        cuotas_atrasadas = max(
            cuotas_atrasadas,
            max(0, (dias - 1) // intervalo),
        )
    en_mora = (
        not pagos_adelantados
        and (
            estado_venta == 'Vencido'
            or cuotas_atrasadas > 0
            or ciclo_incumplido
            or primer_abono_vencido
        )
    )

    nivel_deterioro = 0
    if en_mora:
        if dias >= critico:
            nivel_deterioro = 3
        elif dias >= alto:
            nivel_deterioro = 2
        elif dias >= atencion:
            nivel_deterioro = 1

    if pagos_adelantados:
        nivel_cobranza = 0
        clave_cobranza = 'al_dia'
        motivo = 'Pagos adelantados'
    elif (
        en_mora
        and (
            estado_venta == 'Vencido'
            or cuotas_atrasadas >= 5
            or dias >= critico
        )
    ):
        nivel_cobranza = 3
        clave_cobranza = 'critico'
        motivo = 'Crédito vencido' if estado_venta == 'Vencido' else 'Atraso crítico'
    elif en_mora and (
        cuotas_atrasadas >= 2
        or dias >= max(intervalo * 2, atencion)
    ):
        nivel_cobranza = 2
        clave_cobranza = 'urgente'
        motivo = 'Sin primer abono' if sin_primer_abono else 'Atraso acumulado'
    elif en_mora or (sin_primer_abono and ciclo_incumplido):
        nivel_cobranza = 1
        clave_cobranza = 'hoy'
        motivo = 'Sin primer abono' if sin_primer_abono else 'Ciclo pendiente'
    elif dias > 0:
        nivel_cobranza = 0
        clave_cobranza = 'vigilar'
        motivo = 'Último abono antiguo'
    else:
        nivel_cobranza = 0
        clave_cobranza = 'al_dia'
        motivo = 'Sin atraso registrado'

    if nivel_cobranza == 1:
        umbral_alerta = intervalo
    elif nivel_cobranza == 2:
        umbral_alerta = max(intervalo * 2, atencion)
    elif nivel_cobranza == 3:
        umbral_alerta = critico
    else:
        umbral_alerta = 0

    return {
        'nivel_cobranza': nivel_cobranza,
        'clave_cobranza': clave_cobranza,
        'motivo': motivo,
        'nivel_deterioro': nivel_deterioro,
        'en_mora': en_mora,
        'dias_sin_abono': dias,
        'cuotas_atrasadas': cuotas_atrasadas,
        'intervalo_cobro': intervalo,
        'umbrales_dsa': {
            'atencion': atencion,
            'alto': alto,
            'critico': critico,
        },
        'umbral_alerta': umbral_alerta,
        'candidato_castigo': en_mora and dias >= DIAS_CANDIDATO_CASTIGO,
    }
